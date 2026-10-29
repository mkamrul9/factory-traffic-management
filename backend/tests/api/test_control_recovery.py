import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from time import monotonic, sleep

import pytest
from conftest import NOW, sensor_event
from control_helpers import ControlHarness, FakeClock
from fastapi.testclient import TestClient

from factory_traffic.api.app import create_app
from factory_traffic.api.schemas import SensorEventRequest
from factory_traffic.domain.models import DEFAULT_JUNCTION


@pytest.mark.parametrize(
    "stage",
    [
        "GREEN_CONFIRMED",
        "YELLOW_PENDING",
        "YELLOW_CONFIRMED",
        "ALL_RED_PENDING",
        "ALL_RED_CONFIRMED",
        "GREEN_PENDING",
    ],
)
def test_restart_never_resumes_old_green_or_old_pending_commands(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "traffic.sqlite3"
    clock = FakeClock()
    with TestClient(create_app(path, clock=clock, run_background_tasks=False)) as client:
        control = ControlHarness(client, clock)
        arrival = control.green()
        if stage != "GREEN_CONFIRMED":
            client.post(
                "/api/junctions/A/commands",
                json={"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"},
            )
        if stage in ("YELLOW_CONFIRMED", "ALL_RED_PENDING", "ALL_RED_CONFIRMED", "GREEN_PENDING"):
            control.acknowledge()
        if stage in ("ALL_RED_PENDING", "ALL_RED_CONFIRMED", "GREEN_PENDING"):
            control.tick(5)
        if stage in ("ALL_RED_CONFIRMED", "GREEN_PENDING"):
            control.acknowledge()
        if stage == "GREEN_PENDING":
            control.tick(2)
        client.post("/api/junctions/A/simulator", json={"auto_ack": True})
        old = control.status()
    with TestClient(create_app(path, clock=clock, run_background_tasks=False)) as client:
        control = ControlHarness(client, clock)
        restarted = control.status()
        assert restarted["mode"] == "DEGRADED"
        assert restarted["stage"] == "ALL_RED"
        assert restarted["manual_expires_at"] is None
        assert restarted["simulator_auto_ack"] is False
        assert restarted["queues"]["NORTH"] == 1
        assert restarted["batch_id"] != old["batch_id"]
        assert set(restarted["actual_signals"].values()) == {"UNKNOWN"}
        assert {item["requested_state"] for item in restarted["pending_commands"]} == {"RED"}
        for command in old["pending_commands"]:
            response = client.post(
                "/api/controller-events",
                json={
                    "command_id": command["command_id"],
                    "junction_id": "A",
                    "status": "ACK",
                    "actual_state": command["requested_state"],
                },
            )
            assert response.status_code == 409
            assert response.json()["code"] == "STALE_ACK"
        assert client.post("/api/sensor-events", json=arrival).json()["duplicate"] is True
        control.acknowledge()
        assert control.tick(1.999)["mode"] == "DEGRADED"
        assert control.tick(0.001)["phase"] == "NORTH_SOUTH"
        assert control.acknowledge()["actual_signals"]["NORTH"] == "GREEN"
        with closing(sqlite3.connect(path)) as connection:
            interrupted = connection.execute(
                "SELECT COUNT(*) FROM controller_commands WHERE status = 'INTERRUPTED'"
            ).fetchone()[0]
            assert interrupted == len(old["pending_commands"])


def test_pending_commands_and_failure_state_are_durable(tmp_path: Path) -> None:
    path = tmp_path / "traffic.sqlite3"
    clock = FakeClock()
    with TestClient(create_app(path, clock=clock, run_background_tasks=False)) as client:
        control = ControlHarness(client, clock)
        pending = control.status()["pending_commands"]
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute(
                "SELECT command_id, status FROM controller_commands"
            ).fetchall()
            assert {row[0] for row in rows} == {item["command_id"] for item in pending}
            assert {row[1] for row in rows} == {"PENDING"}
        control.tick(3)
        with closing(sqlite3.connect(path)) as connection:
            runtime = json.loads(
                connection.execute(
                    "SELECT state_json FROM junction_runtime WHERE junction_id = 'A'"
                ).fetchone()[0]
            )
            assert runtime["fault_code"] == "ACK_TIMEOUT"
            assert runtime["recovering"] is False


def test_existing_database_migrates_without_losing_queue_or_receipt(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    migration = (
        Path(__file__).parents[2] / "src/factory_traffic/persistence/migrations/001_initial.sql"
    )
    payload = sensor_event()
    canonical = SensorEventRequest.model_validate(payload).to_domain().canonical_payload()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1, ?)", (NOW.isoformat(),))
        connection.execute(
            "INSERT INTO junctions VALUES (?, ?, ?)",
            ("A", json.dumps(asdict(DEFAULT_JUNCTION)), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO waiting_vehicles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("A", "VH-1", "NORTH", "TRUCK", NOW.isoformat(), NOW.isoformat(), "evt-1", 1),
        )
        connection.execute("INSERT INTO sequence_cursors VALUES ('A', 'NORTH', 1)")
        connection.execute(
            "INSERT INTO sensor_event_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-1",
                "A",
                canonical,
                "ACCEPTED",
                "VEHICLE_ARRIVED",
                "Vehicle added to the queue.",
                NOW.isoformat(),
                200,
            ),
        )
        connection.commit()
    with TestClient(create_app(path, clock=lambda: NOW, run_background_tasks=False)) as client:
        assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1
        assert client.post("/api/sensor-events", json=payload).json()["duplicate"] is True
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]


def test_background_ticker_drives_explicit_simulation_without_requests_sleeping(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    with TestClient(
        create_app(tmp_path / "traffic.sqlite3", clock=clock, tick_interval=0.01)
    ) as client:
        control = ControlHarness(client, clock)
        client.post("/api/junctions/A/simulator", json={"auto_ack": True})
        control.arrival()
        limit = monotonic() + 3
        while control.status()["awaiting_confirmation"] and monotonic() < limit:
            sleep(0.01)
        assert control.status()["awaiting_confirmation"] is False
        clock.advance(2)
        limit = monotonic() + 3
        while control.status()["actual_signals"]["NORTH"] != "GREEN" and monotonic() < limit:
            sleep(0.01)
        assert control.status()["actual_signals"]["NORTH"] == "GREEN"
