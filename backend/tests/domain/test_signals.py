from itertools import product

import pytest

from factory_traffic.domain.models import (
    DEFAULT_JUNCTION,
    Direction,
    JunctionConfiguration,
    SignalColor,
    SignalTimings,
    TrafficPhase,
)
from factory_traffic.domain.signals import (
    UnsafeSignalPlan,
    all_red_signals,
    signals_for_phase,
    validate_signal_plan,
)


@pytest.mark.parametrize("colors", list(product(SignalColor, repeat=4)))
def test_every_possible_signal_snapshot_preserves_conflict_rules(
    colors: tuple[SignalColor, ...],
) -> None:
    signals = dict(zip(Direction, colors, strict=True))
    north, south, east, west = colors
    active_ns = {color for color in (north, south) if color != SignalColor.RED}
    active_ew = {color for color in (east, west) if color != SignalColor.RED}
    unsafe = bool(active_ns and active_ew) or len(active_ns | active_ew) > 1

    if unsafe:
        with pytest.raises(UnsafeSignalPlan):
            validate_signal_plan(DEFAULT_JUNCTION, signals)
    else:
        validate_signal_plan(DEFAULT_JUNCTION, signals)


def test_partial_signal_snapshot_is_rejected() -> None:
    signals = all_red_signals()
    del signals[Direction.WEST]
    with pytest.raises(UnsafeSignalPlan, match="four known directions"):
        validate_signal_plan(DEFAULT_JUNCTION, signals)


def test_unknown_phase_cannot_produce_a_signal_request() -> None:
    with pytest.raises(ValueError, match="Unknown traffic phase"):
        signals_for_phase(DEFAULT_JUNCTION, "UNKNOWN", SignalColor.GREEN)


def test_signal_plans_do_not_share_mutable_state() -> None:
    first = signals_for_phase(DEFAULT_JUNCTION, "NORTH_SOUTH", SignalColor.GREEN)
    second = all_red_signals()
    first[Direction.EAST] = SignalColor.GREEN
    assert set(second.values()) == {SignalColor.RED}


def test_another_junction_can_use_the_same_signal_rules() -> None:
    junction = JunctionConfiguration(
        id="B",
        name="Warehouse junction",
        phases=(
            TrafficPhase("LOADING_ROAD", (Direction.NORTH, Direction.SOUTH)),
            TrafficPhase("SERVICE_ROAD", (Direction.EAST, Direction.WEST)),
        ),
    )
    assert signals_for_phase(junction, "SERVICE_ROAD", SignalColor.GREEN) == {
        Direction.NORTH: SignalColor.RED,
        Direction.SOUTH: SignalColor.RED,
        Direction.EAST: SignalColor.GREEN,
        Direction.WEST: SignalColor.GREEN,
    }


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
@pytest.mark.parametrize(
    "field",
    ["green_seconds", "yellow_seconds", "all_red_seconds", "acknowledgement_timeout_seconds"],
)
def test_invalid_signal_timings_are_rejected(field: str, duration: float) -> None:
    with pytest.raises(ValueError, match="finite, positive"):
        SignalTimings(**{field: duration})


@pytest.mark.parametrize(
    "phases",
    [
        (),
        (
            TrafficPhase("NS", (Direction.NORTH, Direction.SOUTH)),
            TrafficPhase("EW", (Direction.EAST,)),
        ),
        (
            TrafficPhase("NS", (Direction.NORTH, Direction.SOUTH)),
            TrafficPhase("EW", (Direction.EAST, Direction.WEST, Direction.NORTH)),
        ),
        (
            TrafficPhase("SAME", (Direction.NORTH, Direction.SOUTH)),
            TrafficPhase("SAME", (Direction.EAST, Direction.WEST)),
        ),
    ],
)
def test_invalid_junction_topology_is_rejected(phases: tuple[TrafficPhase, ...]) -> None:
    with pytest.raises(ValueError):
        JunctionConfiguration(id="A", name="Junction A", phases=phases)
