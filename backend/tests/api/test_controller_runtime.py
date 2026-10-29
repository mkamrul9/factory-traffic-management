import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from control_helpers import ControlHarness

from factory_traffic.domain.controller import ControllerCommand
from factory_traffic.persistence.sqlite import SQLiteTransaction


def test_outbox_has_unique_ids_and_startup_needs_all_four_confirmations(
    control: ControlHarness,
) -> None:
    initial = control.status()
    commands = initial["pending_commands"]
    assert len(commands) == len({command["command_id"] for command in commands}) == 4
    assert {command["requested_state"] for command in commands} == {"RED"}
    assert {command["batch_id"] for command in commands} == {initial["batch_id"]}
    assert initial["mode"] == "DEGRADED"
    control.arrival()
    first = commands[0]
    response = control.client.post(
        "/api/controller-events",
        json={
            "command_id": first["command_id"],
            "junction_id": "A",
            "status": "ACK",
            "actual_state": "RED",
        },
    )
    assert response.status_code == 200
    partial = control.status()
    assert partial["awaiting_confirmation"]
    assert partial["next_transition_at"] is None
    assert len(partial["pending_commands"]) == 3
    assert list(partial["actual_signals"].values()).count("RED") == 1
    control.clock.advance(2.9)
    confirmed = control.acknowledge()
    assert set(confirmed["actual_signals"].values()) == {"RED"}
    assert control.tick(1.999)["mode"] == "DEGRADED"
    green = control.tick(0.001)
    assert green["stage"] == "GREEN"
    assert green["phase"] == "NORTH_SOUTH"
    assert set(green["actual_signals"].values()) == {"UNKNOWN"}
    assert control.acknowledge()["actual_signals"]["NORTH"] == "GREEN"


