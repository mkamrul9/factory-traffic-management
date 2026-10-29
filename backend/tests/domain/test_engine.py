from dataclasses import replace
from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from factory_traffic.domain.engine import (
    CommandRejected,
    ConfirmationRejected,
    ControlCommand,
    EngineResult,
    OperatingMode,
    SignalStage,
    TrafficEngine,
)
from factory_traffic.domain.events import VehicleType, WaitingVehicle
from factory_traffic.domain.models import DEFAULT_JUNCTION, Direction, SignalColor
from factory_traffic.domain.signals import UnsafeSignalPlan, all_red_signals, validate_signal_plan

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)


class Scenario:
    def __init__(self) -> None:
        self.engine = TrafficEngine(DEFAULT_JUNCTION)
        self.now = NOW
        self.waiting: list[WaitingVehicle] = []
        self.result = self.engine.start(self.now)

    def arrive(
        self, direction: Direction, vehicle_type: VehicleType = VehicleType.TRUCK
    ) -> WaitingVehicle:
        index = len(self.waiting)
        vehicle = WaitingVehicle(
            "A", f"VH-{index}", direction, vehicle_type, self.now, self.now, f"event-{index}", index
        )
        self.waiting.append(vehicle)
        return vehicle

    def tick(self, seconds: float = 0) -> EngineResult:
        self.now += timedelta(seconds=seconds)
        self.result = self.engine.tick(self.result.state, self.waiting, self.now)
        return self.result

    def confirm(self, delay: float = 0) -> EngineResult:
        self.now += timedelta(seconds=delay)
        state = self.result.state
        self.result = self.engine.confirm_signals(
            state, state.signal_version, self.engine.desired_signals(state), self.waiting, self.now
        )
        return self.result

    def manual(self, direction: Direction) -> EngineResult:
        self.result = self.engine.execute_command(
            self.result.state,
            ControlCommand.MANUAL_GREEN_REQUEST,
            self.waiting,
            self.now,
            direction,
        )
        return self.result

    def automatic(self) -> EngineResult:
        self.result = self.engine.execute_command(
            self.result.state, ControlCommand.RETURN_TO_AUTOMATIC, self.waiting, self.now
        )
        return self.result

    def start_green(self, vehicle_type: VehicleType = VehicleType.TRUCK) -> None:
        self.arrive(Direction.NORTH, vehicle_type)
        self.confirm()
        self.tick(2)
        self.confirm()
        assert self.result.state.stage == SignalStage.GREEN

    def complete_switch(self) -> None:
        assert self.result.state.stage == SignalStage.YELLOW
        self.confirm()
        self.tick(5)
        assert self.result.state.stage == SignalStage.ALL_RED
        self.confirm()
        self.tick(2)
        assert self.result.state.stage == SignalStage.GREEN
        self.confirm()


def test_startup_waits_for_confirmation_and_fresh_clearance() -> None:
    scenario = Scenario()
    scenario.arrive(Direction.NORTH)
    scenario.tick(3600)
    assert scenario.result.state.stage == SignalStage.ALL_RED
    assert scenario.result.state.deadline is None
    assert scenario.result.request is None
    scenario.confirm()
    scenario.tick(1.999)
    assert scenario.result.state.stage == SignalStage.ALL_RED
    scenario.tick(0.001)
    assert scenario.result.state.stage == SignalStage.GREEN
    assert scenario.result.state.awaiting_confirmation
    assert scenario.result.state.deadline is None
    scenario.confirm(10)
    assert scenario.result.state.deadline == scenario.now + timedelta(seconds=30)


def test_each_clearance_interval_starts_after_confirmation() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.tick(1)
    scenario.manual(Direction.WEST)
    assert scenario.result.state.stage == SignalStage.YELLOW
    scenario.tick(100)
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.request is None
    scenario.confirm()
    scenario.tick(4.999)
    assert scenario.result.state.stage == SignalStage.YELLOW
    scenario.tick(0.001)
    assert scenario.result.state.stage == SignalStage.ALL_RED
    scenario.confirm(100)
    scenario.tick(1.999)
    assert scenario.result.state.stage == SignalStage.ALL_RED
    scenario.tick(0.001)
    # The manual lease has expired during the delayed confirmations; resume the NORTH queue.
    assert scenario.result.state.phase_id == "NORTH_SOUTH"
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC


def test_normal_traffic_gets_its_green_interval_before_switching() -> None:
    scenario = Scenario()
    scenario.start_green(VehicleType.EMPLOYEE_VEHICLE)
    scenario.arrive(Direction.EAST, VehicleType.TRUCK)
    scenario.tick(29.999)
    assert scenario.result.state.stage == SignalStage.GREEN
    scenario.tick(0.001)
    assert scenario.result.state.stage == SignalStage.YELLOW
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"


