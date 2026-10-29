"""Traffic state machine. Callers supply time, queues, and complete confirmations."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from factory_traffic.domain.events import VehicleType, WaitingVehicle
from factory_traffic.domain.models import Direction, JunctionConfiguration, SignalColor
from factory_traffic.domain.scheduling import ControlPolicy, PhaseService, choose_automatic_phase
from factory_traffic.domain.signals import (
    SignalPlan,
    all_red_signals,
    signals_for_phase,
    validate_signal_plan,
)


class OperatingMode(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"


class SignalStage(StrEnum):
    ALL_RED = "ALL_RED"
    GREEN = "GREEN"
    YELLOW = "YELLOW"


class ControlCommand(StrEnum):
    MANUAL_GREEN_REQUEST = "MANUAL_GREEN_REQUEST"
    RETURN_TO_AUTOMATIC = "RETURN_TO_AUTOMATIC"


class CommandRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConfirmationRejected(ValueError):
    pass


@dataclass(frozen=True)
class ManualOverride:
    phase_id: str
    expires_at: datetime


@dataclass(frozen=True)
class EngineState:
    junction_id: str
    mode: OperatingMode
    stage: SignalStage
    phase_id: str | None
    target_phase_id: str | None
    signal_version: int
    awaiting_confirmation: bool
    requested_at: datetime
    confirmed_at: datetime | None
    deadline: datetime | None
    observed_at: datetime
    manual_override: ManualOverride | None = None
    services: tuple[PhaseService, ...] = ()
    emergency_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineEvent:
    event_type: str
    details: dict[str, object]


@dataclass(frozen=True)
class SignalRequest:
    """Local state version; a transport adapter must assign durable command IDs."""

    version: int
    signals: SignalPlan


@dataclass(frozen=True)
class EngineResult:
    state: EngineState
    events: tuple[EngineEvent, ...] = ()
    request: SignalRequest | None = None


class TrafficEngine:
    def __init__(
        self, configuration: JunctionConfiguration, policy: ControlPolicy | None = None
    ) -> None:
        self.configuration = configuration
        self.policy = ControlPolicy() if policy is None else policy

    def start(self, now: datetime) -> EngineResult:
        self._require_aware_time(now)
        state = EngineState(
            junction_id=self.configuration.id,
            mode=OperatingMode.AUTOMATIC,
            stage=SignalStage.ALL_RED,
            phase_id=None,
            target_phase_id=None,
            signal_version=1,
            awaiting_confirmation=True,
            requested_at=now,
            confirmed_at=None,
            deadline=None,
            observed_at=now,
        )
        return EngineResult(
            state,
            (EngineEvent("SIGNAL_STATE_REQUESTED", {"stage": "ALL_RED", "reason": "STARTUP"}),),
            SignalRequest(state.signal_version, all_red_signals()),
        )

    def desired_signals(self, state: EngineState) -> SignalPlan:
        if state.junction_id != self.configuration.id:
            raise ValueError("Engine state belongs to another junction.")
        if state.stage == SignalStage.ALL_RED:
            return all_red_signals()
        if state.phase_id is None:
            raise ValueError("An active stage must identify its traffic phase.")
        color = SignalColor.GREEN if state.stage == SignalStage.GREEN else SignalColor.YELLOW
        return signals_for_phase(self.configuration, state.phase_id, color)

    @staticmethod
    def _require_aware_time(now: datetime) -> None:
        if now.utcoffset() is None:
            raise ValueError("Engine time must include a timezone.")

    def _check_input(
        self, state: EngineState, waiting: Sequence[WaitingVehicle], now: datetime
    ) -> None:
        self._require_aware_time(now)
        if state.junction_id != self.configuration.id:
            raise ValueError("Engine state belongs to another junction.")
        if now < state.observed_at:
            raise ValueError("Engine time must not move backwards.")
        if any(vehicle.junction_id != self.configuration.id for vehicle in waiting):
            raise ValueError("Queue data belongs to another junction.")

    def active_emergencies(
        self, waiting: Sequence[WaitingVehicle], now: datetime
    ) -> tuple[WaitingVehicle, ...]:
        return tuple(
            sorted(
                (
                    vehicle
                    for vehicle in waiting
                    if vehicle.vehicle_type == VehicleType.EMERGENCY
                    and (now - vehicle.arrived_at).total_seconds()
                    < self.policy.emergency_ttl_seconds
                ),
                key=lambda vehicle: (vehicle.arrived_at, vehicle.arrival_event_id),
            )
        )

    def _refresh(
        self, state: EngineState, waiting: Sequence[WaitingVehicle], now: datetime
    ) -> tuple[EngineState, list[EngineEvent]]:
        self._check_input(state, waiting, now)
        events: list[EngineEvent] = []
        manual = state.manual_override
        if manual is not None and now >= manual.expires_at:
            events.append(EngineEvent("MANUAL_OVERRIDE_EXPIRED", {"phase_id": manual.phase_id}))
            manual = None
        emergencies = self.active_emergencies(waiting, now)
        ids = tuple(vehicle.arrival_event_id for vehicle in emergencies)
        previous_ids = set(state.emergency_event_ids)
        for vehicle in emergencies:
            if vehicle.arrival_event_id not in previous_ids:
                events.append(
                    EngineEvent(
                        "EMERGENCY_DETECTED",
                        {
                            "arrival_event_id": vehicle.arrival_event_id,
                            "vehicle_id": vehicle.vehicle_id,
                            "direction": vehicle.direction,
                        },
                    )
                )
        by_event_id = {vehicle.arrival_event_id: vehicle for vehicle in waiting}
        for event_id in state.emergency_event_ids:
            if event_id not in ids:
                kind = "EMERGENCY_EXPIRED" if event_id in by_event_id else "EMERGENCY_CLEARED"
                events.append(EngineEvent(kind, {"arrival_event_id": event_id}))
        if emergencies and manual is not None:
            events.append(EngineEvent("MANUAL_OVERRIDE_CANCELLED", {"reason": "EMERGENCY"}))
            manual = None
        mode = (
            OperatingMode.EMERGENCY
            if emergencies
            else (OperatingMode.MANUAL if manual is not None else OperatingMode.AUTOMATIC)
        )
        target = state.target_phase_id
        deadline = state.deadline
        if mode != state.mode:
            events.append(EngineEvent("MODE_CHANGED", {"previous": state.mode, "new": mode}))
            target = None
            if mode == OperatingMode.EMERGENCY and state.stage == SignalStage.GREEN:
                # An emergency joining an already-green group gets a bounded service interval.
                if not state.awaiting_confirmation:
                    deadline = now + timedelta(seconds=self.configuration.timings.green_seconds)
        return replace(
            state,
            mode=mode,
            manual_override=manual,
            emergency_event_ids=ids,
            target_phase_id=target,
            deadline=deadline,
            observed_at=now,
        ), events

    def _emergency_target(
        self, state: EngineState, waiting: Sequence[WaitingVehicle], now: datetime
    ) -> str:
        emergencies = self.active_emergencies(waiting, now)
        phases: list[str] = []
        for vehicle in emergencies:
            phase = next(
                p.id for p in self.configuration.phases if vehicle.direction in p.directions
            )
            if phase not in phases:
                phases.append(phase)
        if state.stage == SignalStage.GREEN and state.phase_id in phases:
            if state.deadline is not None and now >= state.deadline:
                alternatives = [phase for phase in phases if phase != state.phase_id]
                if alternatives:
                    return alternatives[0]
            return state.phase_id
        # Keep a chosen emergency movement through YELLOW and clearance to avoid oscillation.
        if state.stage != SignalStage.GREEN and state.target_phase_id in phases:
            return state.target_phase_id
        return phases[0]

    def tick(
        self, state: EngineState, waiting: Sequence[WaitingVehicle], now: datetime
    ) -> EngineResult:
        state, events = self._refresh(state, waiting, now)
        return self._advance(state, waiting, now, events)

    def _advance(
        self,
        state: EngineState,
        waiting: Sequence[WaitingVehicle],
        now: datetime,
        events: list[EngineEvent],
    ) -> EngineResult:
        target: str | None
        if state.mode == OperatingMode.EMERGENCY:
            target = self._emergency_target(state, waiting, now)
        elif state.manual_override is not None:
            target = state.manual_override.phase_id
        else:
            current = state.phase_id if state.stage == SignalStage.GREEN else None
            target = choose_automatic_phase(
                self.configuration, self.policy, waiting, now, state.services, current
            )
        state = replace(state, target_phase_id=target)
        if state.awaiting_confirmation:
            return EngineResult(state, tuple(events))
        if state.deadline is None:
            raise ValueError("A confirmed stage must have a deadline.")

        if state.stage == SignalStage.YELLOW and now >= state.deadline:
            return self._request(state, SignalStage.ALL_RED, None, now, events, "YELLOW_COMPLETE")
        if state.stage == SignalStage.ALL_RED and now >= state.deadline and target is not None:
            return self._request(
                state, SignalStage.GREEN, target, now, events, "CLEARANCE_COMPLETE"
            )
        if state.stage == SignalStage.GREEN:
            if target is not None and target != state.phase_id:
                if state.mode != OperatingMode.AUTOMATIC or now >= state.deadline:
                    reason = f"{state.mode}_SELECTION"
                    return self._request(
                        state, SignalStage.YELLOW, state.phase_id, now, events, reason
                    )
            elif now >= state.deadline:
                state = replace(
                    state,
                    deadline=now + timedelta(seconds=self.configuration.timings.green_seconds),
                )
        return EngineResult(state, tuple(events))

    @staticmethod
    def _record_service(
        state: EngineState, phase_id: str, now: datetime
    ) -> tuple[PhaseService, ...]:
        return tuple(service for service in state.services if service.phase_id != phase_id) + (
            PhaseService(phase_id, now),
        )

    def _request(
        self,
        state: EngineState,
        stage: SignalStage,
        phase_id: str | None,
        now: datetime,
        events: list[EngineEvent],
        reason: str,
    ) -> EngineResult:
        services = state.services
        if state.stage == SignalStage.GREEN and state.phase_id is not None:
            services = self._record_service(state, state.phase_id, now)
        updated = replace(
            state,
            stage=stage,
            phase_id=phase_id,
            signal_version=state.signal_version + 1,
            awaiting_confirmation=True,
            requested_at=now,
            confirmed_at=None,
            deadline=None,
            observed_at=now,
            services=services,
        )
        signals = self.desired_signals(updated)
        validate_signal_plan(self.configuration, signals)
        events.append(
            EngineEvent(
                "SIGNAL_TRANSITION_STARTED",
                {
                    "previous_stage": state.stage,
                    "new_stage": stage,
                    "phase_id": phase_id,
                    "reason": reason,
                },
            )
        )
        events.append(
            EngineEvent(
                "SIGNAL_STATE_REQUESTED",
                {
                    "signal_version": updated.signal_version,
                    "signals": dict(signals),
                },
            )
        )
        return EngineResult(updated, tuple(events), SignalRequest(updated.signal_version, signals))

    def confirm_signals(
        self,
        state: EngineState,
        version: int,
        actual_signals: Mapping[Direction, SignalColor],
        waiting: Sequence[WaitingVehicle],
        now: datetime,
    ) -> EngineResult:
        self._check_input(state, waiting, now)
        if (
            isinstance(version, bool)
            or not state.awaiting_confirmation
            or version != state.signal_version
        ):
            raise ConfirmationRejected(
                "Confirmation does not match the outstanding signal request."
            )
        validate_signal_plan(self.configuration, actual_signals)
        if actual_signals != self.desired_signals(state):
            raise ConfirmationRejected("Controller signals do not match the requested snapshot.")
        duration = {
            SignalStage.GREEN: self.configuration.timings.green_seconds,
            SignalStage.YELLOW: self.configuration.timings.yellow_seconds,
            SignalStage.ALL_RED: self.configuration.timings.all_red_seconds,
        }[state.stage]
        services = state.services
        if state.stage == SignalStage.GREEN and state.phase_id is not None:
            services = self._record_service(state, state.phase_id, now)
        confirmed = replace(
            state,
            awaiting_confirmation=False,
            confirmed_at=now,
            deadline=now + timedelta(seconds=duration),
            observed_at=now,
            services=services,
        )
        refreshed, events = self._refresh(confirmed, waiting, now)
        events.insert(
            0,
            EngineEvent(
                "SIGNAL_STATE_CONFIRMED",
                {
                    "signal_version": version,
                    "stage": state.stage,
                    "phase_id": state.phase_id,
                },
            ),
        )
        return self._advance(refreshed, waiting, now, events)

    def execute_command(
        self,
        state: EngineState,
        command: ControlCommand,
        waiting: Sequence[WaitingVehicle],
        now: datetime,
        direction: Direction | None = None,
    ) -> EngineResult:
        state, events = self._refresh(state, waiting, now)
        if not isinstance(command, ControlCommand):
            raise CommandRejected("INVALID_COMMAND", "Unknown control command.")
        if command == ControlCommand.MANUAL_GREEN_REQUEST:
            if state.mode == OperatingMode.EMERGENCY:
                raise CommandRejected(
                    "EMERGENCY_ACTIVE", "Manual control cannot override an emergency."
                )
            phase = next(
                (phase for phase in self.configuration.phases if direction in phase.directions),
                None,
            )
            if phase is None:
                raise CommandRejected("INVALID_DIRECTION", "A known direction is required.")
            manual = ManualOverride(
                phase.id, now + timedelta(seconds=self.policy.manual_lease_seconds)
            )
            if state.mode != OperatingMode.MANUAL:
                events.append(
                    EngineEvent(
                        "MODE_CHANGED",
                        {
                            "previous": state.mode,
                            "new": OperatingMode.MANUAL,
                        },
                    )
                )
            state = replace(state, mode=OperatingMode.MANUAL, manual_override=manual)
            events.append(
                EngineEvent(
                    "MANUAL_OVERRIDE",
                    {
                        "phase_id": phase.id,
                        "expires_at": manual.expires_at.isoformat(),
                    },
                )
            )
        else:
            if direction is not None:
                raise CommandRejected(
                    "INVALID_DIRECTION", "RETURN_TO_AUTOMATIC takes no direction."
                )
            state = replace(state, manual_override=None)
            state, changes = self._refresh(state, waiting, now)
            events.extend(changes)
            events.append(EngineEvent("RETURN_TO_AUTOMATIC", {}))
        return self._advance(state, waiting, now, events)
