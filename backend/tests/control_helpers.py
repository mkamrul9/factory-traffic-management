from datetime import datetime, timedelta
from uuid import uuid4

from conftest import NOW, sensor_event
from fastapi.testclient import TestClient


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ControlHarness:
    def __init__(self, client: TestClient, clock: FakeClock) -> None:
        self.client = client
        self.clock = clock

    def status(self) -> dict:
        response = self.client.get("/api/junctions/A/status")
        assert response.status_code == 200
        return response.json()

    def tick(self, seconds: float = 0) -> dict:
        self.clock.advance(seconds)
        self.client.app.state.traffic_service.tick_all()
        return self.status()

    def acknowledge(self) -> dict:
        status = self.status()
        response = self.client.post(
            "/api/junctions/A/simulator/acknowledge", json={"batch_id": status["batch_id"]}
        )
        assert response.status_code == 200, response.text
        return self.status()

    def ready(self) -> dict:
        self.acknowledge()
        status = self.tick(2)
        assert status["control_active"] is True
        return status

    def arrival(self, direction: str = "NORTH", kind: str = "TRUCK") -> dict:
        sequence = self.status()["sequence_numbers"][direction]
        token = uuid4().hex
        payload = sensor_event(
            event_id=f"evt-{token}",
            vehicle_id=f"VH-{token}",
            direction=direction,
            vehicle_type=kind,
            sequence_no=0 if sequence is None else sequence + 1,
            timestamp=self.clock().isoformat(),
        )
        response = self.client.post("/api/sensor-events", json=payload)
        assert response.status_code == 200, response.text
        return payload

    def green(self, kind: str = "TRUCK") -> dict:
        self.ready()
        arrival = self.arrival(kind=kind)
        self.acknowledge()
        assert self.status()["actual_signals"]["NORTH"] == "GREEN"
        return arrival

    def complete_switch(self) -> dict:
        assert self.status()["stage"] == "YELLOW"
        self.acknowledge()
        self.tick(5)
        assert self.status()["stage"] == "ALL_RED"
        self.acknowledge()
        self.tick(2)
        assert self.status()["stage"] == "GREEN"
        return self.acknowledge()

    def device(
        self, device_type: str, status: str, direction: str | None = None, **changes: object
    ):
        payload = {
            "event_id": f"device-{uuid4().hex}",
            "junction_id": "A",
            "device_type": device_type,
            "status": status,
            "timestamp": self.clock().isoformat(),
        }
        if direction is not None:
            payload["direction"] = direction
        payload.update(changes)
        return self.client.post("/api/device-events", json=payload)
