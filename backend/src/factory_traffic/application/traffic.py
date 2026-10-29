from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock

from factory_traffic.application.ports import (
    AuditEntry,
    EventReceipt,
    JunctionRecord,
    TrafficRepository,
    TrafficTransaction,
)
from factory_traffic.domain.events import (
    EventOutcome,
    QueueChange,
    SensorEvent,
    WaitingVehicle,
    evaluate_vehicle_event,
)
from factory_traffic.domain.models import DEFAULT_JUNCTION, JunctionConfiguration


class JunctionNotFound(Exception):
    pass


class JunctionAlreadyExists(Exception):
    pass


class TrafficService:
    def __init__(
        self,
        repository: TrafficRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.clock = clock
        self._registry_lock = Lock()
        self._junction_locks: dict[str, Lock] = {}

    def _lock_for(self, junction_id: str) -> Lock:
        with self._registry_lock:
            if junction_id not in self._junction_locks:
                self._junction_locks[junction_id] = Lock()
            return self._junction_locks[junction_id]

    def initialize(self) -> None:
        self.repository.initialize()
        with self.repository.transaction() as transaction:
            if transaction.get_junction(DEFAULT_JUNCTION.id) is None:
                self._add_junction(transaction, DEFAULT_JUNCTION)

    def _add_junction(
        self, transaction: TrafficTransaction, configuration: JunctionConfiguration
    ) -> JunctionRecord:
        now = self.clock()
        record = JunctionRecord(configuration, now)
        transaction.add_junction(record)
        transaction.add_audit(AuditEntry(configuration.id, "JUNCTION_CREATED", now, {}))
        return record

    def create_junction(self, configuration: JunctionConfiguration) -> JunctionRecord:
        with self._lock_for(configuration.id), self.repository.transaction() as transaction:
            if transaction.get_junction(configuration.id) is not None:
                raise JunctionAlreadyExists(configuration.id)
            return self._add_junction(transaction, configuration)

    def list_junctions(self) -> list[JunctionRecord]:
        with self.repository.transaction(read_only=True) as transaction:
            return transaction.list_junctions()

    def get_junction(self, junction_id: str) -> JunctionRecord:
        with self.repository.transaction(read_only=True) as transaction:
            return self._require_junction(transaction, junction_id)

    @staticmethod
    def _require_junction(transaction: TrafficTransaction, junction_id: str) -> JunctionRecord:
        record = transaction.get_junction(junction_id)
        if record is None:
            raise JunctionNotFound(junction_id)
        return record

    def get_waiting(self, junction_id: str) -> list[WaitingVehicle]:
        with self.repository.transaction(read_only=True) as transaction:
            self._require_junction(transaction, junction_id)
            return transaction.list_waiting(junction_id)

    def get_history(
        self, junction_id: str, limit: int, before_id: int | None
    ) -> tuple[list[AuditEntry], int | None]:
        with self.repository.transaction(read_only=True) as transaction:
            self._require_junction(transaction, junction_id)
            entries = transaction.list_history(junction_id, limit + 1, before_id)
        more = len(entries) > limit
        page = entries[:limit]
        return page, page[-1].id if more else None

    def process_sensor_event(self, event: SensorEvent) -> EventReceipt:
        # SQLite also locks the write transaction, protecting global event IDs across junctions.
        with self._lock_for(event.junction_id), self.repository.transaction() as transaction:
            now = self.clock()
            existing = transaction.get_receipt(event.event_id)
            if existing is not None:
                if existing.payload == event.canonical_payload():
                    transaction.add_audit(
                        AuditEntry(
                            event.junction_id,
                            "DUPLICATE_SENSOR_EVENT",
                            now,
                            {"original_outcome": existing.outcome, "code": existing.code},
                            event.event_id,
                        )
                    )
                    return replace(existing, duplicate=True)
                receipt = EventReceipt(
                    event.event_id,
                    event.junction_id,
                    event.canonical_payload(),
                    EventOutcome.REJECTED,
                    "EVENT_ID_CONFLICT",
                    "This event ID was already used with different content.",
                    now,
                    status_code=409,
                )
                self._audit_receipt(transaction, event, receipt)
                return receipt

            if transaction.get_junction(event.junction_id) is None:
                receipt = EventReceipt(
                    event.event_id,
                    event.junction_id,
                    event.canonical_payload(),
                    EventOutcome.REJECTED,
                    "UNKNOWN_JUNCTION",
                    "The junction does not exist.",
                    now,
                    status_code=404,
                )
            else:
                waiting = transaction.find_vehicle(event.junction_id, event.vehicle_id)
                sequence = transaction.get_sequence(event.junction_id, event.direction)
                decision = evaluate_vehicle_event(event, waiting, sequence, now)
                if decision.change == QueueChange.ADD:
                    if event.vehicle_type is None:
                        raise ValueError("An accepted arrival must have a vehicle type.")
                    transaction.add_vehicle(
                        WaitingVehicle(
                            event.junction_id,
                            event.vehicle_id,
                            event.direction,
                            event.vehicle_type,
                            now,
                            event.timestamp,
                            event.event_id,
                            event.sequence_no,
                        )
                    )
                elif decision.change == QueueChange.REMOVE:
                    transaction.remove_vehicle(event.junction_id, event.vehicle_id)
                if decision.advance_sequence:
                    transaction.set_sequence(event.junction_id, event.direction, event.sequence_no)
                receipt = EventReceipt(
                    event.event_id,
                    event.junction_id,
                    event.canonical_payload(),
                    decision.outcome,
                    decision.code,
                    decision.message,
                    now,
                    status_code=409 if decision.outcome == EventOutcome.REJECTED else 200,
                )
            transaction.add_receipt(receipt)
            self._audit_receipt(transaction, event, receipt)
            return receipt

    @staticmethod
    def _audit_receipt(
        transaction: TrafficTransaction, event: SensorEvent, receipt: EventReceipt
    ) -> None:
        event_type = event.event_type.value
        if receipt.outcome != EventOutcome.ACCEPTED:
            event_type = f"SENSOR_EVENT_{receipt.outcome}"
        transaction.add_audit(
            AuditEntry(
                event.junction_id,
                event_type,
                receipt.received_at,
                {
                    "code": receipt.code,
                    "message": receipt.message,
                    "direction": event.direction,
                    "vehicle_id": event.vehicle_id,
                    "vehicle_type": event.vehicle_type,
                    "sequence_no": event.sequence_no,
                    "sensor_timestamp": event.timestamp.astimezone(UTC).isoformat(),
                },
                event.event_id,
            )
        )

    def audit_invalid_event(
        self, junction_id: str | None, event_id: str | None, errors: list[dict[str, object]]
    ) -> None:
        with self.repository.transaction() as transaction:
            transaction.add_audit(
                AuditEntry(
                    junction_id,
                    "SENSOR_EVENT_REJECTED",
                    self.clock(),
                    {"code": "VALIDATION_ERROR", "errors": errors},
                    event_id,
                )
            )
