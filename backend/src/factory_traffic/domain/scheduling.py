"""Deterministic queue scoring and normal-traffic starvation protection."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from factory_traffic.domain.events import VehicleType, WaitingVehicle
from factory_traffic.domain.models import JunctionConfiguration


@dataclass(frozen=True)
class ControlPolicy:
    truck_weight: int = 4
    forklift_weight: int = 2
    employee_weight: int = 1
    age_step_seconds: float = 10.0
    maximum_wait_seconds: float = 120.0
    manual_lease_seconds: float = 120.0
    emergency_ttl_seconds: float = 120.0

    def __post_init__(self) -> None:
        weights = (self.truck_weight, self.forklift_weight, self.employee_weight)
        durations = (
            self.age_step_seconds,
            self.maximum_wait_seconds,
            self.manual_lease_seconds,
            self.emergency_ttl_seconds,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in weights
        ):
            raise ValueError("Vehicle weights must be positive integers.")
        if any(isinstance(value, bool) or not isfinite(value) or value <= 0 for value in durations):
            raise ValueError("Control policy durations must be finite and positive.")

    def weight(self, vehicle_type: VehicleType) -> int:
        if vehicle_type == VehicleType.EMPLOYEE_VEHICLE:
            return self.employee_weight
        if vehicle_type == VehicleType.FORKLIFT:
            return self.forklift_weight
        # An expired emergency remains a physical queued vehicle at ordinary truck priority.
        return self.truck_weight


@dataclass(frozen=True)
class PhaseService:
    phase_id: str
    served_at: datetime


@dataclass(frozen=True)
class PhaseDemand:
    phase_id: str
    vehicle_count: int
    score: float
    wait_seconds: float


def phase_demands(
    configuration: JunctionConfiguration,
    policy: ControlPolicy,
    waiting: Sequence[WaitingVehicle],
    now: datetime,
    services: tuple[PhaseService, ...] = (),
    current_phase: str | None = None,
) -> tuple[PhaseDemand, ...]:
    if any(vehicle.junction_id != configuration.id for vehicle in waiting):
        raise ValueError("Queue data belongs to another junction.")
    served_at = {service.phase_id: service.served_at for service in services}
    demands = []
    for phase in configuration.phases:
        vehicles = [vehicle for vehicle in waiting if vehicle.direction in phase.directions]
        age = 0.0
        if vehicles and phase.id != current_phase:
            oldest = min(vehicle.arrived_at for vehicle in vehicles)
            since = max(oldest, served_at.get(phase.id, oldest))
            age = max(0.0, (now - since).total_seconds())
        score = sum(policy.weight(vehicle.vehicle_type) for vehicle in vehicles)
        weighted_score = score + age // policy.age_step_seconds
        demands.append(PhaseDemand(phase.id, len(vehicles), weighted_score, age))
    return tuple(demands)


def choose_automatic_phase(
    configuration: JunctionConfiguration,
    policy: ControlPolicy,
    waiting: Sequence[WaitingVehicle],
    now: datetime,
    services: tuple[PhaseService, ...] = (),
    current_phase: str | None = None,
) -> str | None:
    demands = [
        demand
        for demand in phase_demands(configuration, policy, waiting, now, services, current_phase)
        if demand.vehicle_count
    ]
    if not demands:
        return None
    overdue = [demand for demand in demands if demand.wait_seconds >= policy.maximum_wait_seconds]
    if overdue:
        return max(overdue, key=lambda demand: demand.wait_seconds).phase_id
    # max() preserves configuration order when both score and current-phase preference tie.
    return max(
        demands, key=lambda demand: (demand.score, demand.phase_id == current_phase)
    ).phase_id
