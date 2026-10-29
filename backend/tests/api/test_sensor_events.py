from datetime import timedelta, timezone
from pathlib import Path

import pytest
from conftest import NOW, sensor_event
from fastapi.testclient import TestClient

from factory_traffic.api.app import create_app


def test_arrival_clearance_and_retries_change_the_queue_once(client: TestClient) -> None:
    arrival = sensor_event()
    first = client.post("/api/sensor-events", json=arrival)
    assert first.status_code == 200
    assert first.json()["outcome"] == "ACCEPTED"
    assert first.json()["duplicate"] is False
    duplicate = client.post("/api/sensor-events", json=arrival)
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["received_at"] == first.json()["received_at"]
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1

    clearance = sensor_event(
        event_id="clear-1", event_type="VEHICLE_CLEARED", vehicle_type=None, sequence_no=2
    )
    assert client.post("/api/sensor-events", json=clearance).json()["outcome"] == "ACCEPTED"
    assert client.post("/api/sensor-events", json=clearance).json()["duplicate"] is True
    status = client.get("/api/junctions/A/status").json()
    assert status["queues"]["NORTH"] == 0
    assert status["queued_vehicles"] == []
    kinds = [
        entry["event_type"] for entry in client.get("/api/junctions/A/history").json()["items"]
    ]
    assert kinds.count("VEHICLE_ARRIVED") == 1
    assert kinds.count("VEHICLE_CLEARED") == 1
    assert kinds.count("DUPLICATE_SENSOR_EVENT") == 2


def test_reusing_event_id_with_changed_content_is_a_conflict(client: TestClient) -> None:
    client.post("/api/sensor-events", json=sensor_event())
    response = client.post("/api/sensor-events", json=sensor_event(vehicle_id="OTHER"))
    assert response.status_code == 409
    assert response.json()["code"] == "EVENT_ID_CONFLICT"
    status = client.get("/api/junctions/A/status").json()
    assert [vehicle["vehicle_id"] for vehicle in status["queued_vehicles"]] == ["VH-1"]
    # A conflict must not overwrite the original receipt.
    assert client.post("/api/sensor-events", json=sensor_event()).json()["duplicate"] is True


def test_equivalent_utc_timestamp_is_a_logical_duplicate(client: TestClient) -> None:
    client.post("/api/sensor-events", json=sensor_event())
    local_time = NOW.astimezone(timezone(timedelta(hours=6))).isoformat()
    response = client.post("/api/sensor-events", json=sensor_event(timestamp=local_time))
    assert response.json()["duplicate"] is True


def test_another_event_id_cannot_add_a_waiting_vehicle_twice(client: TestClient) -> None:
    client.post("/api/sensor-events", json=sensor_event())
    response = client.post("/api/sensor-events", json=sensor_event(event_id="new", sequence_no=2))
    assert response.status_code == 409
    assert response.json()["code"] == "VEHICLE_ALREADY_QUEUED"
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"direction": "EAST"}, "DIRECTION_MISMATCH"),
        ({"vehicle_type": "FORKLIFT"}, "VEHICLE_TYPE_MISMATCH"),
        ({"vehicle_id": "UNKNOWN"}, "VEHICLE_NOT_QUEUED"),
    ],
)
def test_invalid_clearance_keeps_the_waiting_vehicle(
    client: TestClient, changes: dict[str, object], code: str
) -> None:
    client.post("/api/sensor-events", json=sensor_event())
    payload = sensor_event(event_id="clear", sequence_no=2, event_type="VEHICLE_CLEARED")
    payload.update(changes)
    response = client.post("/api/sensor-events", json=payload)
    assert response.status_code == 409
    assert response.json()["code"] == code
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 1


def test_clearance_before_arrival_cannot_resurrect_an_old_detection(client: TestClient) -> None:
    clearance = sensor_event(event_id="clear", event_type="VEHICLE_CLEARED", sequence_no=20)
    assert client.post("/api/sensor-events", json=clearance).status_code == 409
    response = client.post("/api/sensor-events", json=sensor_event(sequence_no=19))
    assert response.json()["outcome"] == "IGNORED"
    assert response.json()["code"] == "STALE_SEQUENCE"
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 0


def test_sequence_numbers_are_scoped_to_each_direction(client: TestClient) -> None:
    for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
        response = client.post(
            "/api/sensor-events",
            json=sensor_event(event_id=direction, vehicle_id=direction, direction=direction),
        )
        assert response.json()["outcome"] == "ACCEPTED"
    assert set(client.get("/api/junctions/A/status").json()["queues"].values()) == {1}


