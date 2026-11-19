"""
Database connection and management.
"""

import sqlite3
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Database:
    """
    SQLite database connection manager.
    """

    def __init__(self, db_path: Path | str, migrations_dir: Path | str = "migrations") -> None:
        """
        Initialize database.

        Args:
            db_path: Path to SQLite database file
            migrations_dir: Path to migrations directory
        """
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir)
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Connect to database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for transactions."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def migrate(self) -> None:
        """Apply pending migrations."""
        if not self._connection:
            self.connect()
        assert self._connection is not None

        try:
            cursor = self._connection.execute(
                "SELECT MAX(version) as version FROM schema_migrations"
            )
            row = cursor.fetchone()
            current_version = int(row["version"]) if row and row["version"] else 0
        except sqlite3.OperationalError:
            current_version = 0

        migrations = sorted(self.migrations_dir.glob("*.sql"))

        for migration_file in migrations:
            version_str = migration_file.stem.split("_")[0]
            version = int(version_str)

            if version > current_version:
                with open(migration_file, encoding="utf-8") as f:
                    migration_sql = f.read()

                self._connection.executescript(migration_sql)
                self._connection.execute("PRAGMA foreign_keys = ON")
                self._connection.commit()
                current_version = version

    def execute(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
    ) -> sqlite3.Cursor:
        """Execute SQL statement."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        return self._connection.execute(sql, parameters or [])

    def execute_many(
        self,
        sql: str,
        parameters: Iterable[Sequence[Any]],
    ) -> sqlite3.Cursor:
        """Execute SQL statement with multiple parameter sets."""
        if not self._connection:
            raise RuntimeError("Database not connected")

        return self._connection.executemany(sql, parameters)