def test_late_acknowledgement_cannot_use_its_sensor_timestamp_to_avoid_timeout(
    control: ControlHarness,
) -> None:
    command = control.status()["pending_commands"][0]
    control.clock.advance(3)
    response = control.client.post(
        "/api/controller-events",
        json={
            "command_id": command["command_id"],
            "junction_id": "A",
            "status": "ACK",
            "actual_state": "RED",
            "timestamp": command["requested_at"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_ACK"
    status = control.status()
    assert status["mode"] == "DEGRADED"
    assert "ACK_TIMEOUT" in [alert["code"] for alert in status["alerts"]]
    assert status["batch_id"] != command["batch_id"]
    assert {item["requested_state"] for item in status["pending_commands"]} == {"RED"}
    # A second timeout does not create an endless stream of new commands.
    batch_id = status["batch_id"]
    assert control.tick(3)["pending_commands"] == []
    assert control.tick(100)["batch_id"] == batch_id


@pytest.mark.parametrize(
    ("status", "actual", "code"),
    [("ACK", "GREEN", "ACK_MISMATCH"), ("FAILED", None, "COMMAND_FAILED")],
)
def test_failed_or_mismatched_ack_latches_failure(
    control: ControlHarness, status: str, actual: str | None, code: str
) -> None:
    command = control.status()["pending_commands"][0]
    response = control.client.post(
        "/api/controller-events",
        json={
            "command_id": command["command_id"],
            "junction_id": "A",
            "status": status,
            "actual_state": actual,
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == code
    assert set(control.status()["actual_signals"].values()) == {"UNKNOWN"}
    control.acknowledge()
    assert control.tick(10)["mode"] == "DEGRADED"
    assert control.status()["pending_commands"] == []
    recovery = control.client.post(
        "/api/junctions/A/commands", json={"command": "RECOVER_CONTROLLER"}
    )
    assert recovery.status_code == 200
    assert control.ready()["mode"] == "AUTOMATIC"


def test_duplicate_old_ack_cannot_overwrite_a_newer_physical_observation(
    control: ControlHarness,
) -> None:
    old_command = control.status()["pending_commands"][0]
    control.green()
    before = control.status()["actual_signals"]
    response = control.client.post(
        "/api/controller-events",
        json={
            "command_id": old_command["command_id"],
            "junction_id": "A",
            "status": "ACK",
            "actual_state": "RED",
        },
    )
    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert control.status()["actual_signals"] == before


def test_unknown_command_and_wrong_junction_do_not_change_state(control: ControlHarness) -> None:
    before = control.status()
    for command_id in ("missing", before["pending_commands"][0]["command_id"]):
        control.client.post("/api/junctions", json={"junction_id": "B", "name": "Bay"})
        response = control.client.post(
            "/api/controller-events",
            json={
                "command_id": command_id,
                "junction_id": "B",
                "status": "ACK",
                "actual_state": "RED",
            },
        )
        assert response.status_code == 404
    assert control.status()["actual_signals"] == before["actual_signals"]


def test_normal_scheduling_and_manual_controls_run_through_the_api(control: ControlHarness) -> None:
    control.green("EMPLOYEE_VEHICLE")
    control.arrival("EAST", "TRUCK")
    assert control.tick(29.999)["stage"] == "GREEN"
    assert control.tick(0.001)["stage"] == "YELLOW"
    east = control.complete_switch()
    assert east["actual_signals"]["EAST"] == "GREEN"
    assert east["actual_signals"]["NORTH"] == "RED"
    response = control.client.post(
        "/api/junctions/A/commands", json={"command": "MANUAL_GREEN_REQUEST", "direction": "SOUTH"}
    )
    assert response.status_code == 200
    assert control.status()["stage"] == "YELLOW"
    north = control.complete_switch()
    assert north["mode"] == "MANUAL"
    assert north["actual_signals"]["NORTH"] == "GREEN"
    assert north["queues"]["NORTH"] == 1  # GREEN does not invent vehicle clearances.
    assert (
        control.client.post(
            "/api/junctions/A/commands", json={"command": "RETURN_TO_AUTOMATIC"}
        ).status_code
        == 200
    )
    assert control.status()["mode"] == "AUTOMATIC"


def test_emergency_preempts_manual_and_duplicate_detection_does_not_repeat_commands(
    control: ControlHarness,
) -> None:
    control.green()
    control.client.post(
        "/api/junctions/A/commands", json={"command": "MANUAL_GREEN_REQUEST", "direction": "NORTH"}
    )
    emergency = control.arrival("EAST", "EMERGENCY")
    before = control.status()
    assert before["mode"] == "EMERGENCY"
    assert before["stage"] == "YELLOW"
    assert before["manual_expires_at"] is None
    assert control.client.post("/api/sensor-events", json=emergency).json()["duplicate"] is True
    assert control.status()["batch_id"] == before["batch_id"]
    response = control.client.post(
        "/api/junctions/A/commands", json={"command": "MANUAL_GREEN_REQUEST", "direction": "NORTH"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "EMERGENCY_ACTIVE"
    assert control.complete_switch()["actual_signals"]["EAST"] == "GREEN"
    clearance = {
        **emergency,
        "event_id": "clear-emergency",
        "event_type": "VEHICLE_CLEARED",
        "sequence_no": emergency["sequence_no"] + 1,
    }
    assert control.client.post("/api/sensor-events", json=clearance).status_code == 200
    assert control.status()["mode"] == "AUTOMATIC"


def test_manual_lease_expires_in_the_running_application(control: ControlHarness) -> None:
    control.green()
    control.client.post(
        "/api/junctions/A/commands", json={"command": "MANUAL_GREEN_REQUEST", "direction": "NORTH"}
    )
    assert control.tick(120)["mode"] == "AUTOMATIC"
    assert control.status()["manual_expires_at"] is None


def test_recovery_command_cannot_bypass_transitions_on_a_healthy_junction(
    control: ControlHarness,
) -> None:
    control.green()
    before = control.status()
    response = control.client.post(
        "/api/junctions/A/commands", json={"command": "RECOVER_CONTROLLER"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RECOVERY_NOT_REQUIRED"
    assert control.status()["batch_id"] == before["batch_id"]
    assert control.status()["actual_signals"] == before["actual_signals"]


def test_fault_mode_rejects_operator_override(control: ControlHarness) -> None:
    for command in (
        {"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"},
        {"command": "RETURN_TO_AUTOMATIC"},
    ):
        response = control.client.post("/api/junctions/A/commands", json=command)
        assert response.status_code == 409
        assert response.json()["code"] == "CONTROL_UNAVAILABLE"


def test_simulator_is_opt_in_and_never_treats_dispatch_as_confirmation(
    control: ControlHarness, tmp_path: Path
) -> None:
    assert control.status()["simulator_auto_ack"] is False
    status = control.status()

    class DeferredController:
        def execute(self, commands, now):
            with closing(sqlite3.connect(tmp_path / "traffic.sqlite3")) as connection:
                for command in commands:
                    assert connection.execute(
                        "SELECT status FROM controller_commands WHERE command_id = ?",
                        (command.command_id,),
                    ).fetchone() == ("PENDING",)
            return ()

    control.client.app.state.traffic_service.controller = DeferredController()
    response = control.client.post(
        "/api/junctions/A/simulator/acknowledge", json={"batch_id": status["batch_id"]}
    )
    assert response.status_code == 200
    assert response.json()["code"] == "COMMANDS_DISPATCHED"
    assert control.status()["awaiting_confirmation"] is True
    assert set(control.status()["actual_signals"].values()) == {"UNKNOWN"}


def test_enabling_simulator_recovers_and_acknowledges_each_committed_request(
    control: ControlHarness,
) -> None:
    control.tick(10)
    response = control.client.post("/api/junctions/A/simulator", json={"auto_ack": True})
    assert response.status_code == 200
    control.tick()
    assert control.status()["awaiting_confirmation"] is False
    control.arrival()
    status = control.tick(2)
    assert status["mode"] == "AUTOMATIC"
    assert status["actual_signals"]["NORTH"] == "GREEN"
    control.client.post("/api/junctions/A/simulator", json={"auto_ack": False})
    control.client.post(
        "/api/junctions/A/commands", json={"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"}
    )
    assert control.tick(3)["mode"] == "DEGRADED"


def test_rollback_does_not_publish_or_preserve_a_partially_written_green_request(
    control: ControlHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    control.ready()
    before = control.status()
    original = SQLiteTransaction.save_command

    def fail_green(self: SQLiteTransaction, command: ControllerCommand) -> None:
        if command.requested_state == "GREEN":
            raise sqlite3.OperationalError("simulated outbox failure")
        original(self, command)

    from conftest import sensor_event

    payload = sensor_event(timestamp=control.clock().isoformat())
    with monkeypatch.context() as patch:
        patch.setattr(SQLiteTransaction, "save_command", fail_green)
        response = control.client.post("/api/sensor-events", json=payload)
    assert response.status_code == 503
    after = control.status()
    assert after["queues"]["NORTH"] == 0
    assert after["batch_id"] == before["batch_id"]
    assert after["actual_signals"] == before["actual_signals"]
    retry = control.client.post("/api/sensor-events", json=payload)
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is False


def test_concurrent_sensor_and_operator_intents_preserve_emergency_priority(
    control: ControlHarness,
) -> None:
    control.green()
    from conftest import sensor_event

    emergency = sensor_event(
        event_id="emergency",
        vehicle_id="EM-1",
        direction="EAST",
        vehicle_type="EMERGENCY",
        sequence_no=0,
        timestamp=control.clock().isoformat(),
    )

    def submit(index: int) -> int:
        if index == 1:
            return control.client.post(
                "/api/junctions/A/commands",
                json={"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"},
            ).status_code
        return control.client.post("/api/sensor-events", json=emergency).status_code

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(submit, range(3)))
    assert set(results).issubset({200, 409})
    status = control.status()
    assert status["mode"] == "EMERGENCY"
    assert status["queues"]["EAST"] == 1
    assert status["stage"] == "YELLOW"
    assert len(status["pending_commands"]) == 4
    assert set(status["desired_signals"].values()) <= {"YELLOW", "RED"}


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "SET_GREEN", "direction": "NORTH"},
        {"command": "MANUAL_GREEN_REQUEST"},
        {"command": "RETURN_TO_AUTOMATIC", "direction": "NORTH"},
        {"command": "RECOVER_CONTROLLER", "direction": "WEST"},
        {"command": "MANUAL_GREEN_REQUEST", "direction": "NORTH", "actual_state": "GREEN"},
    ],
)
def test_invalid_operator_commands_are_rejected(control: ControlHarness, payload: dict) -> None:
    assert control.client.post("/api/junctions/A/commands", json=payload).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "MAYBE"},
        {"actual_state": None},
        {"actual_state": "UNKNOWN"},
        {"timestamp": 1234},
        {"direction": "NORTH"},
    ],
)
def test_invalid_controller_messages_are_rejected(control: ControlHarness, changes: dict) -> None:
    command = control.status()["pending_commands"][0]
    payload = {
        "junction_id": "A",
        "command_id": command["command_id"],
        "status": "ACK",
        "actual_state": "RED",
        **changes,
    }
    assert control.client.post("/api/controller-events", json=payload).status_code == 422