@pytest.mark.parametrize(
    ("vehicle_type", "age", "expected"),
    [
        ("TRUCK", 86400, "VEHICLE_ARRIVED"),
        ("TRUCK", -30, "VEHICLE_ARRIVED"),
        ("TRUCK", -31, "FUTURE_TIMESTAMP"),
        ("EMERGENCY", 120, "VEHICLE_ARRIVED"),
        ("EMERGENCY", 121, "STALE_EMERGENCY"),
    ],
)
def test_timestamp_freshness_policy(
    client: TestClient, vehicle_type: str, age: int, expected: str
) -> None:
    source_time = (NOW - timedelta(seconds=age)).isoformat()
    response = client.post(
        "/api/sensor-events", json=sensor_event(vehicle_type=vehicle_type, timestamp=source_time)
    )
    assert response.json()["code"] == expected
    if expected == "VEHICLE_ARRIVED":
        vehicle = client.get("/api/junctions/A/status").json()["queued_vehicles"][0]
        assert vehicle["arrived_at"] == NOW.isoformat().replace("+00:00", "Z")
        assert vehicle["sensor_timestamp"] == source_time.replace("+00:00", "Z")


def test_bad_timestamp_does_not_poison_the_sequence_stream(client: TestClient) -> None:
    future = (NOW + timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/sensor-events", json=sensor_event(timestamp=future, sequence_no=999)
    )
    assert response.json()["code"] == "FUTURE_TIMESTAMP"
    response = client.post("/api/sensor-events", json=sensor_event(event_id="corrected"))
    assert response.json()["outcome"] == "ACCEPTED"


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "UP"},
        {"vehicle_type": "BICYCLE"},
        {"vehicle_type": None},
        {"event_type": "SET_GREEN"},
        {"sequence_no": -1},
        {"sequence_no": True},
        {"sequence_no": "1"},
        {"sequence_no": 2**63},
        {"timestamp": "2026-10-29T04:00:00"},
        {"timestamp": "not-a-time"},
        {"timestamp": "0001-01-01T00:00:00+14:00"},
        {"timestamp": 1793246400},
        {"vehicle_id": " "},
        {"junction_id": "../A"},
        {"signal_state": "GREEN"},
    ],
)
def test_invalid_sensor_input_is_rejected_and_audited(
    client: TestClient, changes: dict[str, object]
) -> None:
    response = client.post("/api/sensor-events", json=sensor_event(**changes))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/api/junctions/A/status").json()["queues"]["NORTH"] == 0
    if "junction_id" not in changes:
        latest = client.get("/api/junctions/A/history").json()["items"][0]
        assert latest["event_type"] == "SENSOR_EVENT_REJECTED"
        assert latest["details"]["code"] == "VALIDATION_ERROR"


def test_validation_failure_does_not_reserve_an_event_id(client: TestClient) -> None:
    invalid = sensor_event()
    del invalid["vehicle_id"]
    assert client.post("/api/sensor-events", json=invalid).status_code == 422
    assert client.post("/api/sensor-events", json=sensor_event()).json()["outcome"] == "ACCEPTED"


def test_unknown_junction_has_a_durable_rejection_receipt(client: TestClient) -> None:
    payload = sensor_event(junction_id="B")
    assert client.post("/api/sensor-events", json=payload).status_code == 404
    client.post("/api/junctions", json={"junction_id": "B", "name": "Bay"})
    retry = client.post("/api/sensor-events", json=payload)
    assert retry.status_code == 404
    assert retry.json()["duplicate"] is True
    assert client.get("/api/junctions/B/status").json()["queues"]["NORTH"] == 0
    assert (
        client.post(
            "/api/sensor-events", json=sensor_event(junction_id="B", event_id="new")
        ).status_code
        == 200
    )


def test_queues_receipts_and_sequence_cursors_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "traffic.sqlite3"
    with TestClient(create_app(path, clock=lambda: NOW, run_background_tasks=False)) as first:
        first.post("/api/sensor-events", json=sensor_event(sequence_no=10))
    with TestClient(
        create_app(path, clock=lambda: NOW + timedelta(seconds=10), run_background_tasks=False)
    ) as second:
        response = second.post("/api/sensor-events", json=sensor_event(sequence_no=10))
        assert response.json()["duplicate"] is True
        assert response.json()["received_at"] == NOW.isoformat().replace("+00:00", "Z")
        stale = second.post(
            "/api/sensor-events",
            json=sensor_event(event_id="older", vehicle_id="OTHER", sequence_no=9),
        )
        assert stale.json()["code"] == "STALE_SEQUENCE"
        status = second.get("/api/junctions/A/status").json()
        assert status["queues"]["NORTH"] == 1
        assert set(status["actual_signals"].values()) == {"UNKNOWN"}
        kinds = [
            entry["event_type"]
            for entry in second.get("/api/junctions/A/history?limit=100").json()["items"]
        ]
        assert kinds.count("VEHICLE_ARRIVED") == 1
        assert kinds.count("DUPLICATE_SENSOR_EVENT") == 1
        assert kinds.count("SENSOR_EVENT_IGNORED") == 1
