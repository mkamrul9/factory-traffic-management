from datetime import timedelta

import pytest
from control_helpers import ControlHarness


@pytest.mark.parametrize(
    ("device_type", "direction"),
    [("SENSOR", "NORTH"), ("SIGNAL", "EAST"), ("SIGNAL_CONTROLLER", None)],
)
def test_offline_device_stops_control_and_reconnect_requires_fresh_red(
    control: ControlHarness, device_type: str, direction: str | None
) -> None:
    control.green()
    previous = control.status()
    response = control.device(device_type, "OFFLINE", direction)
    assert response.status_code == 200
    failed = control.status()
    assert failed["mode"] == "DEGRADED"
    assert failed["control_active"] is False
    assert set(failed["desired_signals"].values()) == {"RED"}
    assert set(failed["actual_signals"].values()) == {"UNKNOWN"}
    assert failed["queues"] == previous["queues"]
    assert (
        control.client.post(
            "/api/junctions/A/commands", json={"command": "RECOVER_CONTROLLER"}
        ).status_code
        == 409
    )
    assert (
        control.client.post("/api/junctions/A/simulator", json={"auto_ack": True}).status_code
        == 409
    )
    control.clock.advance(0.1)
    response = control.device(device_type, "ONLINE", direction)
    assert response.status_code == 200
    reconnect = control.status()
    assert reconnect["mode"] == "DEGRADED"
    assert reconnect["batch_id"] != failed["batch_id"]
    control.acknowledge()
    assert control.tick(1.999)["mode"] == "DEGRADED"
    assert control.tick(0.001)["phase"] == "NORTH_SOUTH"
    assert control.acknowledge()["actual_signals"]["NORTH"] == "GREEN"


@pytest.mark.parametrize("device_type", ["SENSOR", "SIGNAL", "SIGNAL_CONTROLLER"])
def test_unknown_device_state_is_a_failure_not_an_online_assumption(
    control: ControlHarness, device_type: str
) -> None:
    control.green()
    response = control.device(device_type, "UNKNOWN", "SOUTH")
    assert response.status_code == 200
    assert control.status()["mode"] == "DEGRADED"
    assert set(control.status()["desired_signals"].values()) == {"RED"}


