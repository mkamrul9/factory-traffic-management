from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory_traffic.api.app import create_app

NOW = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)


def sensor_event(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": "evt-1",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "VH-1",
        "vehicle_type": "TRUCK",
        "sequence_no": 1,
        "timestamp": NOW.isoformat(),
    }
    payload.update(changes)
    return payload


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(tmp_path / "traffic.sqlite3", clock=lambda: NOW, run_background_tasks=False)
    ) as connection:
        yield connection


@pytest.fixture
def control(tmp_path: Path):
    from control_helpers import ControlHarness, FakeClock

    clock = FakeClock()
    with TestClient(
        create_app(tmp_path / "traffic.sqlite3", clock=clock, run_background_tasks=False)
    ) as connection:
        yield ControlHarness(connection, clock)
