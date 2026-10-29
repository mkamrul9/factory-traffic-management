from pathlib import Path

import pytest
from conftest import NOW, sensor_event
from fastapi.testclient import TestClient

from factory_traffic.api.app import create_app


def test_startup_seeds_junction_a_without_assuming_physical_state(client: TestClient) -> None:
    junctions = client.get("/api/junctions").json()
    assert [junction["junction_id"] for junction in junctions] == ["A"]
    assert junctions[0]["timings"]["yellow_seconds"] == 5
    status = client.get("/api/junctions/A/status").json()
    assert status["queues"] == {"NORTH": 0, "SOUTH": 0, "EAST": 0, "WEST": 0}
    assert status["mode"] == "DEGRADED"
    assert status["control_active"] is False
    assert set(status["actual_signals"].values()) == {"UNKNOWN"}
    assert set(status["desired_signals"].values()) == {"RED"}
    assert status["controller_status"] == "UNKNOWN"


def test_junction_configuration_and_history_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "traffic.sqlite3"
    with TestClient(create_app(path, clock=lambda: NOW, run_background_tasks=False)) as first:
        result = first.post(
            "/api/junctions",
            json={"junction_id": "B", "name": "Loading bay", "timings": {"green_seconds": 20}},
        )
        assert result.status_code == 201
        first_created = first.get("/api/junctions/A").json()["created_at"]
    with TestClient(create_app(path, clock=lambda: NOW, run_background_tasks=False)) as second:
        record = second.get("/api/junctions/B").json()
        assert record["name"] == "Loading bay"
        assert record["timings"]["green_seconds"] == 20
        assert len(second.get("/api/junctions").json()) == 2
        assert second.get("/api/junctions/A").json()["created_at"] == first_created
        entries = second.get("/api/junctions/A/history").json()["items"]
        assert sum(entry["event_type"] == "JUNCTION_CREATED" for entry in entries) == 1


def test_duplicate_junction_cannot_replace_existing_configuration(client: TestClient) -> None:
    response = client.post("/api/junctions", json={"junction_id": "A", "name": "Replacement"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JUNCTION_ALREADY_EXISTS"
    assert client.get("/api/junctions/A").json()["name"] == "Junction A"


@pytest.mark.parametrize("suffix", ["", "/status", "/history"])
def test_unknown_junction_returns_not_found(client: TestClient, suffix: str) -> None:
    response = client.get(f"/api/junctions/missing{suffix}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_JUNCTION"


@pytest.mark.parametrize(
    "body",
    [
        {"junction_id": "B", "name": " "},
        {"junction_id": "B", "name": "Bay", "timings": {"yellow_seconds": 0}},
        {"junction_id": "B", "name": "Bay", "timings": {"green_seconds": True}},
        {"junction_id": "B", "name": "Bay", "desired_signals": {"NORTH": "GREEN"}},
        {"junction_id": "../B", "name": "Bay"},
    ],
)
def test_invalid_configuration_cannot_create_a_junction(
    client: TestClient, body: dict[str, object]
) -> None:
    assert client.post("/api/junctions", json=body).status_code == 422
    assert len(client.get("/api/junctions").json()) == 1


def test_history_pagination_remains_stable_when_new_events_arrive(client: TestClient) -> None:
    for index in range(1, 4):
        assert (
            client.post(
                "/api/sensor-events",
                json=sensor_event(
                    event_id=f"evt-{index}", vehicle_id=f"VH-{index}", sequence_no=index
                ),
            ).status_code
            == 200
        )
    original_ids = [
        entry["id"] for entry in client.get("/api/junctions/A/history?limit=100").json()["items"]
    ]
    first_page = client.get("/api/junctions/A/history?limit=2").json()
    cursor = first_page["next_before_id"]
    assert cursor is not None
    client.post(
        "/api/sensor-events", json=sensor_event(event_id="evt-4", vehicle_id="VH-4", sequence_no=4)
    )
    ids = [entry["id"] for entry in first_page["items"]]
    while cursor is not None:
        page = client.get(f"/api/junctions/A/history?limit=2&before_id={cursor}").json()
        ids.extend(entry["id"] for entry in page["items"])
        cursor = page["next_before_id"]
    assert ids == sorted(set(ids), reverse=True)
    assert ids == original_ids
    client.post("/api/junctions", json={"junction_id": "B", "name": "Bay"})
    assert all(
        entry["junction_id"] == "B"
        for entry in client.get("/api/junctions/B/history").json()["items"]
    )


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "before_id=0", "before_id=abc"])
def test_history_rejects_invalid_pagination(client: TestClient, query: str) -> None:
    assert client.get(f"/api/junctions/A/history?{query}").status_code == 422
