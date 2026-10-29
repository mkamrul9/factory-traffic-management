import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from importlib.resources import files
from pathlib import Path

from factory_traffic.application.ports import StorageUnavailable, TrafficTransaction
from factory_traffic.persistence.sql_transaction import SQLCursor, SQLTransaction


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        # Each SQLite transaction closes its own connection.
        return

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


class SQLiteTransaction(SQLTransaction):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> SQLCursor:
        return self.connection.execute(statement, parameters)
