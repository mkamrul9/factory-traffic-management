from datetime import UTC, datetime, timedelta
from time import monotonic


class MonotonicClock:
    """UTC-shaped process timeline whose intervals do not follow wall-clock jumps."""

    def __init__(self) -> None:
        self._origin = datetime.now(UTC)
        self._elapsed_origin = monotonic()

    def __call__(self) -> datetime:
        return self._origin + timedelta(seconds=monotonic() - self._elapsed_origin)
