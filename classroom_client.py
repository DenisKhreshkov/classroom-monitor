"""Read-only Classroom v1 REST client with OAuth, pagination and bounded reads."""

import logging
from datetime import datetime

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from errors import ProviderError
from google_authentication import TimedRequest
from models import BERLIN, Assignment

log = logging.getLogger(__name__)


class GoogleAPI:
    def __init__(self, credentials, session=None):
        self.session = session or AuthorizedSession(credentials, auth_request=TimedRequest())
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

    def get(self, url: str, *, params=None, binary: bool = False, max_bytes=5_000_000):
        try:
            with self.session.get(
                url, params=params, timeout=(10, 45), stream=True, allow_redirects=False
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ProviderError(
                        f"Google API returned HTTP {response.status_code}; check account permissions and enabled APIs."
                    )
                content = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise ProviderError(
                            "Google material exceeds the configured safe download limit."
                        )
                if binary:
                    return bytes(content)
                import json

                return json.loads(content)
        except (requests.RequestException, GoogleAuthError, ValueError):
            raise ProviderError(
                "Google API request failed or returned invalid data. Check network and authorization."
            ) from None


class ClassroomClient:
    BASE = "https://classroom.googleapis.com/v1"

    def __init__(self, api: GoogleAPI):
        self.api = api
        self.failures = 0

    def pages(self, path: str, key: str, *, params=None):
        token = None
        seen = set()
        while True:
            page_params = {"pageSize": 100, **(params or {})}
            if token:
                page_params["pageToken"] = token
            data = self.api.get(self.BASE + path, params=page_params)
            yield from data.get(key, [])
            token = data.get("nextPageToken")
            if not token:
                break
            if token in seen:
                raise ProviderError(
                    "Google returned a repeated pagination token; synchronization is incomplete."
                )
            seen.add(token)

    def assignments(self, *, now: datetime | None = None):
        current = now or datetime.now(BERLIN)
        self.failures = 0
        scanned = skipped = 0
        for course in self.pages(
            "/courses",
            "courses",
            params={
                "courseStates": ["ACTIVE"],
                "fields": "nextPageToken,courses(id,name,courseState)",
            },
        ):
            if course.get("courseState", "ACTIVE") != "ACTIVE":
                continue
            try:
                path = f"/courses/{course['id']}/courseWork"
                # Classroom has no creation/due-date predicate. Read only date
                # metadata for old items; get full content only after the filter.
                for raw in self.pages(
                    path,
                    "courseWork",
                    params={
                        "courseWorkStates": ["PUBLISHED"],
                        "orderBy": "updateTime desc",
                        "fields": "nextPageToken,courseWork(id,state,creationTime,scheduledTime,dueDate,dueTime)",
                    },
                ):
                    scanned += 1
                    assignment = Assignment(
                        str(course["id"]),
                        course.get("name", "Unnamed course"),
                        str(raw["id"]),
                        raw.get("title", "Untitled"),
                        raw,
                        course_state=course.get("courseState", "ACTIVE"),
                    )
                    if not assignment.eligible_for_analysis(current):
                        skipped += 1
                        continue
                    try:
                        full = self.api.get(self.BASE + path + f"/{assignment.id}")
                    except ProviderError as exc:
                        self.failures += 1
                        log.warning("Could not read one coursework item: %s", exc)
                        continue
                    assignment.raw = full
                    assignment.title = full.get("title", "Untitled")
                    # Metadata may change between list and get.
                    if not assignment.eligible_for_analysis(current):
                        skipped += 1
                        continue
                    yield assignment
            except ProviderError as exc:
                self.failures += 1
                log.warning("Could not read one course: %s", exc)
        log.info(
            "Classroom date filter: %d metadata items checked; %d excluded before full analysis.",
            scanned,
            skipped,
        )
