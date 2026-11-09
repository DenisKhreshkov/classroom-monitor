import sqlite3

import pytest

from database import Database
from errors import StateError
from models import Assignment


def test_creation_and_round_trip(database, assignment, analysis):
    database.store_assignment(assignment, "fingerprint", analysis)
    row = database.get_assignment("course1", "work1")
    assert row["title"] == assignment.title
    assert row["last_processed_at"]
    assert database.cached_analysis(assignment, "fingerprint") == analysis
    assert database.cached_analysis(assignment, "changed") is None
    database.set_metadata("last_successful_sync", "now")
    assert database.get_metadata("last_successful_sync") == "now"


def test_composite_key_and_update(database, assignment, analysis):
    database.store_assignment(assignment, "a", analysis)
    other = Assignment("other-course", "Other", assignment.id, "Other title", {})
    database.store_assignment(other, "b", analysis)
    assert database.get_assignment(other.course_id, other.id)["title"] == "Other title"
    assignment.title = "Edited"
    database.store_assignment(assignment, "c", analysis)
    assert database.get_assignment(assignment.course_id, assignment.id)["title"] == "Edited"


def test_duplicate_and_recovery(database):
    database.enqueue("key", "first")
    database.enqueue("key", "updated")
    assert database.notification("key")["body"] == "updated"
    assert database.claim("key")
    assert not database.claim("key")
    database.recover_interrupted()
    assert database.notification("key")["status"] == "uncertain"
    assert database.uncertain()[0]["key"] == "key"
    database.finish("key", "sent", "123")
    database.enqueue("key", "cannot resend")
    assert database.notification("key")["status"] == "sent"
    assert not database.claim("key")


def test_snapshot_reopens(database, tmp_path):
    database.enqueue("a", "hello")
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(database.snapshot())
    restored = Database(copy)
    assert restored.notification("a")["status"] == "pending"
    restored.close()


def test_corrupt_database_is_quarantined_and_stops(tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"broken-not-sqlite")
    with pytest.raises(StateError, match="corrupt"):
        Database(path)
    assert not path.exists()
    assert next(tmp_path.glob("*.quarantine-*")).read_bytes() == b"broken-not-sqlite"
    with pytest.raises(StateError, match="quarantined"):
        Database(path)


def test_unsupported_schema_is_preserved(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(StateError, match="Unsupported"):
        Database(path)
    assert path.exists()
