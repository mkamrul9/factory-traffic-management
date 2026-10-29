"""Transactional coordination of the pure engine, device state, and command outbox."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from factory_traffic.application.ports import (
    AuditEntry,
    EventReceipt,
    JunctionRecord,
    OperationResult,
    TrafficTransaction,
)
from factory_traffic.domain.controller import (
    AcknowledgementStatus,
    CommandStatus,
    ControllerAcknowledgement,
    ControllerCommand,
    DeviceEvent,
    DeviceStatus,
    DeviceType,
    JunctionRuntime,
    OperatorCommand,
)
from factory_traffic.domain.engine import (
    CommandRejected,
    ControlCommand,
    EngineResult,
    SignalRequest,
    TrafficEngine,
)
from factory_traffic.domain.events import EventOutcome
from factory_traffic.domain.models import Direction


class ControlCoordinator:
    @staticmethod
    def audit(
        transaction: TrafficTransaction,
        junction_id: str,
        kind: str,
        now: datetime,
        details: dict[str, object] | None = None,
    ) -> None:
        transaction.add_audit(AuditEntry(junction_id, kind, now, details or {}))

    @staticmethod
    def runtime(transaction: TrafficTransaction, junction_id: str) -> JunctionRuntime:
        runtime = transaction.get_runtime(junction_id)
        if runtime is None:
            raise RuntimeError(f"Control runtime missing for junction {junction_id}")
        return runtime

    @staticmethod
    def cancel_pending(
        transaction: TrafficTransaction, junction_id: str, status: CommandStatus
    ) -> None:
        for command in transaction.list_commands(junction_id, pending_only=True):
            transaction.save_command(replace(command, status=status))

    def apply(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord,
        runtime: JunctionRuntime,
        result: EngineResult,
        now: datetime,
    ) -> JunctionRuntime:
        junction_id = record.configuration.id
        updated = replace(runtime, engine=result.state)
        for event in result.events:
            self.audit(transaction, junction_id, event.event_type, now, event.details)
        if result.request is not None:
            self.cancel_pending(transaction, junction_id, CommandStatus.CANCELLED)
            batch_id = f"batch-{uuid4().hex}"
            deadline = now + timedelta(
                seconds=record.configuration.timings.acknowledgement_timeout_seconds
            )
            for direction, color in result.request.signals.items():
                command = ControllerCommand(
                    f"cmd-{uuid4().hex}",
                    junction_id,
                    batch_id,
                    updated.epoch,
                    result.request.version,
                    direction,
                    color,
                    now,
                    deadline,
                )
                transaction.save_command(command)
                self.audit(
                    transaction,
                    junction_id,
                    "CONTROLLER_COMMAND_QUEUED",
                    now,
                    {
                        "command_id": command.command_id,
                        "batch_id": batch_id,
                        "direction": direction,
                        "requested_state": color,
                        "deadline": deadline.isoformat(),
                    },
                )
            updated = replace(
                updated,
                batch_id=batch_id,
                actual_signals={direction: None for direction in Direction},
            )
        transaction.save_runtime(updated)
        return updated

    def initialize(
        self, transaction: TrafficTransaction, record: JunctionRecord, now: datetime
    ) -> JunctionRuntime:
        previous = transaction.get_runtime(record.configuration.id)
        self.cancel_pending(transaction, record.configuration.id, CommandStatus.INTERRUPTED)
        result = TrafficEngine(record.configuration).start(now)
        if previous is None:
            runtime = JunctionRuntime(result.state, uuid4().hex, {d: None for d in Direction})
        else:
            result = replace(result, state=replace(result.state, services=previous.engine.services))
            runtime = replace(
                previous,
                engine=result.state,
                epoch=uuid4().hex,
                controller_status=DeviceStatus.UNKNOWN,
                actual_signals={d: None for d in Direction},
                fault_code="RECOVERING",
                fault_message="Restart requires fresh ALL RED confirmation.",
                recovering=True,
                simulator_auto_ack=False,
                batch_id=None,
            )
        blocked = runtime.blocked_device()
        if blocked:
            runtime = replace(
                runtime,
                fault_code=blocked,
                fault_message="A device remains unavailable.",
                recovering=False,
            )
        self.audit(
            transaction,
            record.configuration.id,
            "RECOVERY_STARTED",
            now,
            {
                "reason": "BACKEND_RESTART" if previous else "INITIALIZATION",
                "epoch": runtime.epoch,
            },
        )
        return self.apply(transaction, record, runtime, result, now)

    def fail(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord,
        runtime: JunctionRuntime,
        now: datetime,
        code: str,
        message: str,
    ) -> JunctionRuntime:
        self.audit(
            transaction,
            record.configuration.id,
            "CONTROL_FAILURE",
            now,
            {"code": code, "message": message},
        )
        already_latched = runtime.fault_code is not None and not runtime.recovering
        runtime = replace(
            runtime,
            fault_code=code,
            fault_message=message,
            recovering=False,
            actual_signals={direction: None for direction in Direction},
        )
        if already_latched:
            # Keep the best-effort RED commands in flight when another device reports a fault.
            transaction.save_runtime(runtime)
            return runtime
        self.cancel_pending(transaction, record.configuration.id, CommandStatus.CANCELLED)
        # Fail-safe RED is the only request allowed outside the normal engine transition path.
        result = TrafficEngine(record.configuration).start(now)
        state = replace(
            result.state,
            signal_version=runtime.engine.signal_version + 1,
            services=runtime.engine.services,
        )
        result = (
            replace(
                result,
                state=state,
                request=SignalRequest(state.signal_version, result.request.signals),
            )
            if result.request
            else result
        )
        return self.apply(transaction, record, runtime, result, now)

    def recover(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord,
        runtime: JunctionRuntime,
        now: datetime,
    ) -> tuple[JunctionRuntime, OperationResult]:
        junction_id = record.configuration.id
        if runtime.fault_code is None:
            return runtime, OperationResult(
                junction_id,
                EventOutcome.REJECTED,
                "RECOVERY_NOT_REQUIRED",
                "An operating junction cannot bypass transitions through recovery.",
                409,
            )
        if runtime.blocked_device():
            return runtime, OperationResult(
                junction_id,
                EventOutcome.REJECTED,
                "DEVICE_UNAVAILABLE",
                "Bring unavailable devices ONLINE before recovery.",
                409,
            )
        result = TrafficEngine(record.configuration).start(now)
        result = replace(result, state=replace(result.state, services=runtime.engine.services))
        runtime = replace(
            runtime,
            epoch=uuid4().hex,
            fault_code="RECOVERING",
            fault_message="Waiting for fresh ALL RED confirmation and clearance.",
            recovering=True,
        )
        self.audit(transaction, junction_id, "RECOVERY_STARTED", now, {"epoch": runtime.epoch})
        runtime = self.apply(transaction, record, runtime, result, now)
        return runtime, OperationResult(
            junction_id,
            EventOutcome.ACCEPTED,
            "RECOVERY_STARTED",
            "Confirm the new ALL RED batch, then allow clearance.",
        )

    def advance(
        self, transaction: TrafficTransaction, record: JunctionRecord, now: datetime
    ) -> JunctionRuntime:
        runtime = self.runtime(transaction, record.configuration.id)
        if now < runtime.engine.observed_at:
            return self.fail(
                transaction,
                record,
                runtime,
                runtime.engine.observed_at,
                "CLOCK_ROLLBACK",
                "Runtime time moved backwards; control is stopped.",
            )
        pending = transaction.list_commands(record.configuration.id, pending_only=True)
        expired = [command for command in pending if now >= command.deadline]
        if expired:
            for command in expired:
                transaction.save_command(replace(command, status=CommandStatus.TIMED_OUT))
            self.audit(
                transaction,
                record.configuration.id,
                "CONTROLLER_TIMEOUT",
                now,
                {
                    "command_ids": [command.command_id for command in expired],
                    "batch_id": runtime.batch_id,
                },
            )
            if runtime.fault_code is not None and not runtime.recovering:
                runtime = replace(runtime, actual_signals={d: None for d in Direction})
                transaction.save_runtime(runtime)
                return runtime
            return self.fail(
                transaction,
                record,
                runtime,
                now,
                "ACK_TIMEOUT",
                "Controller acknowledgement deadline expired.",
            )
        if runtime.fault_code is not None and not runtime.recovering:
            return runtime
        blocked = runtime.blocked_device()
        if blocked:
            return self.fail(
                transaction, record, runtime, now, blocked, "A required device is unavailable."
            )
        if runtime.recovering and not runtime.engine.awaiting_confirmation:
            deadline = runtime.engine.deadline
            if deadline is not None and now >= deadline:
                runtime = replace(runtime, fault_code=None, fault_message=None, recovering=False)
                self.audit(transaction, record.configuration.id, "CONTROLLER_RECOVERED", now)
        engine = TrafficEngine(record.configuration)
        waiting = transaction.list_waiting(record.configuration.id)
        return self.apply(
            transaction, record, runtime, engine.tick(runtime.engine, waiting, now), now
        )

    def acknowledge(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord,
        runtime: JunctionRuntime,
        event: ControllerAcknowledgement,
        now: datetime,
    ) -> tuple[JunctionRuntime, OperationResult]:
        junction_id = record.configuration.id
        command = transaction.get_command(event.command_id)
        code, message, status = "UNKNOWN_COMMAND", "Command does not exist for this junction.", 404
        if (
            command is not None
            and command.junction_id == junction_id
            and event.junction_id == junction_id
        ):
            if (
                command.status == CommandStatus.ACKNOWLEDGED
                and event.status == AcknowledgementStatus.ACK
                and event.actual_state == command.actual_state
            ):
                self.audit(
                    transaction,
                    junction_id,
                    "DUPLICATE_CONTROLLER_ACK",
                    now,
                    {"command_id": command.command_id},
                )
                return runtime, OperationResult(
                    junction_id,
                    EventOutcome.IGNORED,
                    "DUPLICATE_ACK",
                    "Acknowledgement already recorded.",
                    duplicate=True,
                )
            if (
                command.epoch != runtime.epoch
                or command.batch_id != runtime.batch_id
                or command.status != CommandStatus.PENDING
            ):
                code, message, status = (
                    "STALE_ACK",
                    "Command is no longer awaiting this acknowledgement.",
                    409,
                )
            elif (
                runtime.controller_status == DeviceStatus.OFFLINE
                or runtime.signals[command.direction] != DeviceStatus.ONLINE
            ):
                code, message, status = (
                    "DEVICE_UNAVAILABLE",
                    "An offline or unknown device cannot confirm signals.",
                    409,
                )
            elif (
                event.status == AcknowledgementStatus.FAILED
                or event.actual_state != command.requested_state
            ):
                transaction.save_command(
                    replace(
                        command,
                        status=CommandStatus.FAILED,
                        actual_state=event.actual_state,
                        acknowledged_at=now,
                    )
                )
                code = (
                    "COMMAND_FAILED"
                    if event.status == AcknowledgementStatus.FAILED
                    else "ACK_MISMATCH"
                )
                message = "Controller did not confirm the requested signal state."
                self.audit(
                    transaction,
                    junction_id,
                    "CONTROLLER_ACKNOWLEDGEMENT",
                    now,
                    {
                        "command_id": command.command_id,
                        "status": event.status,
                        "actual_state": event.actual_state,
                    },
                )
                runtime = self.fail(transaction, record, runtime, now, code, message)
                return runtime, OperationResult(
                    junction_id, EventOutcome.REJECTED, code, message, 409
                )
            else:
                transaction.save_command(
                    replace(
                        command,
                        status=CommandStatus.ACKNOWLEDGED,
                        actual_state=event.actual_state,
                        acknowledged_at=now,
                    )
                )
                actual = dict(runtime.actual_signals)
                actual[command.direction] = event.actual_state
                runtime = replace(
                    runtime, actual_signals=actual, controller_status=DeviceStatus.ONLINE
                )
                self.audit(
                    transaction,
                    junction_id,
                    "CONTROLLER_ACKNOWLEDGEMENT",
                    now,
                    {
                        "command_id": command.command_id,
                        "status": event.status,
                        "actual_state": event.actual_state,
                        "sensor_timestamp": event.timestamp.isoformat()
                        if event.timestamp
                        else None,
                    },
                )
                commands = transaction.list_commands(junction_id, batch_id=runtime.batch_id)
                if len(commands) == len(Direction) and all(
                    item.status == CommandStatus.ACKNOWLEDGED for item in commands
                ):
                    confirmed = {item.direction: item.requested_state for item in commands}
                    engine = TrafficEngine(record.configuration)
                    result = engine.confirm_signals(
                        runtime.engine,
                        command.signal_version,
                        confirmed,
                        transaction.list_waiting(junction_id),
                        now,
                    )
                    runtime = self.apply(transaction, record, runtime, result, now)
                else:
                    transaction.save_runtime(runtime)
                return runtime, OperationResult(
                    junction_id,
                    EventOutcome.ACCEPTED,
                    "ACK_RECORDED",
                    "Controller acknowledgement recorded.",
                )
        self.audit(
            transaction,
            junction_id,
            "CONTROLLER_ACK_REJECTED",
            now,
            {"command_id": event.command_id, "code": code},
        )
        return runtime, OperationResult(junction_id, EventOutcome.REJECTED, code, message, status)

    def command(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord,
        runtime: JunctionRuntime,
        command: OperatorCommand,
        direction: Direction | None,
        now: datetime,
    ) -> OperationResult:
        junction_id = record.configuration.id
        if command == OperatorCommand.RECOVER_CONTROLLER:
            _, result = self.recover(transaction, record, runtime, now)
            return result
        if runtime.fault_code is not None:
            result = OperationResult(
                junction_id,
                EventOutcome.REJECTED,
                "CONTROL_UNAVAILABLE",
                "Control is degraded or recovering; recover the controller first.",
                409,
            )
        else:
            try:
                engine = TrafficEngine(record.configuration)
                result_engine = engine.execute_command(
                    runtime.engine,
                    ControlCommand(command.value),
                    transaction.list_waiting(junction_id),
                    now,
                    direction,
                )
                self.apply(transaction, record, runtime, result_engine, now)
                return OperationResult(
                    junction_id, EventOutcome.ACCEPTED, command.value, "Operator intent accepted."
                )
            except CommandRejected as error:
                result = OperationResult(
                    junction_id, EventOutcome.REJECTED, error.code, str(error), 409
                )
        self.audit(
            transaction,
            junction_id,
            "OPERATOR_COMMAND_REJECTED",
            now,
            {"command": command, "code": result.code},
        )
        return result

    def device_event(
        self,
        transaction: TrafficTransaction,
        record: JunctionRecord | None,
        event: DeviceEvent,
        now: datetime,
    ) -> EventReceipt:
        existing = transaction.get_receipt(event.event_id)
        if existing is not None:
            if existing.payload == event.canonical_payload():
                self.audit(
                    transaction,
                    event.junction_id,
                    "DUPLICATE_DEVICE_EVENT",
                    now,
                    {"event_id": event.event_id},
                )
                return replace(existing, duplicate=True)
            receipt = EventReceipt(
                event.event_id,
                event.junction_id,
                event.canonical_payload(),
                EventOutcome.REJECTED,
                "EVENT_ID_CONFLICT",
                "Event ID already used with different content.",
                now,
                409,
            )
            self.audit(
                transaction,
                event.junction_id,
                "DEVICE_EVENT_REJECTED",
                now,
                {"event_id": event.event_id, "code": receipt.code},
            )
            return receipt
        outcome, code, message, status = (
            EventOutcome.ACCEPTED,
            "DEVICE_STATUS_RECORDED",
            "Device status recorded.",
            200,
        )
        if record is None:
            outcome, code, message, status = (
                EventOutcome.REJECTED,
                "UNKNOWN_JUNCTION",
                "The junction does not exist.",
                404,
            )
        elif (event.timestamp - now).total_seconds() > 30:
            outcome, code, message, status = (
                EventOutcome.REJECTED,
                "FUTURE_TIMESTAMP",
                "Device timestamp is over 30 seconds ahead.",
                409,
            )
        else:
            runtime = self.advance(transaction, record, now)
            previous_timestamp = runtime.device_timestamps.get(event.device_key)
            if previous_timestamp is not None and event.timestamp < previous_timestamp:
                outcome, code, message = (
                    EventOutcome.IGNORED,
                    "STALE_DEVICE_EVENT",
                    "A newer device report was already accepted.",
                )
            else:
                timestamps = {**runtime.device_timestamps, event.device_key: event.timestamp}
                if event.device_type == DeviceType.SIGNAL_CONTROLLER:
                    previous_status = runtime.controller_status
                    runtime = replace(
                        runtime, controller_status=event.status, device_timestamps=timestamps
                    )
                else:
                    if event.direction is None:
                        raise ValueError("Direction is required for a sensor or signal report.")
                    collection = (
                        runtime.sensors
                        if event.device_type == DeviceType.SENSOR
                        else runtime.signals
                    )
                    previous_status = collection[event.direction]
                    updated = {**collection, event.direction: event.status}
                    if event.device_type == DeviceType.SENSOR:
                        runtime = replace(runtime, device_timestamps=timestamps, sensors=updated)
                    else:
                        runtime = replace(runtime, device_timestamps=timestamps, signals=updated)
                transaction.save_runtime(runtime)
                if event.status != DeviceStatus.ONLINE:
                    fault = runtime.blocked_device() or "CONTROLLER_UNKNOWN"
                    self.fail(
                        transaction, record, runtime, now, fault, "A device reported unavailable."
                    )
                elif previous_status != DeviceStatus.ONLINE:
                    blocked = runtime.blocked_device()
                    if blocked is None:
                        self.recover(transaction, record, runtime, now)
                    else:
                        transaction.save_runtime(
                            replace(
                                runtime,
                                fault_code=blocked,
                                fault_message="Another required device remains unavailable.",
                            )
                        )
        receipt = EventReceipt(
            event.event_id,
            event.junction_id,
            event.canonical_payload(),
            outcome,
            code,
            message,
            now,
            status,
        )
        transaction.add_receipt(receipt)
        transaction.add_audit(
            AuditEntry(
                event.junction_id,
                "DEVICE_STATUS" if outcome == EventOutcome.ACCEPTED else "DEVICE_EVENT_REJECTED",
                now,
                {
                    "code": code,
                    "device_type": event.device_type,
                    "direction": event.direction,
                    "status": event.status,
                    "sensor_timestamp": event.timestamp.isoformat(),
                },
                event.event_id,
            )
        )
        return receipt
