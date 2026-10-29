import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import cast

from factory_traffic.application.ports import (
    AuditEntry,
    EventReceipt,
    JunctionRecord,
    StorageUnavailable,
    TrafficTransaction,
)
from factory_traffic.domain.controller import CommandStatus, ControllerCommand, JunctionRuntime
from factory_traffic.domain.events import EventOutcome, VehicleType, WaitingVehicle
from factory_traffic.domain.models import (
    Direction,
    JunctionConfiguration,
    SignalColor,
    SignalTimings,
    TrafficPhase,
)
from factory_traffic.persistence.runtime_json import decode_runtime, encode_runtime


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                migrations = files("factory_traffic.persistence").joinpath("migrations")
                for migration in sorted(migrations.iterdir(), key=lambda entry: entry.name):
                    if not migration.name.endswith(".sql"):
                        continue
                    version = int(migration.name.split("_", 1)[0])
                    # Take the lock before checking the version, including on simultaneous startups.
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        applied = connection.execute(
                            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                        ).fetchone()
                        if applied is None:
                            statement = ""
                            for line in migration.read_text(encoding="utf-8").splitlines(True):
                                statement += line
                                if sqlite3.complete_statement(statement):
                                    connection.execute(statement)
                                    statement = ""
                            if statement.strip():
                                raise ValueError(f"Incomplete migration: {migration.name}")
                            connection.execute(
                                "INSERT INTO schema_migrations VALUES "
                                "(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                                (version,),
                            )
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
        except (sqlite3.Error, OSError) as error:
            raise StorageUnavailable("Database initialization failed.") from error

    @contextmanager
    def transaction(self, *, read_only: bool = False) -> Iterator[TrafficTransaction]:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
                try:
                    yield SQLiteTransaction(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise StorageUnavailable("Database transaction failed.") from error


class SQLiteTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _junction(row: sqlite3.Row) -> JunctionRecord:
        data = json.loads(row["configuration_json"])
        configuration = JunctionConfiguration(
            id=data["id"],
            name=data["name"],
            phases=tuple(
                TrafficPhase(phase["id"], tuple(Direction(value) for value in phase["directions"]))
                for phase in data["phases"]
            ),
            timings=SignalTimings(**data["timings"]),
        )
        return JunctionRecord(configuration, datetime.fromisoformat(row["created_at"]))

    def get_junction(self, junction_id: str) -> JunctionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM junctions WHERE junction_id = ?", (junction_id,)
        ).fetchone()
        return None if row is None else self._junction(row)

    def list_junctions(self) -> list[JunctionRecord]:
        rows = self.connection.execute("SELECT * FROM junctions ORDER BY junction_id").fetchall()
        return [self._junction(row) for row in rows]

    def add_junction(self, junction: JunctionRecord) -> None:
        self.connection.execute(
            "INSERT INTO junctions (junction_id, configuration_json, created_at) VALUES (?, ?, ?)",
            (
                junction.configuration.id,
                json.dumps(asdict(junction.configuration)),
                junction.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _vehicle(row: sqlite3.Row) -> WaitingVehicle:
        return WaitingVehicle(
            junction_id=row["junction_id"],
            vehicle_id=row["vehicle_id"],
            direction=Direction(row["direction"]),
            vehicle_type=VehicleType(row["vehicle_type"]),
            arrived_at=datetime.fromisoformat(row["arrived_at"]),
            sensor_timestamp=datetime.fromisoformat(row["sensor_timestamp"]),
            arrival_event_id=row["arrival_event_id"],
            sequence_no=row["sequence_no"],
        )

    def list_waiting(self, junction_id: str) -> list[WaitingVehicle]:
        rows = self.connection.execute(
            "SELECT * FROM waiting_vehicles WHERE junction_id = ? ORDER BY arrived_at, vehicle_id",
            (junction_id,),
        ).fetchall()
        return [self._vehicle(row) for row in rows]

    def find_vehicle(self, junction_id: str, vehicle_id: str) -> WaitingVehicle | None:
        row = self.connection.execute(
            "SELECT * FROM waiting_vehicles WHERE junction_id = ? AND vehicle_id = ?",
            (junction_id, vehicle_id),
        ).fetchone()
        return None if row is None else self._vehicle(row)

    def add_vehicle(self, vehicle: WaitingVehicle) -> None:
        self.connection.execute(
            "INSERT INTO waiting_vehicles "
            "(junction_id, vehicle_id, direction, vehicle_type, arrived_at, sensor_timestamp, "
            "arrival_event_id, sequence_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                vehicle.junction_id,
                vehicle.vehicle_id,
                vehicle.direction,
                vehicle.vehicle_type,
                vehicle.arrived_at.isoformat(),
                vehicle.sensor_timestamp.isoformat(),
                vehicle.arrival_event_id,
                vehicle.sequence_no,
            ),
        )

    def remove_vehicle(self, junction_id: str, vehicle_id: str) -> None:
        self.connection.execute(
            "DELETE FROM waiting_vehicles WHERE junction_id = ? AND vehicle_id = ?",
            (junction_id, vehicle_id),
        )

    def get_receipt(self, event_id: str) -> EventReceipt | None:
        row = self.connection.execute(
            "SELECT * FROM event_receipts WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return EventReceipt(
            event_id=row["event_id"],
            junction_id=row["junction_id"],
            payload=row["payload_json"],
            outcome=EventOutcome(row["outcome"]),
            code=row["code"],
            message=row["message"],
            received_at=datetime.fromisoformat(row["received_at"]),
            status_code=row["status_code"],
        )

    def add_receipt(self, receipt: EventReceipt) -> None:
        self.connection.execute(
            "INSERT INTO event_receipts "
            "(event_id, junction_id, payload_json, outcome, code, message, "
            "received_at, status_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.event_id,
                receipt.junction_id,
                receipt.payload,
                receipt.outcome,
                receipt.code,
                receipt.message,
                receipt.received_at.isoformat(),
                receipt.status_code,
            ),
        )

    def get_sequence(self, junction_id: str, direction: Direction) -> int | None:
        row = self.connection.execute(
            "SELECT sequence_no FROM sequence_cursors WHERE junction_id = ? AND direction = ?",
            (junction_id, direction),
        ).fetchone()
        return None if row is None else int(row["sequence_no"])

    def set_sequence(self, junction_id: str, direction: Direction, sequence_no: int) -> None:
        self.connection.execute(
            "INSERT INTO sequence_cursors (junction_id, direction, sequence_no) VALUES (?, ?, ?) "
            "ON CONFLICT(junction_id, direction) DO UPDATE SET sequence_no = "
            "MAX(sequence_cursors.sequence_no, excluded.sequence_no)",
            (junction_id, direction, sequence_no),
        )

    def add_audit(self, entry: AuditEntry) -> None:
        self.connection.execute(
            "INSERT INTO audit_history "
            "(junction_id, event_type, event_id, timestamp, details_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                entry.junction_id,
                entry.event_type,
                entry.event_id,
                entry.timestamp.isoformat(),
                json.dumps(entry.details),
            ),
        )

    def list_history(self, junction_id: str, limit: int, before_id: int | None) -> list[AuditEntry]:
        rows = self.connection.execute(
            "SELECT * FROM audit_history WHERE junction_id = ? "
            "AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
            (junction_id, before_id, before_id, limit),
        ).fetchall()
        return [
            AuditEntry(
                junction_id=row["junction_id"],
                event_type=row["event_type"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                details=cast(dict[str, object], json.loads(row["details_json"])),
                event_id=row["event_id"],
                id=row["id"],
            )
            for row in rows
        ]

    def get_runtime(self, junction_id: str) -> JunctionRuntime | None:
        row = self.connection.execute(
            "SELECT state_json FROM junction_runtime WHERE junction_id = ?", (junction_id,)
        ).fetchone()
        return None if row is None else decode_runtime(row["state_json"])

    def save_runtime(self, runtime: JunctionRuntime) -> None:
        self.connection.execute(
            "INSERT INTO junction_runtime (junction_id, state_json) VALUES (?, ?) "
            "ON CONFLICT(junction_id) DO UPDATE SET state_json = excluded.state_json",
            (runtime.engine.junction_id, encode_runtime(runtime)),
        )

    @staticmethod
    def _command(row: sqlite3.Row) -> ControllerCommand:
        return ControllerCommand(
            command_id=row["command_id"],
            junction_id=row["junction_id"],
            batch_id=row["batch_id"],
            epoch=row["epoch"],
            signal_version=row["signal_version"],
            direction=Direction(row["direction"]),
            requested_state=SignalColor(row["requested_state"]),
            requested_at=datetime.fromisoformat(row["requested_at"]),
            deadline=datetime.fromisoformat(row["deadline"]),
            status=CommandStatus(row["status"]),
            actual_state=SignalColor(row["actual_state"]) if row["actual_state"] else None,
            acknowledged_at=datetime.fromisoformat(row["acknowledged_at"])
            if row["acknowledged_at"]
            else None,
        )

    def get_command(self, command_id: str) -> ControllerCommand | None:
        row = self.connection.execute(
            "SELECT * FROM controller_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        return None if row is None else self._command(row)

    def save_command(self, command: ControllerCommand) -> None:
        self.connection.execute(
            "INSERT INTO controller_commands "
            "(command_id, junction_id, batch_id, epoch, signal_version, direction, "
            "requested_state, "
            "requested_at, deadline, status, actual_state, acknowledged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(command_id) DO UPDATE SET status = excluded.status, "
            "actual_state = excluded.actual_state, acknowledged_at = excluded.acknowledged_at",
            (
                command.command_id,
                command.junction_id,
                command.batch_id,
                command.epoch,
                command.signal_version,
                command.direction,
                command.requested_state,
                command.requested_at.isoformat(),
                command.deadline.isoformat(),
                command.status,
                command.actual_state,
                command.acknowledged_at.isoformat() if command.acknowledged_at else None,
            ),
        )

    def list_commands(
        self, junction_id: str, *, batch_id: str | None = None, pending_only: bool = False
    ) -> list[ControllerCommand]:
        rows = self.connection.execute(
            "SELECT * FROM controller_commands WHERE junction_id = ? "
            "AND (? IS NULL OR batch_id = ?) AND (? = 0 OR status = 'PENDING') "
            "ORDER BY requested_at, direction",
            (junction_id, batch_id, batch_id, int(pending_only)),
        ).fetchall()
        return [self._command(row) for row in rows]
