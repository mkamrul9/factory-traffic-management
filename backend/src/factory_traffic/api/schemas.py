from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from factory_traffic.application.ports import EventReceipt, JunctionRecord
from factory_traffic.domain.controller import (
    AcknowledgementStatus,
    CommandStatus,
    ControllerAcknowledgement,
    DeviceEvent,
    DeviceStatus,
    DeviceType,
    OperatorCommand,
)
from factory_traffic.domain.engine import SignalStage
from factory_traffic.domain.events import EventOutcome, SensorEvent, SensorEventType, VehicleType
from factory_traffic.domain.models import (
    Direction,
    JunctionConfiguration,
    SignalColor,
    SignalTimings,
)

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
]
JunctionId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
]
PositiveSeconds = Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimingsRequest(InputModel):
    green_seconds: PositiveSeconds = 30.0
    yellow_seconds: PositiveSeconds = 5.0
    all_red_seconds: PositiveSeconds = 2.0
    acknowledgement_timeout_seconds: PositiveSeconds = 3.0


class JunctionCreateRequest(InputModel):
    junction_id: JunctionId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    timings: TimingsRequest = Field(default_factory=TimingsRequest)

    def to_domain(self) -> JunctionConfiguration:
        return JunctionConfiguration(
            id=self.junction_id, name=self.name, timings=SignalTimings(**self.timings.model_dump())
        )


class PhaseResponse(BaseModel):
    id: str
    directions: tuple[Direction, ...]


class JunctionResponse(BaseModel):
    junction_id: str
    name: str
    phases: list[PhaseResponse]
    timings: TimingsRequest
    created_at: datetime

    @classmethod
    def from_record(cls, record: JunctionRecord) -> Self:
        configuration = record.configuration
        return cls(
            junction_id=configuration.id,
            name=configuration.name,
            phases=[PhaseResponse(id=p.id, directions=p.directions) for p in configuration.phases],
            timings=TimingsRequest(
                green_seconds=configuration.timings.green_seconds,
                yellow_seconds=configuration.timings.yellow_seconds,
                all_red_seconds=configuration.timings.all_red_seconds,
                acknowledgement_timeout_seconds=configuration.timings.acknowledgement_timeout_seconds,
            ),
            created_at=record.created_at,
        )


class SensorEventRequest(InputModel):
    event_id: Identifier
    junction_id: JunctionId
    direction: Direction
    event_type: SensorEventType
    vehicle_id: Identifier
    vehicle_type: VehicleType | None = None
    sequence_no: Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
    timestamp: AwareDatetime

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value: object) -> object:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Use an ISO 8601 timestamp including time and timezone.")
        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise ValueError("Timestamp cannot be represented in UTC.") from error

    @model_validator(mode="after")
    def require_arrival_type(self) -> Self:
        if self.event_type == SensorEventType.VEHICLE_ARRIVED and self.vehicle_type is None:
            raise ValueError("vehicle_type is required for VEHICLE_ARRIVED.")
        return self

    def to_domain(self) -> SensorEvent:
        return SensorEvent(**self.model_dump())


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    junction_id: str
    outcome: EventOutcome
    code: str
    message: str
    received_at: datetime
    duplicate: bool

    @classmethod
    def from_receipt(cls, receipt: EventReceipt) -> Self:
        return cls.model_validate(receipt)


class WaitingVehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: str
    direction: Direction
    vehicle_type: VehicleType
    arrived_at: datetime
    sensor_timestamp: datetime
    arrival_event_id: str
    sequence_no: int


class AlertResponse(BaseModel):
    code: str
    message: str


class JunctionStatusResponse(BaseModel):
    junction_id: str
    mode: str
    phase: str
    stage: SignalStage
    target_phase_id: str | None
    control_active: bool
    controller_status: DeviceStatus
    desired_signals: dict[Direction, SignalColor]
    actual_signals: dict[Direction, SignalColor | Literal["UNKNOWN"]]
    queues: dict[Direction, int]
    queued_vehicles: list[WaitingVehicleResponse]
    emergency_directions: list[Direction]
    alerts: list[AlertResponse]
    awaiting_confirmation: bool
    next_transition_at: datetime | None
    manual_expires_at: datetime | None
    batch_id: str | None
    pending_commands: list["ControllerCommandResponse"]
    simulator_auto_ack: bool
    sensor_statuses: dict[Direction, DeviceStatus]
    signal_statuses: dict[Direction, DeviceStatus]
    sequence_numbers: dict[Direction, int | None]
    server_time: datetime


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    junction_id: str | None
    event_type: str
    event_id: str | None
    timestamp: datetime
    details: dict[str, object]


class HistoryResponse(BaseModel):
    items: list[AuditEntryResponse]
    next_before_id: int | None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ControllerCommandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    command_id: str
    junction_id: str
    batch_id: str
    epoch: str
    signal_version: int
    direction: Direction
    requested_state: SignalColor
    requested_at: datetime
    deadline: datetime
    status: CommandStatus
    actual_state: SignalColor | None
    acknowledged_at: datetime | None


class OperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    junction_id: str
    outcome: EventOutcome
    code: str
    message: str
    duplicate: bool


class OperatorCommandRequest(InputModel):
    command: OperatorCommand
    direction: Direction | None = None

    @model_validator(mode="after")
    def validate_direction(self) -> Self:
        if self.command == OperatorCommand.MANUAL_GREEN_REQUEST and self.direction is None:
            raise ValueError("MANUAL_GREEN_REQUEST requires a direction.")
        if self.command != OperatorCommand.MANUAL_GREEN_REQUEST and self.direction is not None:
            raise ValueError("Only MANUAL_GREEN_REQUEST takes a direction.")
        return self


class ControllerEventRequest(InputModel):
    command_id: Identifier
    junction_id: JunctionId
    status: AcknowledgementStatus
    actual_state: SignalColor | None = None
    timestamp: AwareDatetime | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_timestamp_string(cls, value: object) -> object:
        return None if value is None else SensorEventRequest.require_iso_timestamp(value)

    @field_validator("timestamp")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else SensorEventRequest.normalize_timestamp(value)

    @model_validator(mode="after")
    def require_actual_state(self) -> Self:
        if self.status == AcknowledgementStatus.ACK and self.actual_state is None:
            raise ValueError("ACK requires actual_state.")
        return self

    def to_domain(self) -> ControllerAcknowledgement:
        return ControllerAcknowledgement(**self.model_dump())


class DeviceEventRequest(InputModel):
    event_id: Identifier
    junction_id: JunctionId
    device_type: DeviceType
    status: DeviceStatus
    direction: Direction | None = None
    timestamp: AwareDatetime

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_timestamp_string(cls, value: object) -> object:
        return SensorEventRequest.require_iso_timestamp(value)

    @field_validator("timestamp")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return SensorEventRequest.normalize_timestamp(value)

    @model_validator(mode="after")
    def require_device_direction(self) -> Self:
        if self.device_type != DeviceType.SIGNAL_CONTROLLER and self.direction is None:
            raise ValueError("A signal or sensor event requires a direction.")
        return self

    def to_domain(self) -> DeviceEvent:
        return DeviceEvent(**self.model_dump())


class SimulatorSettingsRequest(InputModel):
    auto_ack: StrictBool


class SimulatorBatchRequest(InputModel):
    batch_id: Identifier
