import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from conftest import NOW, sensor_event
from fastapi.testclient import TestClient

from factory_traffic.api.app import create_app
from factory_traffic.application.ports import AuditEntry
from factory_traffic.persistence.sqlite import SQLiteTransaction


def test_parallel_duplicate_submissions_across_service_instances_apply_once(tmp_path: Path) -> None:
    path = tmp_path / "traffic.sqlite3"
    with (
        TestClient(create_app(path, clock=lambda: NOW)) as first,
        TestClient(create_app(path, clock=lambda: NOW)) as second,
    ):
        clients = (first, second)

        def submit(index: int) -> dict[str, object]:
            response = clients[index % 2].post("/api/sensor-events", json=sensor_event())
            assert response.status_code == 200
            return response.json()

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(submit, range(24)))
        assert sum(result["duplicate"] is False for result in results) == 1
        assert first.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1
        entries = first.get("/api/junctions/A/history").json()["items"]
        assert sum(entry["event_type"] == "DUPLICATE_SENSOR_EVENT" for entry in entries) == 23


def test_event_id_uniqueness_is_global_even_during_concurrent_junction_requests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "traffic.sqlite3"
    with (
        TestClient(create_app(path, clock=lambda: NOW)) as first,
        TestClient(create_app(path, clock=lambda: NOW)) as second,
    ):
        first.post("/api/junctions", json={"junction_id": "B", "name": "Bay"})

        def submit(junction_id: str) -> int:
            client = first if junction_id == "A" else second
            return client.post(
                "/api/sensor-events", json=sensor_event(junction_id=junction_id)
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, ["A", "B"]))
        assert sorted(statuses) == [200, 409]
        queue_total = sum(
            first.get(f"/api/junctions/{junction_id}/status").json()["queues"]["NORTH"]
            for junction_id in ("A", "B")
        )
        assert queue_total == 1


def test_concurrent_arrivals_have_no_lost_accepted_updates(client: TestClient) -> None:
    directions = ("NORTH", "SOUTH", "EAST", "WEST")

    def submit(index: int) -> tuple[str, str]:
        direction = directions[index % 4]
        response = client.post(
            "/api/sensor-events",
            json=sensor_event(
                event_id=f"evt-{index}",
                vehicle_id=f"VH-{index}",
                direction=direction,
                sequence_no=index // 4,
            ),
        )
        assert response.status_code == 200
        return direction, response.json()["outcome"]

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(submit, range(24)))
    status = client.get("/api/junctions/A/status").json()
    for direction in directions:
        accepted = sum(d == direction and outcome == "ACCEPTED" for d, outcome in results)
        assert status["queues"][direction] == accepted
        assert accepted >= 1
    assert sum(status["queues"].values()) == len(status["queued_vehicles"])
    entries = client.get("/api/junctions/A/history").json()["items"]
    assert len(entries) == 25


def test_concurrent_clearances_never_make_a_queue_negative(client: TestClient) -> None:
    client.post("/api/sensor-events", json=sensor_event())

    def clear(index: int) -> str:
        response = client.post(
            "/api/sensor-events",
            json=sensor_event(
                event_id=f"clear-{index}", event_type="VEHICLE_CLEARED", sequence_no=index + 2
            ),
        )
        assert response.status_code in (200, 409)
        return response.json()["outcome"]

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(clear, range(24)))
    assert outcomes.count("ACCEPTED") == 1
    assert set(client.get("/api/junctions/A/status").json()["queues"].values()) == {0}


def test_database_failure_rolls_back_queue_receipt_cursor_and_history(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_audit(self: SQLiteTransaction, entry: AuditEntry) -> None:
        raise sqlite3.OperationalError("simulated database failure")

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteTransaction, "add_audit", fail_audit)
        response = client.post("/api/sensor-events", json=sensor_event())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    with closing(sqlite3.connect(tmp_path / "traffic.sqlite3")) as connection:
        for table in ("waiting_vehicles", "sensor_event_receipts", "sequence_cursors"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_history").fetchone()[0] == 1
    retry = client.post("/api/sensor-events", json=sensor_event())
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1


def test_malformed_json_is_audited_without_changing_queues(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/sensor-events", content="{broken", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    with closing(sqlite3.connect(tmp_path / "traffic.sqlite3")) as connection:
        row = connection.execute(
            "SELECT junction_id, event_type FROM audit_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == (None, "SENSOR_EVENT_REJECTED")


def test_vehicle_ids_can_be_reused_at_different_junctions(client: TestClient) -> None:
    client.post("/api/junctions", json={"junction_id": "B", "name": "Bay"})
    for junction_id in ("A", "B"):
        response = client.post(
            "/api/sensor-events", json=sensor_event(event_id=junction_id, junction_id=junction_id)
        )
        assert response.json()["outcome"] == "ACCEPTED"


def test_simultaneous_junction_creation_returns_one_conflict(client: TestClient) -> None:
    def create(_: int) -> int:
        return client.post("/api/junctions", json={"junction_id": "B", "name": "Bay"}).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create, range(2))) == [201, 409]
