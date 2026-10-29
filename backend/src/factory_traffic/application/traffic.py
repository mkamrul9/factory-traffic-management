import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock

from factory_traffic.application.clock import MonotonicClock
from factory_traffic.application.control import ControlCoordinator
from factory_traffic.application.ports import (
    AuditEntry,
    ControllerPort,
    EventReceipt,
    JunctionRecord,
    JunctionSnapshot,
    OperationResult,
    TrafficRepository,
    TrafficTransaction,
)
from factory_traffic.controllers.rest_simulator import RestControllerSimulator
from factory_traffic.domain.controller import (
    ControllerAcknowledgement,
    ControllerCommand,
    DeviceEvent,
    DeviceStatus,
    OperatorCommand,
)
from factory_traffic.domain.events import (
    EventOutcome,
    QueueChange,
    SensorEvent,
    WaitingVehicle,
    evaluate_vehicle_event,
)
from factory_traffic.domain.models import DEFAULT_JUNCTION, Direction, JunctionConfiguration

logger = logging.getLogger(__name__)


class JunctionNotFound(Exception):
    pass


class JunctionAlreadyExists(Exception):
    pass


class TrafficService:
    def __init__(
        self,
        repository: TrafficRepository,
        clock: Callable[[], datetime] | None = None,
        controller: ControllerPort | None = None,
    ) -> None:
        self.repository = repository
        self.clock = MonotonicClock() if clock is None else clock
        self.controller = RestControllerSimulator() if controller is None else controller
        self.control = ControlCoordinator()
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
                self._add_junction(transaction, DEFAULT_JUNCTION, initialize_control=False)
            for record in transaction.list_junctions():
                self.control.initialize(transaction, record, self.clock())

    def _add_junction(
        self,
        transaction: TrafficTransaction,
        configuration: JunctionConfiguration,
        *,
        initialize_control: bool = True,
    ) -> JunctionRecord:
        now = self.clock()
        record = JunctionRecord(configuration, now)
        transaction.add_junction(record)
        transaction.add_audit(AuditEntry(configuration.id, "JUNCTION_CREATED", now, {}))
        if initialize_control:
            self.control.initialize(transaction, record, now)
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
            if receipt.outcome == EventOutcome.ACCEPTED:
                record = self._require_junction(transaction, event.junction_id)
                self.control.advance(transaction, record, now)
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

    def get_snapshot(self, junction_id: str) -> JunctionSnapshot:
        with self.repository.transaction(read_only=True) as transaction:
            record = self._require_junction(transaction, junction_id)
            return JunctionSnapshot(
                configuration=record.configuration,
                runtime=self.control.runtime(transaction, junction_id),
                waiting=transaction.list_waiting(junction_id),
                pending_commands=transaction.list_commands(junction_id, pending_only=True),
                sequence_numbers={
                    direction: transaction.get_sequence(junction_id, direction)
                    for direction in Direction
                },
                timestamp=self.clock(),
            )

    def pending_commands(self, junction_id: str) -> list[ControllerCommand]:
        with self.repository.transaction(read_only=True) as transaction:
            self._require_junction(transaction, junction_id)
            return transaction.list_commands(junction_id, pending_only=True)

    def execute_command(
        self, junction_id: str, command: OperatorCommand, direction: Direction | None = None
    ) -> OperationResult:
        with self._lock_for(junction_id), self.repository.transaction() as transaction:
            record = self._require_junction(transaction, junction_id)
            now = self.clock()
            runtime = self.control.advance(transaction, record, now)
            return self.control.command(transaction, record, runtime, command, direction, now)

    def process_acknowledgement(self, event: ControllerAcknowledgement) -> OperationResult:
        with self._lock_for(event.junction_id), self.repository.transaction() as transaction:
            record = self._require_junction(transaction, event.junction_id)
            now = self.clock()
            runtime = self.control.advance(transaction, record, now)
            _, result = self.control.acknowledge(transaction, record, runtime, event, now)
            return result

    def process_device_event(self, event: DeviceEvent) -> EventReceipt:
        with self._lock_for(event.junction_id), self.repository.transaction() as transaction:
            return self.control.device_event(
                transaction, transaction.get_junction(event.junction_id), event, self.clock()
            )

    def configure_simulator(self, junction_id: str, enabled: bool) -> OperationResult:
        with self._lock_for(junction_id), self.repository.transaction() as transaction:
            record = self._require_junction(transaction, junction_id)
            now = self.clock()
            runtime = self.control.advance(transaction, record, now)
            if enabled and runtime.blocked_device():
                return OperationResult(
                    junction_id,
                    EventOutcome.REJECTED,
                    "DEVICE_UNAVAILABLE",
                    "Bring unavailable devices ONLINE before enabling simulation.",
                    409,
                )
            if enabled and runtime.fault_code is not None and not runtime.recovering:
                runtime, _ = self.control.recover(transaction, record, runtime, now)
            transaction.save_runtime(replace(runtime, simulator_auto_ack=enabled))
            self.control.audit(
                transaction, junction_id, "SIMULATOR_CONFIGURED", now, {"auto_ack": enabled}
            )
            return OperationResult(
                junction_id,
                EventOutcome.ACCEPTED,
                "SIMULATOR_CONFIGURED",
                "Automatic simulated acknowledgements enabled."
                if enabled
                else "Automatic simulated acknowledgements disabled.",
            )

    def simulate_batch(self, junction_id: str, batch_id: str) -> OperationResult:
        # Read committed outbox records before interacting with any controller adapter.
        with self.repository.transaction(read_only=True) as transaction:
            self._require_junction(transaction, junction_id)
            runtime = self.control.runtime(transaction, junction_id)
            if runtime.batch_id != batch_id:
                return OperationResult(
                    junction_id,
                    EventOutcome.REJECTED,
                    "STALE_BATCH",
                    "Batch is no longer current.",
                    409,
                )
            if runtime.controller_status == DeviceStatus.OFFLINE or any(
                status != DeviceStatus.ONLINE for status in runtime.signals.values()
            ):
                return OperationResult(
                    junction_id,
                    EventOutcome.REJECTED,
                    "DEVICE_UNAVAILABLE",
                    "Unavailable signals or controller cannot be simulated ONLINE.",
                    409,
                )
            commands = transaction.list_commands(junction_id, batch_id=batch_id, pending_only=True)
        if not commands:
            return OperationResult(
                junction_id,
                EventOutcome.IGNORED,
                "NO_PENDING_COMMANDS",
                "This batch has no pending commands.",
                duplicate=True,
            )
        acknowledgements = self.controller.execute(commands, self.clock())
        with self._lock_for(junction_id), self.repository.transaction() as transaction:
            record = self._require_junction(transaction, junction_id)
            now = self.clock()
            runtime = self.control.advance(transaction, record, now)
            result = OperationResult(
                junction_id,
                EventOutcome.ACCEPTED,
                "COMMANDS_DISPATCHED",
                "Commands dispatched; awaiting controller evidence.",
            )
            for acknowledgement in acknowledgements:
                runtime, current = self.control.acknowledge(
                    transaction, record, runtime, acknowledgement, now
                )
                if result.outcome != EventOutcome.REJECTED:
                    result = current
            self.control.audit(
                transaction,
                junction_id,
                "SIMULATOR_EXECUTED",
                now,
                {"batch_id": batch_id, "command_count": len(commands)},
            )
            return result

    def tick_all(self) -> None:
        for record in self.list_junctions():
            junction_id = record.configuration.id
            with self._lock_for(junction_id), self.repository.transaction() as transaction:
                current = self._require_junction(transaction, junction_id)
                runtime = self.control.advance(transaction, current, self.clock())
                simulate = (
                    runtime.simulator_auto_ack and runtime.controller_status != DeviceStatus.OFFLINE
                )
                simulate = simulate and all(
                    status == DeviceStatus.ONLINE for status in runtime.signals.values()
                )
                batch_id = runtime.batch_id
                pending = transaction.list_commands(junction_id, pending_only=True)
            if simulate and batch_id is not None and pending:
                self.simulate_batch(junction_id, batch_id)
