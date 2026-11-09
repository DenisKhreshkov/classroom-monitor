"""SQLite records and a durable notification outbox."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from errors import StateError
from models import Analysis


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() and any(self.path.parent.glob(self.path.name + ".quarantine-*")):
            raise StateError(
                "A quarantined database exists. Restore a backup before initializing new state."
            )
        self.connection = None
        try:
            if self.path.exists():
                with self.path.open("rb") as source:
                    if source.read(16) != b"SQLite format 3\x00":
                        raise sqlite3.DatabaseError("Invalid database header")
            self.connection = sqlite3.connect(self.path, timeout=30)
            self.connection.row_factory = sqlite3.Row
            if self.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Integrity check failed")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise StateError("Unsupported database version. Restore a compatible backup.")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS assignments (
                    course_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
                    course_name TEXT NOT NULL, title TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, analysis TEXT NOT NULL,
                    last_processed_at TEXT NOT NULL,
                    PRIMARY KEY (course_id, assignment_id)
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    key TEXT PRIMARY KEY, body TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','uncertain')),
                    updated_at TEXT NOT NULL, telegram_message_id TEXT
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            self.connection.commit()
        except StateError:
            if self.connection:
                self.connection.close()
            raise
        except sqlite3.OperationalError:
            if self.connection:
                self.connection.close()
            raise StateError(
                "Database could not be opened or updated. Check file permissions and running processes."
            ) from None
        except sqlite3.DatabaseError:
            if self.connection:
                self.connection.close()
            quarantine = self.path.with_name(
                self.path.name + ".quarantine-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
            )
            self.path.rename(quarantine)
            raise StateError(
                "Database is corrupt; preserved as a quarantine file. Restore a backup before sending notifications."
            ) from None

    def get_assignment(self, course_id: str, assignment_id: str):
        return self.connection.execute(
            "SELECT * FROM assignments WHERE course_id=? AND assignment_id=?",
            (course_id, assignment_id),
        ).fetchone()

    def cached_analysis(self, assignment, fingerprint: str) -> Analysis | None:
        row = self.get_assignment(assignment.course_id, assignment.id)
        if row and row["fingerprint"] == fingerprint:
            return Analysis.model_validate_json(row["analysis"])
        return None

    def store_assignment(self, assignment, fingerprint: str, analysis: Analysis):
        with self.connection:
            self.connection.execute(
                """INSERT INTO assignments VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(course_id,assignment_id) DO UPDATE SET
                course_name=excluded.course_name,title=excluded.title,fingerprint=excluded.fingerprint,
                analysis=excluded.analysis,last_processed_at=excluded.last_processed_at""",
                (
                    assignment.course_id,
                    assignment.id,
                    assignment.course_name,
                    assignment.title,
                    fingerprint,
                    analysis.model_dump_json(),
                    timestamp(),
                ),
            )

    def enqueue(self, key: str, body: str):
        with self.connection:
            self.connection.execute(
                """INSERT INTO notifications(key,body,status,updated_at)
                VALUES (?,?,'pending',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body
                WHERE notifications.status='pending'""",
                (key, body, timestamp()),
            )

    def notification(self, key: str):
        return self.connection.execute("SELECT * FROM notifications WHERE key=?", (key,)).fetchone()

    def claim(self, key: str) -> bool:
        with self.connection:
            result = self.connection.execute(
                "UPDATE notifications SET status='sending',updated_at=? WHERE key=? AND status='pending'",
                (timestamp(), key),
            )
        return result.rowcount == 1

    def finish(self, key: str, status: str, message_id: str | None = None):
        if status not in {"pending", "sent", "uncertain"}:
            raise ValueError("Invalid notification state")
        with self.connection:
            self.connection.execute(
                "UPDATE notifications SET status=?,updated_at=?,telegram_message_id=? WHERE key=?",
                (status, timestamp(), message_id, key),
            )

    def recover_interrupted(self):
        with self.connection:
            self.connection.execute(
                "UPDATE notifications SET status='uncertain',updated_at=? WHERE status='sending'",
                (timestamp(),),
            )

    def uncertain(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT key,status,updated_at FROM notifications WHERE status='uncertain'"
            )
        ]

    def set_metadata(self, key: str, value: str):
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def snapshot(self) -> bytes:
        # No WAL mode: a committed file is a complete, durable snapshot.
        self.connection.commit()
        return self.path.read_bytes()

    def close(self):
        self.connection.close()
