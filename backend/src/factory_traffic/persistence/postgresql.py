"""PostgreSQL storage for a persistent hosted demo without a local disk."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from factory_traffic.application.ports import StorageUnavailable, TrafficTransaction
from factory_traffic.persistence.sql_transaction import SQLCursor, SQLTransaction

# One database-wide write lock preserves the existing global event-ID transaction boundary.
_LOCK_NAMESPACE = 734291
_WRITE_LOCK = 1


class PostgreSQLRepository:
    def __init__(self, database_url: str) -> None:
        self.pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] = ConnectionPool(
            database_url,
            min_size=1,
            max_size=6,
            timeout=10,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 10},
        )

    def initialize(self) -> None:
        try:
            self.pool.open(wait=True, timeout=15)
            with self.pool.connection() as connection, connection.transaction():
                connection.execute("SET LOCAL lock_timeout = '10s'")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NAMESPACE, _WRITE_LOCK)
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                migrations = files("factory_traffic.persistence").joinpath("postgresql_migrations")
                for migration in sorted(migrations.iterdir(), key=lambda entry: entry.name):
                    if not migration.name.endswith(".sql"):
                        continue
                    version = int(migration.name.split("_", 1)[0])
                    applied = connection.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %s", (version,)
                    ).fetchone()
                    if applied is None:
                        connection.execute(migration.read_text(encoding="utf-8"))
                        connection.execute(
                            "INSERT INTO schema_migrations VALUES (%s, %s)",
                            (version, datetime.now(UTC).isoformat()),
                        )
        except (psycopg.Error, PoolTimeout, OSError) as error:
            raise StorageUnavailable("Database initialization failed.") from error

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def transaction(self, *, read_only: bool = False) -> Iterator[TrafficTransaction]:
        try:
            with self.pool.connection() as connection, connection.transaction():
                if read_only:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL lock_timeout = '10s'")
                connection.execute("SET LOCAL statement_timeout = '20s'")
                if not read_only:
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NAMESPACE, _WRITE_LOCK)
                    )
                yield PostgreSQLTransaction(connection)
        except (psycopg.Error, PoolTimeout) as error:
            raise StorageUnavailable("Database transaction failed.") from error


class PostgreSQLTransaction(SQLTransaction):
    def __init__(self, connection: psycopg.Connection[dict[str, Any]]) -> None:
        self.connection = connection

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> SQLCursor:
        # The shared fixed SQL uses qmark bindings; data is always passed separately.
        return self.connection.execute(statement.replace("?", "%s"), parameters)
