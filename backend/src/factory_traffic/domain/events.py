"""Vehicle event rules, independent of HTTP and persistence."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from factory_traffic.domain.models import Direction


class VehicleType(StrEnum):
    FORKLIFT = "FORKLIFT"
    TRUCK = "TRUCK"
    EMPLOYEE_VEHICLE = "EMPLOYEE_VEHICLE"
    EMERGENCY = "EMERGENCY"


class SensorEventType(StrEnum):
    VEHICLE_ARRIVED = "VEHICLE_ARRIVED"
    VEHICLE_CLEARED = "VEHICLE_CLEARED"


class EventOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"


class QueueChange(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    NONE = "NONE"


@dataclass(frozen=True)
class SensorEvent:
    event_id: str
    junction_id: str
    direction: Direction
    event_type: SensorEventType
    vehicle_id: str
    sequence_no: int
    timestamp: datetime
    vehicle_type: VehicleType | None = None

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() is None:
            raise ValueError("Sensor timestamps must include a timezone.")
        if self.sequence_no < 0:
            raise ValueError("Sequence numbers must be nonnegative.")
        if self.event_type == SensorEventType.VEHICLE_ARRIVED and self.vehicle_type is None:
            raise ValueError("An arrival must specify a vehicle type.")

    def canonical_payload(self) -> str:
        """Equivalent timezone representations have the same logical identity."""
        return json.dumps(
            {
                "event_id": self.event_id,
                "junction_id": self.junction_id,
                "direction": self.direction,
                "event_type": self.event_type,
                "vehicle_id": self.vehicle_id,
                "vehicle_type": self.vehicle_type,
                "sequence_no": self.sequence_no,
                "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class WaitingVehicle:
    junction_id: str
    vehicle_id: str
    direction: Direction
    vehicle_type: VehicleType
    arrived_at: datetime
    sensor_timestamp: datetime
    arrival_event_id: str
    sequence_no: int


@dataclass(frozen=True)
class EventDecision:
    outcome: EventOutcome
    code: str
    message: str
    change: QueueChange = QueueChange.NONE
    advance_sequence: bool = False


def evaluate_vehicle_event(
    event: SensorEvent,
    waiting: WaitingVehicle | None,
    last_sequence: int | None,
    received_at: datetime,
) -> EventDecision:
    if (event.timestamp - received_at).total_seconds() > 30:
        return EventDecision(
            EventOutcome.REJECTED, "FUTURE_TIMESTAMP", "Sensor time is over 30 seconds ahead."
        )
    if (
        event.event_type == SensorEventType.VEHICLE_ARRIVED
        and event.vehicle_type == VehicleType.EMERGENCY
        and (received_at - event.timestamp).total_seconds() > 120
    ):
        return EventDecision(
            EventOutcome.REJECTED, "STALE_EMERGENCY", "Emergency detection is over 120 seconds old."
        )
    if last_sequence is not None and event.sequence_no <= last_sequence:
        return EventDecision(
            EventOutcome.IGNORED, "STALE_SEQUENCE", "Sequence number has already been superseded."
        )

    if event.event_type == SensorEventType.VEHICLE_ARRIVED:
        if waiting is not None:
            return EventDecision(
                EventOutcome.REJECTED,
                "VEHICLE_ALREADY_QUEUED",
                "This vehicle is already waiting at the junction.",
                advance_sequence=True,
            )
        return EventDecision(
            EventOutcome.ACCEPTED,
            "VEHICLE_ARRIVED",
            "Vehicle added to the queue.",
            QueueChange.ADD,
            advance_sequence=True,
        )

    if waiting is None:
        code, message = "VEHICLE_NOT_QUEUED", "No matching arrival exists for this vehicle."
    elif waiting.direction != event.direction:
        code, message = (
            "DIRECTION_MISMATCH",
            "Clearance direction differs from the waiting vehicle.",
        )
    elif event.vehicle_type is not None and event.vehicle_type != waiting.vehicle_type:
        code, message = "VEHICLE_TYPE_MISMATCH", "Clearance type differs from the waiting vehicle."
    else:
        return EventDecision(
            EventOutcome.ACCEPTED,
            "VEHICLE_CLEARED",
            "Vehicle removed from the queue.",
            QueueChange.REMOVE,
            advance_sequence=True,
        )
    return EventDecision(EventOutcome.REJECTED, code, message, advance_sequence=True)
