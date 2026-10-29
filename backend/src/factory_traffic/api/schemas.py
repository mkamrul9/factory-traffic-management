from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from factory_traffic.application.ports import EventReceipt, JunctionRecord
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
    mode: Literal["DEGRADED"] = "DEGRADED"
    phase: Literal["ALL_RED"] = "ALL_RED"
    control_active: Literal[False] = False
    controller_status: Literal["UNKNOWN"] = "UNKNOWN"
    desired_signals: dict[Direction, SignalColor]
    actual_signals: dict[Direction, Literal["UNKNOWN"]]
    queues: dict[Direction, int]
    queued_vehicles: list[WaitingVehicleResponse]
    emergency_directions: list[Direction]
    alerts: list[AlertResponse]


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
