from datetime import UTC, datetime, timedelta

import pytest

from factory_traffic.application import clock as clock_module


def test_elapsed_timeline_does_not_follow_wall_clock_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)
    elapsed = [100.0]
    wall = [base]

    class WallClock:
        @staticmethod
        def now(timezone):
            return wall[0]

    monkeypatch.setattr(clock_module, "datetime", WallClock)
    monkeypatch.setattr(clock_module, "monotonic", lambda: elapsed[0])
    clock = clock_module.MonotonicClock()
    elapsed[0] += 5
    wall[0] += timedelta(hours=1)
    assert clock() == base + timedelta(seconds=5)
    elapsed[0] += 1
    wall[0] -= timedelta(days=1)
    assert clock() == base + timedelta(seconds=6)
