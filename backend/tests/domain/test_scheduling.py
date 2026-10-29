from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from factory_traffic.domain.events import VehicleType, WaitingVehicle
from factory_traffic.domain.models import DEFAULT_JUNCTION, Direction
from factory_traffic.domain.scheduling import (
    ControlPolicy,
    PhaseService,
    choose_automatic_phase,
    phase_demands,
)

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)


def vehicle(direction: Direction, kind: VehicleType, age: int = 0) -> WaitingVehicle:
    arrived = NOW - timedelta(seconds=age)
    return WaitingVehicle(
        "A", direction.value, direction, kind, arrived, arrived, direction.value, 1
    )


@pytest.mark.parametrize(
    ("north", "east", "expected"),
    [
        (VehicleType.EMPLOYEE_VEHICLE, VehicleType.FORKLIFT, "EAST_WEST"),
        (VehicleType.FORKLIFT, VehicleType.TRUCK, "EAST_WEST"),
        (VehicleType.TRUCK, VehicleType.EMPLOYEE_VEHICLE, "NORTH_SOUTH"),
    ],
)
def test_vehicle_weights_influence_selection(
    north: VehicleType, east: VehicleType, expected: str
) -> None:
    waiting = [vehicle(Direction.NORTH, north), vehicle(Direction.EAST, east)]
    assert choose_automatic_phase(DEFAULT_JUNCTION, ControlPolicy(), waiting, NOW) == expected


def test_queue_size_and_aging_can_outweigh_one_higher_priority_vehicle() -> None:
    employees = [
        replace(vehicle(Direction.NORTH, VehicleType.EMPLOYEE_VEHICLE), vehicle_id=str(index))
        for index in range(5)
    ]
    truck = vehicle(Direction.EAST, VehicleType.TRUCK)
    assert (
        choose_automatic_phase(DEFAULT_JUNCTION, ControlPolicy(), [*employees, truck], NOW)
        == "NORTH_SOUTH"
    )
    old_employee = vehicle(Direction.NORTH, VehicleType.EMPLOYEE_VEHICLE, age=40)
    assert (
        choose_automatic_phase(DEFAULT_JUNCTION, ControlPolicy(), [old_employee, truck], NOW)
        == "NORTH_SOUTH"
    )


def test_score_ties_keep_current_green_or_use_configuration_order() -> None:
    waiting = [
        vehicle(Direction.NORTH, VehicleType.TRUCK),
        vehicle(Direction.EAST, VehicleType.TRUCK),
    ]
    assert choose_automatic_phase(DEFAULT_JUNCTION, ControlPolicy(), waiting, NOW) == "NORTH_SOUTH"
    assert (
        choose_automatic_phase(
            DEFAULT_JUNCTION, ControlPolicy(), waiting, NOW, current_phase="EAST_WEST"
        )
        == "EAST_WEST"
    )


def test_previously_served_vehicles_do_not_keep_their_original_starvation_age() -> None:
    waiting = [vehicle(Direction.NORTH, VehicleType.TRUCK, age=1000)]
    services = (PhaseService("NORTH_SOUTH", NOW - timedelta(seconds=3)),)
    demand = phase_demands(DEFAULT_JUNCTION, ControlPolicy(), waiting, NOW, services)[0]
    assert demand.wait_seconds == 3
    active = phase_demands(
        DEFAULT_JUNCTION, ControlPolicy(), waiting, NOW, services, current_phase="NORTH_SOUTH"
    )[0]
    assert active.wait_seconds == 0


@pytest.mark.parametrize(
    "field",
    ["age_step_seconds", "maximum_wait_seconds", "manual_lease_seconds", "emergency_ttl_seconds"],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_policy_duration_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        ControlPolicy(**{field: value})


@pytest.mark.parametrize("field", ["truck_weight", "forklift_weight", "employee_weight"])
@pytest.mark.parametrize("value", [0, -1, 0.5, True])
def test_invalid_vehicle_weight_is_rejected(field: str, value: int) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        ControlPolicy(**{field: value})