def test_empty_or_same_phase_queues_do_not_cause_unnecessary_switches() -> None:
    scenario = Scenario()
    scenario.confirm()
    scenario.tick(100)
    assert scenario.result.state.stage == SignalStage.ALL_RED
    assert scenario.result.request is None
    scenario.arrive(Direction.SOUTH)
    scenario.tick()
    scenario.confirm()
    scenario.waiting.clear()
    scenario.tick(30)
    assert scenario.result.state.stage == SignalStage.GREEN
    assert scenario.result.request is None


def test_starvation_rule_serves_light_traffic_against_a_heavy_queue() -> None:
    scenario = Scenario()
    for _ in range(10):
        scenario.arrive(Direction.NORTH)
    scenario.confirm()
    scenario.tick(2)
    scenario.confirm()
    scenario.arrive(Direction.EAST, VehicleType.EMPLOYEE_VEHICLE)
    for _ in range(3):
        scenario.tick(30)
        assert scenario.result.state.stage == SignalStage.GREEN
    scenario.tick(30)
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.target_phase_id == "EAST_WEST"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"


def test_manual_request_preempts_normal_green_safely() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.tick(1)
    scenario.manual(Direction.WEST)
    assert scenario.result.state.mode == OperatingMode.MANUAL
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.phase_id == "NORTH_SOUTH"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"
    assert scenario.result.state.mode == OperatingMode.MANUAL


def test_latest_manual_request_wins_without_reversing_yellow() -> None:
    scenario = Scenario()
    scenario.start_green()
    first = scenario.manual(Direction.EAST)
    second = scenario.manual(Direction.SOUTH)
    assert first.state.target_phase_id == "EAST_WEST"
    assert second.state.target_phase_id == "NORTH_SOUTH"
    assert second.state.stage == SignalStage.YELLOW
    assert second.request is None
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "NORTH_SOUTH"


def test_manual_lease_expires_without_operator_disconnect_detection() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.manual(Direction.NORTH)
    lease = scenario.result.state.manual_override
    assert lease is not None
    scenario.tick(119.999)
    assert scenario.result.state.manual_override == lease
    scenario.tick(0.001)
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC
    assert scenario.result.state.manual_override is None
    assert "MANUAL_OVERRIDE_EXPIRED" in [event.event_type for event in scenario.result.events]


def test_return_to_automatic_during_transition_keeps_clearance() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.manual(Direction.EAST)
    scenario.automatic()
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "NORTH_SOUTH"


def test_emergency_preempts_green_and_cancels_manual_lease() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.manual(Direction.NORTH)
    scenario.tick(1)
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick()
    assert scenario.result.state.mode == OperatingMode.EMERGENCY
    assert scenario.result.state.manual_override is None
    assert scenario.result.state.stage == SignalStage.YELLOW
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"
    scenario.waiting = [v for v in scenario.waiting if v.vehicle_type != VehicleType.EMERGENCY]
    scenario.tick()
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC
    assert scenario.result.state.manual_override is None
    assert "EMERGENCY_CLEARED" in [event.event_type for event in scenario.result.events]


def test_manual_control_cannot_override_an_active_emergency() -> None:
    scenario = Scenario()
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick()
    previous = scenario.result.state
    with pytest.raises(CommandRejected) as rejected:
        scenario.manual(Direction.NORTH)
    assert rejected.value.code == "EMERGENCY_ACTIVE"
    assert scenario.result.state == previous
    scenario.automatic()
    assert scenario.result.state.mode == OperatingMode.EMERGENCY


def test_competing_emergencies_rotate_after_bounded_green_intervals() -> None:
    scenario = Scenario()
    scenario.start_green(VehicleType.EMERGENCY)
    scenario.tick(1)
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick()
    assert scenario.result.state.stage == SignalStage.GREEN
    scenario.tick(29)
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.target_phase_id == "EAST_WEST"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"
    scenario.tick(30)
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.target_phase_id == "NORTH_SOUTH"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "NORTH_SOUTH"


def test_initial_competing_emergencies_select_the_oldest_request() -> None:
    scenario = Scenario()
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick(1)
    scenario.arrive(Direction.NORTH, VehicleType.EMERGENCY)
    scenario.confirm()
    scenario.tick(2)
    assert scenario.result.state.phase_id == "EAST_WEST"


