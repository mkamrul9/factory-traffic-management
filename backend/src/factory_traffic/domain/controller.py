"""Controller messages and runtime observations, without transport-specific code."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from factory_traffic.domain.engine import EngineState
from factory_traffic.domain.models import Direction, SignalColor


class DeviceStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class DeviceType(StrEnum):
    SIGNAL_CONTROLLER = "SIGNAL_CONTROLLER"
    SENSOR = "SENSOR"
    SIGNAL = "SIGNAL"


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    INTERRUPTED = "INTERRUPTED"


class AcknowledgementStatus(StrEnum):
    ACK = "ACK"
    FAILED = "FAILED"


class OperatorCommand(StrEnum):
    MANUAL_GREEN_REQUEST = "MANUAL_GREEN_REQUEST"
    RETURN_TO_AUTOMATIC = "RETURN_TO_AUTOMATIC"
    RECOVER_CONTROLLER = "RECOVER_CONTROLLER"


@dataclass(frozen=True)
class ControllerCommand:
    command_id: str
    junction_id: str
    batch_id: str
    epoch: str
    signal_version: int
    direction: Direction
    requested_state: SignalColor
    requested_at: datetime
    deadline: datetime
    status: CommandStatus = CommandStatus.PENDING
    actual_state: SignalColor | None = None
    acknowledged_at: datetime | None = None


@dataclass(frozen=True)
class ControllerAcknowledgement:
    command_id: str
    junction_id: str
    status: AcknowledgementStatus
    actual_state: SignalColor | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class DeviceEvent:
    event_id: str
    junction_id: str
    device_type: DeviceType
    status: DeviceStatus
    timestamp: datetime
    direction: Direction | None = None

    @property
    def device_key(self) -> str:
        if self.device_type == DeviceType.SIGNAL_CONTROLLER:
            return "controller"
        return f"{self.device_type}:{self.direction}"

    def canonical_payload(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "junction_id": self.junction_id,
                "device_type": self.device_type,
                "status": self.status,
                "timestamp": self.timestamp.astimezone(UTC).isoformat(),
                "direction": self.direction,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class JunctionRuntime:
    engine: EngineState
    epoch: str
    actual_signals: dict[Direction, SignalColor | None]
    controller_status: DeviceStatus = DeviceStatus.UNKNOWN
    fault_code: str | None = "RECOVERING"
    fault_message: str | None = "Waiting for fresh ALL RED confirmation and clearance."
    recovering: bool = True
    batch_id: str | None = None
    simulator_auto_ack: bool = False
    sensors: dict[Direction, DeviceStatus] = field(
        default_factory=lambda: {direction: DeviceStatus.ONLINE for direction in Direction}
    )
    signals: dict[Direction, DeviceStatus] = field(
        default_factory=lambda: {direction: DeviceStatus.ONLINE for direction in Direction}
    )
    device_timestamps: dict[str, datetime] = field(default_factory=dict)

    def blocked_device(self) -> str | None:
        if self.controller_status == DeviceStatus.OFFLINE:
            return "CONTROLLER_OFFLINE"
        if any(status != DeviceStatus.ONLINE for status in self.signals.values()):
            return "SIGNAL_UNAVAILABLE"
        if any(status != DeviceStatus.ONLINE for status in self.sensors.values()):
            return "SENSOR_UNAVAILABLE"
        return None
