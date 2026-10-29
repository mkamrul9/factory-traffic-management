from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from factory_traffic.domain.events import EventOutcome, WaitingVehicle
from factory_traffic.domain.models import Direction, JunctionConfiguration


class StorageUnavailable(Exception):
    """The persistence adapter could not complete an operation."""


@dataclass(frozen=True)
class JunctionRecord:
    configuration: JunctionConfiguration
    created_at: datetime


@dataclass(frozen=True)
class EventReceipt:
    event_id: str
    junction_id: str
    payload: str
    outcome: EventOutcome
    code: str
    message: str
    received_at: datetime
    status_code: int = 200
    duplicate: bool = False


@dataclass(frozen=True)
class AuditEntry:
    junction_id: str | None
    event_type: str
    timestamp: datetime
    details: dict[str, object]
    event_id: str | None = None
    id: int = 0


class TrafficTransaction(Protocol):
    def get_junction(self, junction_id: str) -> JunctionRecord | None: ...
    def list_junctions(self) -> list[JunctionRecord]: ...
    def add_junction(self, junction: JunctionRecord) -> None: ...
    def list_waiting(self, junction_id: str) -> list[WaitingVehicle]: ...
    def find_vehicle(self, junction_id: str, vehicle_id: str) -> WaitingVehicle | None: ...
    def add_vehicle(self, vehicle: WaitingVehicle) -> None: ...
    def remove_vehicle(self, junction_id: str, vehicle_id: str) -> None: ...
    def get_receipt(self, event_id: str) -> EventReceipt | None: ...
    def add_receipt(self, receipt: EventReceipt) -> None: ...
    def get_sequence(self, junction_id: str, direction: Direction) -> int | None: ...
    def set_sequence(self, junction_id: str, direction: Direction, sequence_no: int) -> None: ...
    def add_audit(self, entry: AuditEntry) -> None: ...
    def list_history(
        self, junction_id: str, limit: int, before_id: int | None
    ) -> list[AuditEntry]: ...


class TrafficRepository(Protocol):
    def initialize(self) -> None: ...
    def transaction(
        self, *, read_only: bool = False
    ) -> AbstractContextManager[TrafficTransaction]: ...
