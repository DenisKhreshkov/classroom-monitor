"""Private GitHub branch snapshots, checkpointed BEFORE and AFTER delivery.

Uses Git Data API so state has no artifact expiry and no OAuth tokens enter Git.
Non-forced ref updates reject concurrent divergent writers.
"""

import base64
import hashlib
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from errors import StateError


class LocalState:
    def restore(self, path):
        # SQLite already persists locally; Database validates it before any use.
        return path.exists()

    def save(self, database):
        database.snapshot()


class GitHubState:
    def __init__(
        self,
        repository: str,
        token: str,
        branch="classroom-monitor-state",
        session=None,
        *,
        allow_initialize=False,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise StateError("Invalid GitHub repository name.")
        if branch != "classroom-monitor-state":
            raise StateError("Refusing to write to a source-code branch.")
        self.base = f"https://api.github.com/repos/{repository}"
        self.branch = branch
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self.session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=3,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                    raise_on_status=False,
                    backoff_max=30,
                    respect_retry_after_header=False,
                )
            ),
        )
        self.head = None
        self.tree = None
        self.digest = None
        self.restored = False
        self.allow_initialize = allow_initialize

    def api(self, method, path, payload=None, missing_ok=False):
        try:
            response = self.session.request(
                method, self.base + path, json=payload, timeout=(10, 60), allow_redirects=False
            )
            if missing_ok and response.status_code == 404:
                return None
            if not 200 <= response.status_code < 300:
                raise StateError(
                    f"GitHub state HTTP {response.status_code}. Check Contents write permission and branch rules; no unsafe resend will occur."
                )
            return response.json()
        except (requests.RequestException, ValueError):
            raise StateError(
                "GitHub state request failed. Stopping to protect notification history."
            ) from None

    def restore(self, path):
        repo = self.api("GET", "")
        if not repo.get("private"):
            raise StateError("State contains school information. Use a PRIVATE GitHub repository.")
        if repo.get("default_branch") == self.branch:
            raise StateError("The state branch must not be the default source-code branch.")
        ref = self.api("GET", f"/git/ref/heads/{self.branch}", missing_ok=True)
        if ref is None:
            if not self.allow_initialize:
                raise StateError(
                    "Remote state branch is missing. For first-time setup only, run initialize-state. If it existed before, restore its history instead."
                )
            if path.exists():
                raise StateError(
                    "Remote state is missing but local state exists; resolve this conflict manually."
                )
            self.restored = True
            return
        self.head = ref["object"]["sha"]
        self.tree = self.api("GET", f"/git/commits/{self.head}")["tree"]["sha"]
        entries = self.api("GET", f"/git/trees/{self.tree}")["tree"]
        entry = next(
            (
                item
                for item in entries
                if item["path"] == "classroom.sqlite3" and item["type"] == "blob"
            ),
            None,
        )
        if entry is None:
            raise StateError(
                "Existing state branch has no database; restore its history before continuing."
            )
        blob = self.api("GET", f"/git/blobs/{entry['sha']}")
        try:
            data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        except (ValueError, KeyError):
            raise StateError(
                "GitHub state blob is invalid. Restore an earlier state commit."
            ) from None
        if not data.startswith(b"SQLite format 3\x00"):
            raise StateError("GitHub state is not a SQLite database; refusing a fresh start.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".restoring")
        temp.write_bytes(data)
        temp.replace(path)
        self.digest = hashlib.sha256(data).hexdigest()
        self.restored = True

    def save(self, database):
        if not self.restored:
            raise StateError("Remote state must be restored before saving.")
        data = database.snapshot()
        digest = hashlib.sha256(data).hexdigest()
        if digest == self.digest:
            return
        if len(data) > 25_000_000:
            raise StateError(
                "Database exceeds the 25 MB state limit. Archive old state before continuing."
            )
        blob = self.api(
            "POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        )
        tree_payload = {
            "tree": [
                {"path": "classroom.sqlite3", "mode": "100644", "type": "blob", "sha": blob["sha"]}
            ]
        }
        if self.tree:
            tree_payload["base_tree"] = self.tree
        tree = self.api("POST", "/git/trees", tree_payload)
        commit = self.api(
            "POST",
            "/git/commits",
            {
                "message": "Checkpoint classroom monitor state",
                "tree": tree["sha"],
                "author": {
                    "name": "Classroom Monitor",
                    "email": "classroom-monitor@users.noreply.github.com",
                },
                "parents": [self.head] if self.head else [],
            },
        )
        if self.head:
            self.api(
                "PATCH", f"/git/refs/heads/{self.branch}", {"sha": commit["sha"], "force": False}
            )
        else:
            self.api(
                "POST", "/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": commit["sha"]}
            )
        self.head, self.tree, self.digest = commit["sha"], tree["sha"], digest