def test_emergency_expiry_does_not_remove_a_physical_waiting_vehicle() -> None:
    scenario = Scenario()
    scenario.arrive(Direction.NORTH, VehicleType.EMERGENCY)
    scenario.tick()
    scenario.tick(119.999)
    assert scenario.result.state.mode == OperatingMode.EMERGENCY
    scenario.tick(0.001)
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC
    assert len(scenario.waiting) == 1
    assert "EMERGENCY_EXPIRED" in [event.event_type for event in scenario.result.events]
    scenario.tick()
    assert "EMERGENCY_EXPIRED" not in [event.event_type for event in scenario.result.events]


def test_repeated_emergency_observation_does_not_renew_service_or_timeout() -> None:
    scenario = Scenario()
    scenario.start_green(VehicleType.EMERGENCY)
    deadline = scenario.result.state.deadline
    for _ in range(10):
        scenario.tick(1)
        assert scenario.result.state.deadline == deadline
        assert "EMERGENCY_DETECTED" not in [event.event_type for event in scenario.result.events]
    scenario.tick(108)
    assert scenario.result.state.mode == OperatingMode.AUTOMATIC


def test_emergency_while_green_is_unconfirmed_waits_then_starts_yellow() -> None:
    scenario = Scenario()
    scenario.arrive(Direction.NORTH)
    scenario.confirm()
    scenario.tick(2)
    assert scenario.result.state.awaiting_confirmation
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick()
    assert scenario.result.state.stage == SignalStage.GREEN
    assert scenario.result.state.phase_id == "NORTH_SOUTH"
    assert scenario.result.request is None
    scenario.confirm()
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.phase_id == "NORTH_SOUTH"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "EAST_WEST"


def test_emergency_during_yellow_can_retarget_but_never_skip_clearance() -> None:
    scenario = Scenario()
    scenario.start_green()
    scenario.manual(Direction.EAST)
    scenario.arrive(Direction.NORTH, VehicleType.EMERGENCY)
    scenario.tick()
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.target_phase_id == "NORTH_SOUTH"
    scenario.complete_switch()
    assert scenario.result.state.phase_id == "NORTH_SOUTH"
    assert scenario.result.state.mode == OperatingMode.EMERGENCY


def test_emergency_during_all_red_does_not_shorten_the_interval() -> None:
    scenario = Scenario()
    scenario.confirm()
    scenario.tick(1)
    scenario.arrive(Direction.EAST, VehicleType.EMERGENCY)
    scenario.tick()
    assert scenario.result.state.stage == SignalStage.ALL_RED
    scenario.tick(1)
    assert scenario.result.state.stage == SignalStage.GREEN
    assert scenario.result.state.phase_id == "EAST_WEST"


@pytest.mark.parametrize("version", [0, 2, True])
def test_uncorrelated_confirmation_cannot_advance_the_state(version: int) -> None:
    scenario = Scenario()
    previous = scenario.result.state
    with pytest.raises(ConfirmationRejected):
        scenario.engine.confirm_signals(previous, version, all_red_signals(), [], NOW)
    assert scenario.result.state == previous


def test_duplicate_confirmation_is_rejected() -> None:
    scenario = Scenario()
    scenario.confirm()
    with pytest.raises(ConfirmationRejected):
        scenario.confirm()


def test_mismatched_or_partial_confirmation_cannot_start_timing() -> None:
    scenario = Scenario()
    partial = all_red_signals()
    del partial[Direction.WEST]
    with pytest.raises(UnsafeSignalPlan):
        scenario.engine.confirm_signals(scenario.result.state, 1, partial, [], NOW)
    different = all_red_signals()
    different[Direction.NORTH] = SignalColor.GREEN
    with pytest.raises(ConfirmationRejected):
        scenario.engine.confirm_signals(scenario.result.state, 1, different, [], NOW)
    assert scenario.result.state.deadline is None
    assert scenario.result.state.awaiting_confirmation


@pytest.mark.parametrize("direction", [None, "UP"])
def test_invalid_manual_direction_cannot_change_signals(direction: Direction | None) -> None:
    scenario = Scenario()
    with pytest.raises(CommandRejected):
        scenario.engine.execute_command(
            scenario.result.state, ControlCommand.MANUAL_GREEN_REQUEST, [], NOW, direction
        )
    assert scenario.result.state.signal_version == 1


