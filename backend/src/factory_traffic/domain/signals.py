from collections.abc import Mapping

from factory_traffic.domain.models import Direction, JunctionConfiguration, SignalColor

type SignalPlan = dict[Direction, SignalColor]


class UnsafeSignalPlan(ValueError):
    """A requested signal plan violates a junction safety rule."""


def all_red_signals() -> SignalPlan:
    return {direction: SignalColor.RED for direction in Direction}


def signals_for_phase(
    configuration: JunctionConfiguration,
    phase_id: str,
    color: SignalColor,
) -> SignalPlan:
    phase = configuration.get_phase(phase_id)
    if not isinstance(color, SignalColor):
        raise UnsafeSignalPlan("Unknown signal color.")

    signals = all_red_signals()
    for direction in phase.directions:
        signals[direction] = color
    validate_signal_plan(configuration, signals)
    return signals


def validate_signal_plan(
    configuration: JunctionConfiguration,
    signals: Mapping[Direction, SignalColor],
) -> None:
    """Validate one desired snapshot; transition ordering needs the state machine."""
    if set(signals) != set(Direction):
        raise UnsafeSignalPlan("A signal plan must contain exactly the four known directions.")
    if any(not isinstance(color, SignalColor) for color in signals.values()):
        raise UnsafeSignalPlan("A signal plan contains an unknown signal color.")

    active = {direction for direction, color in signals.items() if color != SignalColor.RED}
    if not active:
        return

    # A clearing phase must finish before a conflicting phase can begin moving.
    if len({signals[direction] for direction in active}) != 1:
        raise UnsafeSignalPlan("GREEN and YELLOW must not be requested together.")
    if not any(active.issubset(phase.directions) for phase in configuration.phases):
        raise UnsafeSignalPlan("Conflicting traffic phases must remain RED.")
