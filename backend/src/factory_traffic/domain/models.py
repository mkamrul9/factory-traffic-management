from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class Direction(StrEnum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class SignalColor(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


@dataclass(frozen=True)
class TrafficPhase:
    """Directions declared compatible by the junction configuration."""

    id: str
    directions: tuple[Direction, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("A traffic phase must have an id.")
        if not self.directions or len(set(self.directions)) != len(self.directions):
            raise ValueError("A traffic phase must have distinct, non-empty directions.")
        if any(not isinstance(direction, Direction) for direction in self.directions):
            raise ValueError("A traffic phase contains an unknown direction.")


@dataclass(frozen=True)
class SignalTimings:
    green_seconds: float = 30.0
    yellow_seconds: float = 5.0
    all_red_seconds: float = 2.0
    acknowledgement_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        durations = (
            self.green_seconds,
            self.yellow_seconds,
            self.all_red_seconds,
            self.acknowledgement_timeout_seconds,
        )
        if any(isinstance(value, bool) or not isfinite(value) or value <= 0 for value in durations):
            raise ValueError("Signal durations must be finite, positive numbers.")


DEFAULT_PHASES = (
    TrafficPhase("NORTH_SOUTH", (Direction.NORTH, Direction.SOUTH)),
    TrafficPhase("EAST_WEST", (Direction.EAST, Direction.WEST)),
)


@dataclass(frozen=True)
class JunctionConfiguration:
    id: str
    name: str
    phases: tuple[TrafficPhase, ...] = DEFAULT_PHASES
    timings: SignalTimings = SignalTimings()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("A junction must have an id and a name.")
        if len(self.phases) < 2 or len({phase.id for phase in self.phases}) != len(self.phases):
            raise ValueError("A junction must have at least two phases with distinct ids.")
        directions = [direction for phase in self.phases for direction in phase.directions]
        if len(directions) != len(Direction) or set(directions) != set(Direction):
            raise ValueError("Every direction must belong to exactly one traffic phase.")

    def get_phase(self, phase_id: str) -> TrafficPhase:
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        raise ValueError(f"Unknown traffic phase: {phase_id}")


DEFAULT_JUNCTION = JunctionConfiguration(id="A", name="Junction A")