def test_unknown_command_and_extra_direction_are_rejected() -> None:
    scenario = Scenario()
    with pytest.raises(CommandRejected, match="Unknown control command"):
        scenario.engine.execute_command(scenario.result.state, "SET_GREEN", [], NOW)  # type: ignore[arg-type]
    with pytest.raises(CommandRejected, match="takes no direction"):
        scenario.engine.execute_command(
            scenario.result.state, ControlCommand.RETURN_TO_AUTOMATIC, [], NOW, Direction.NORTH
        )


def test_time_must_be_aware_and_cannot_go_backwards() -> None:
    scenario = Scenario()
    with pytest.raises(ValueError, match="timezone"):
        scenario.engine.start(datetime(2026, 10, 29))
    scenario.tick(1)
    with pytest.raises(ValueError, match="backwards"):
        scenario.engine.tick(scenario.result.state, [], NOW)


def test_state_and_queues_cannot_cross_junction_boundaries() -> None:
    scenario = Scenario()
    other = replace(scenario.result.state, junction_id="B")
    with pytest.raises(ValueError, match="another junction"):
        scenario.engine.tick(other, [], NOW)
    vehicle = scenario.arrive(Direction.NORTH)
    with pytest.raises(ValueError, match="another junction"):
        scenario.engine.tick(scenario.result.state, [replace(vehicle, junction_id="B")], NOW)


def test_long_clock_jump_requests_only_one_transition() -> None:
    scenario = Scenario()
    scenario.start_green(VehicleType.EMPLOYEE_VEHICLE)
    scenario.arrive(Direction.EAST)
    scenario.tick(3600)
    assert scenario.result.state.stage == SignalStage.YELLOW
    assert scenario.result.state.awaiting_confirmation
    scenario.confirm()
    assert scenario.result.state.deadline == scenario.now + timedelta(seconds=5)


def test_request_payload_mutation_does_not_mutate_engine_state() -> None:
    scenario = Scenario()
    assert scenario.result.request is not None
    scenario.result.request.signals[Direction.NORTH] = SignalColor.GREEN
    assert set(scenario.engine.desired_signals(scenario.result.state).values()) == {SignalColor.RED}


@pytest.mark.parametrize("seed", [7, 19, 42])
def test_mixed_event_sequences_preserve_every_signal_transition(seed: int) -> None:
    random = Random(seed)
    scenario = Scenario()
    for index in range(1000):
        previous = scenario.result.state
        scenario.now += timedelta(seconds=random.choice([0, 0.1, 1, 5, 40]))
        action = random.randrange(5)
        if action == 0:
            direction = random.choice(list(Direction))
            kind = random.choice(list(VehicleType))
            scenario.waiting.append(
                WaitingVehicle(
                    "A",
                    f"random-{index}",
                    direction,
                    kind,
                    scenario.now,
                    scenario.now,
                    f"random-event-{index}",
                    index,
                )
            )
        elif action == 1 and scenario.waiting:
            scenario.waiting.pop(random.randrange(len(scenario.waiting)))
        try:
            if action == 2:
                result = scenario.engine.execute_command(
                    previous,
                    ControlCommand.MANUAL_GREEN_REQUEST,
                    scenario.waiting,
                    scenario.now,
                    random.choice(list(Direction)),
                )
            elif action == 3:
                result = scenario.engine.execute_command(
                    previous, ControlCommand.RETURN_TO_AUTOMATIC, scenario.waiting, scenario.now
                )
            elif action == 4 and previous.awaiting_confirmation:
                result = scenario.engine.confirm_signals(
                    previous,
                    previous.signal_version,
                    scenario.engine.desired_signals(previous),
                    scenario.waiting,
                    scenario.now,
                )
            else:
                result = scenario.engine.tick(previous, scenario.waiting, scenario.now)
        except CommandRejected:
            result = scenario.engine.tick(previous, scenario.waiting, scenario.now)
        scenario.result = result
        validate_signal_plan(DEFAULT_JUNCTION, scenario.engine.desired_signals(result.state))
        if result.request is None:
            assert result.state.signal_version == previous.signal_version
            continue
        assert result.request.version == previous.signal_version + 1
        validate_signal_plan(DEFAULT_JUNCTION, result.request.signals)
        if previous.stage == SignalStage.GREEN:
            assert result.state.stage == SignalStage.YELLOW
            assert result.state.phase_id == previous.phase_id
        elif previous.stage == SignalStage.YELLOW:
            assert result.state.stage == SignalStage.ALL_RED
            assert previous.confirmed_at is not None
            assert (scenario.now - previous.confirmed_at).total_seconds() >= 5
        else:
            assert result.state.stage == SignalStage.GREEN
            assert previous.confirmed_at is not None
            assert (scenario.now - previous.confirmed_at).total_seconds() >= 2