def test_controller_offline_cannot_acknowledge_or_be_silently_enabled(
    control: ControlHarness,
) -> None:
    control.green()
    control.device("SIGNAL_CONTROLLER", "OFFLINE")
    status = control.status()
    command = status["pending_commands"][0]
    response = control.client.post(
        "/api/controller-events",
        json={
            "junction_id": "A",
            "command_id": command["command_id"],
            "status": "ACK",
            "actual_state": "RED",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "DEVICE_UNAVAILABLE"
    assert (
        control.client.post(
            "/api/junctions/A/simulator/acknowledge", json={"batch_id": status["batch_id"]}
        ).status_code
        == 409
    )
    assert control.status()["controller_status"] == "OFFLINE"


def test_device_event_deduplication_and_payload_conflicts(control: ControlHarness) -> None:
    control.green()
    payload = {
        "event_id": "status-1",
        "junction_id": "A",
        "device_type": "SENSOR",
        "direction": "NORTH",
        "status": "OFFLINE",
        "timestamp": control.clock().isoformat(),
    }
    assert control.client.post("/api/device-events", json=payload).status_code == 200
    batch_id = control.status()["batch_id"]
    duplicate = control.client.post("/api/device-events", json=payload)
    assert duplicate.json()["duplicate"] is True
    assert control.status()["batch_id"] == batch_id
    conflict = control.client.post("/api/device-events", json={**payload, "status": "ONLINE"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "EVENT_ID_CONFLICT"
    assert control.status()["sensor_statuses"]["NORTH"] == "OFFLINE"


def test_old_online_report_cannot_override_a_newer_offline_report(control: ControlHarness) -> None:
    control.green()
    control.device("SIGNAL_CONTROLLER", "OFFLINE")
    old = (control.clock() - timedelta(seconds=1)).isoformat()
    response = control.device("SIGNAL_CONTROLLER", "ONLINE", timestamp=old)
    assert response.json()["outcome"] == "IGNORED"
    assert response.json()["code"] == "STALE_DEVICE_EVENT"
    assert control.status()["controller_status"] == "OFFLINE"


def test_online_heartbeat_does_not_repeatedly_restart_clearance(control: ControlHarness) -> None:
    control.green()
    before = control.status()
    control.device("SIGNAL_CONTROLLER", "ONLINE")
    after = control.status()
    assert after["batch_id"] == before["batch_id"]
    assert after["actual_signals"] == before["actual_signals"]


def test_future_device_timestamp_is_rejected_without_changing_health(
    control: ControlHarness,
) -> None:
    control.green()
    future = (control.clock() + timedelta(seconds=31)).isoformat()
    response = control.device("SIGNAL_CONTROLLER", "OFFLINE", timestamp=future)
    assert response.status_code == 409
    assert response.json()["code"] == "FUTURE_TIMESTAMP"
    assert control.status()["controller_status"] == "ONLINE"


def test_sensor_and_device_event_ids_share_one_idempotency_namespace(
    control: ControlHarness,
) -> None:
    control.ready()
    arrival = control.arrival()
    response = control.device("SENSOR", "OFFLINE", "NORTH", event_id=arrival["event_id"])
    assert response.status_code == 409
    assert response.json()["code"] == "EVENT_ID_CONFLICT"
    assert control.status()["sensor_statuses"]["NORTH"] == "ONLINE"


def test_sensor_events_remain_recorded_while_device_is_offline(control: ControlHarness) -> None:
    control.ready()
    control.device("SENSOR", "OFFLINE", "NORTH")
    control.arrival()
    status = control.status()
    assert status["queues"]["NORTH"] == 1
    assert status["sensor_statuses"]["NORTH"] == "OFFLINE"
    assert status["mode"] == "DEGRADED"


@pytest.mark.parametrize(
    "changes",
    [
        {"device_type": "CAMERA"},
        {"device_type": "SENSOR", "direction": None},
        {"device_type": "SIGNAL", "direction": None},
        {"status": "BROKEN"},
        {"timestamp": "not-a-time"},
    ],
)
def test_invalid_device_reports_do_not_change_state(control: ControlHarness, changes: dict) -> None:
    payload = {
        "event_id": "invalid",
        "junction_id": "A",
        "device_type": "SIGNAL_CONTROLLER",
        "status": "OFFLINE",
        "timestamp": control.clock().isoformat(),
        **changes,
    }
    assert control.client.post("/api/device-events", json=payload).status_code == 422


def test_sensor_failure_stays_latched_even_after_red_is_confirmed(control: ControlHarness) -> None:
    control.green()
    control.client.post("/api/junctions/A/simulator", json={"auto_ack": True})
    control.device("SENSOR", "OFFLINE", "WEST")
    control.tick()
    status = control.tick(20)
    assert status["mode"] == "DEGRADED"
    assert set(status["actual_signals"].values()) == {"RED"}
    assert status["pending_commands"] == []


def test_additional_faults_preserve_best_effort_red_commands(control: ControlHarness) -> None:
    control.green()
    control.device("SENSOR", "OFFLINE", "NORTH")
    red = control.status()
    command_ids = {command["command_id"] for command in red["pending_commands"]}
    control.device("SIGNAL_CONTROLLER", "OFFLINE")
    second_fault = control.status()
    assert {command["command_id"] for command in second_fault["pending_commands"]} == command_ids
    control.clock.advance(0.1)
    control.device("SIGNAL_CONTROLLER", "ONLINE")
    assert "SENSOR_UNAVAILABLE" in [alert["code"] for alert in control.status()["alerts"]]
    control.acknowledge()
    assert set(control.status()["actual_signals"].values()) == {"RED"}
    assert control.tick(10)["mode"] == "DEGRADED"
