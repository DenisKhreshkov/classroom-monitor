import base64
from unittest.mock import Mock

import pytest
import requests

from database import Database
from errors import StateError
from state_store import GitHubState


class GitServer:
    """In-memory Git Data API double; never makes external requests."""

    def __init__(self):
        self.private = True
        self.head = None
        self.objects = {}
        self.calls = []
        self.session = Mock()
        self.session.request.side_effect = self.request

    def request(self, method, url, json=None, **kwargs):
        path = url.split("/repos/owner/repo", 1)[1]
        self.calls.append((method, path, json))
        status = 200
        data = {}
        if path == "":
            data = {"private": self.private, "default_branch": "main"}
        elif method == "GET" and path.startswith("/git/ref/"):
            if self.head:
                data = {"object": {"sha": self.head}}
            else:
                status = 404
        elif method == "GET":
            data = self.objects[path.rsplit("/", 1)[1]]
        elif method == "POST" and path in {"/git/blobs", "/git/trees", "/git/commits"}:
            key = "sha" + str(len(self.objects))
            self.objects[key] = dict(json)
            if path == "/git/commits":
                self.objects[key]["tree"] = {"sha": json["tree"]}
            data = {"sha": key}
        elif path == "/git/refs" and method == "POST":
            if self.head:
                status = 422
            else:
                self.head = json["sha"]
        elif path.startswith("/git/refs/") and method == "PATCH":
            if self.objects[json["sha"]]["parents"] != [self.head] or json["force"]:
                status = 422
            else:
                self.head = json["sha"]
        else:
            raise AssertionError((method, path))
        response = Mock(status_code=status)
        response.json.return_value = data
        return response

    def client(self):
        return GitHubState(
            "owner/repo", "not-a-real-token", session=self.session, allow_initialize=True
        )


def test_remote_first_save_restore_and_noop(database, tmp_path):
    server = GitServer()
    client = server.client()
    client.restore(tmp_path / "fresh.sqlite3")
    database.enqueue("key", "body")
    client.save(database)
    first_head = server.head
    client.save(database)
    assert server.head == first_head
    restored_client = server.client()
    restored_path = tmp_path / "restored.sqlite3"
    restored_client.restore(restored_path)
    restored = Database(restored_path)
    assert restored.notification("key")["body"] == "body"
    restored.finish("key", "sent", "42")
    restored_client.save(restored)
    assert server.head != first_head
    assert server.objects[server.head]["parents"] == [first_head]
    restored.close()


def test_conflicting_writer_cannot_overwrite_newer_state(database, tmp_path):
    server = GitServer()
    first = server.client()
    first.restore(tmp_path / "initial.sqlite3")
    first.save(database)
    stale = server.client()
    stale_path = tmp_path / "stale.sqlite3"
    stale.restore(stale_path)
    stale_db = Database(stale_path)
    database.enqueue("new", "fresh state")
    first.save(database)
    current_head = server.head
    stale_db.enqueue("old", "stale state")
    with pytest.raises(StateError, match="422"):
        stale.save(stale_db)
    assert server.head == current_head
    stale_db.close()


def test_public_repository_rejected(tmp_path):
    server = GitServer()
    server.private = False
    with pytest.raises(StateError, match="PRIVATE"):
        server.client().restore(tmp_path / "db")


def test_corrupt_remote_blob_never_resets_history(database, tmp_path):
    server = GitServer()
    client = server.client()
    client.restore(tmp_path / "initial.sqlite3")
    client.save(database)
    tree = server.objects[server.objects[server.head]["tree"]["sha"]]
    blob_sha = tree["tree"][0]["sha"]
    server.objects[blob_sha]["content"] = base64.b64encode(b"bad data").decode()
    with pytest.raises(StateError, match="not a SQLite"):
        server.client().restore(tmp_path / "restore.sqlite3")


def test_restore_failure_never_means_empty_database(tmp_path):
    session = Mock()
    session.request.side_effect = requests.Timeout("SECRET")
    client = GitHubState("owner/repo", "token", session=session)
    with pytest.raises(StateError) as exc:
        client.restore(tmp_path / "db")
    assert "SECRET" not in str(exc.value)
    assert client.restored is False


def test_missing_database_on_existing_branch_is_error(database, tmp_path):
    server = GitServer()
    client = server.client()
    client.restore(tmp_path / "initial.sqlite3")
    client.save(database)
    server.objects[server.objects[server.head]["tree"]["sha"]]["tree"] = []
    with pytest.raises(StateError, match="no database"):
        server.client().restore(tmp_path / "restore.sqlite3")


def test_refuses_source_branch_and_save_without_restore(database):
    with pytest.raises(StateError, match="source-code"):
        GitHubState("owner/repo", "token", branch="main")
    with pytest.raises(StateError, match="restored"):
        GitHubState("owner/repo", "token").save(database)


def test_missing_state_requires_explicit_first_time_initialization(tmp_path):
    server = GitServer()
    client = GitHubState("owner/repo", "token", session=server.session)
    with pytest.raises(StateError, match="first-time"):
        client.restore(tmp_path / "fresh.sqlite3")
    assert client.restored is False
