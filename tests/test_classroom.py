from datetime import datetime
from unittest.mock import Mock

import pytest
import requests

from classroom_client import ClassroomClient, GoogleAPI
from errors import ProviderError
from models import BERLIN, Assignment

NOW = datetime(2026, 11, 9, 16, 0, tzinfo=BERLIN)


def recent_work(item_id, **fields):
    return {"id": item_id, "creationTime": "2026-11-08T15:00:00Z", **fields}


def test_course_and_work_pagination():
    api = Mock()
    api.get.side_effect = [
        {"courses": [{"id": "c1", "name": "Math"}], "nextPageToken": "courses2"},
        {"courseWork": [recent_work("a")], "nextPageToken": "work2"},
        recent_work("a", title="First"),
        {"courseWork": [recent_work("b")]},
        recent_work("b", title="Second"),
        {"courses": [{"id": "c2", "name": "Science"}]},
        {"courseWork": [recent_work("c", state="DRAFT"), recent_work("d", state="PUBLISHED")]},
        recent_work("d", title="Third", state="PUBLISHED"),
    ]
    client = ClassroomClient(api)
    assert [a.id for a in client.assignments(now=NOW)] == ["a", "b", "d"]
    calls = api.get.call_args_list
    assert calls[3].kwargs["params"]["pageToken"] == "work2"
    assert calls[5].kwargs["params"]["pageToken"] == "courses2"
    assert calls[0].kwargs["params"]["courseStates"] == ["ACTIVE"]
    assert calls[5].kwargs["params"]["courseStates"] == ["ACTIVE"]
    assert calls[3].kwargs["params"]["courseWorkStates"] == ["PUBLISHED"]
    assert "description" not in calls[1].kwargs["params"]["fields"]


def test_course_permission_error_continues_other_courses():
    api = Mock()
    api.get.side_effect = [
        {"courses": [{"id": "bad"}, {"id": "good"}]},
        ProviderError("No access"),
        {"courseWork": [recent_work("ok")]},
        recent_work("ok"),
    ]
    client = ClassroomClient(api)
    assert [a.id for a in client.assignments(now=NOW)] == ["ok"]
    assert client.failures == 1


def test_repeated_pagination_token_fails():
    api = Mock()
    api.get.return_value = {"nextPageToken": "same"}
    with pytest.raises(ProviderError, match="repeated"):
        list(ClassroomClient(api).pages("/courses", "courses"))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"creationTime": "2026-11-08T15:00:00Z"}, True),
        ({"creationTime": "2026-11-02T15:00:00Z"}, True),
        ({"creationTime": "2026-11-02T14:59:59Z"}, False),
        ({"creationTime": "2026-11-02T16:00:00+01:00"}, True),
        ({"creationTime": "2026-10-01T00:00:00Z", "scheduledTime": "2026-11-08T15:00:00Z"}, True),
        ({"creationTime": "2026-10-01T00:00:00Z", "updateTime": "2026-11-09T15:00:00Z"}, False),
        ({"scheduledTime": "2026-11-10T15:00:00Z"}, False),
        ({"creationTime": "2026-11-10T15:00:00Z"}, False),
        ({}, False),
        ({"creationTime": "not-a-date"}, False),
        ({"creationTime": "2026-11-08T15:00:00"}, False),
        ({"dueDate": {"year": 2026, "month": 11, "day": 20}}, True),
        (
            {
                "creationTime": "2020-01-01T00:00:00Z",
                "dueDate": {"year": 2026, "month": 11, "day": 20},
            },
            True,
        ),
        ({"dueDate": {"year": 2026, "month": 11, "day": 9}}, True),
        ({"dueDate": {"year": 2026, "month": 11, "day": 8}}, False),
        ({"dueDate": {"year": 2026, "month": 11, "day": 9}, "dueTime": {"hours": 14}}, False),
        ({"dueDate": {"year": 2026, "month": 11, "day": 9}, "dueTime": {"hours": 15}}, True),
        (
            {
                "creationTime": "2026-11-08T00:00:00Z",
                "dueDate": {"year": 2026, "month": 11, "day": 1},
            },
            True,
        ),
        ({"dueDate": {"year": 2026, "month": 2, "day": 30}}, False),
        ({"state": "DRAFT", "creationTime": "2026-11-08T00:00:00Z"}, False),
        ({"state": "DELETED", "dueDate": {"year": 2026, "month": 11, "day": 20}}, False),
    ],
)
def test_recent_or_open_deadline_filter(raw, expected):
    assignment = Assignment("course", "Course", "work", "Title", raw)
    assert assignment.eligible_for_analysis(NOW) is expected


def test_archived_course_is_excluded_even_with_future_deadline():
    assignment = Assignment(
        "course",
        "Course",
        "work",
        "Title",
        {
            "creationTime": "2026-11-08T00:00:00Z",
            "dueDate": {"year": 2026, "month": 11, "day": 20},
        },
        course_state="ARCHIVED",
    )
    assert not assignment.eligible_for_analysis(NOW)


def test_only_eligible_items_get_full_details_and_archived_courses_are_not_queried():
    api = Mock()
    upcoming = {
        "id": "future",
        "creationTime": "2020-01-01T00:00:00Z",
        "dueDate": {"year": 2026, "month": 11, "day": 20},
    }
    api.get.side_effect = [
        {
            "courses": [
                {"id": "archived", "courseState": "ARCHIVED"},
                {"id": "active", "courseState": "ACTIVE"},
            ]
        },
        {
            "courseWork": [
                {"id": "old", "creationTime": "2020-01-01T00:00:00Z"},
                {"id": "overdue", "dueDate": {"year": 2026, "month": 1, "day": 1}},
                recent_work("new"),
                upcoming,
            ]
        },
        recent_work("new", description="Recent full content"),
        upcoming | {"description": "Old but still due"},
    ]
    result = list(ClassroomClient(api).assignments(now=NOW))
    assert [item.id for item in result] == ["new", "future"]
    assert result[0].raw["description"] == "Recent full content"
    urls = [call.args[0] for call in api.get.call_args_list]
    assert len(urls) == 4
    assert not any(
        "archived" in url or url.endswith("/old") or url.endswith("/overdue") for url in urls
    )


def test_metadata_permission_error_for_one_item_does_not_drop_remaining_items():
    api = Mock()
    api.get.side_effect = [
        {"courses": [{"id": "active"}]},
        {"courseWork": [recent_work("bad"), recent_work("good")]},
        ProviderError("Permission denied"),
        recent_work("good"),
    ]
    client = ClassroomClient(api)
    assert [item.id for item in client.assignments(now=NOW)] == ["good"]
    assert client.failures == 1


def test_expired_or_deleted_details_are_rechecked_after_metadata_list():
    api = Mock()
    api.get.side_effect = [
        {"courses": [{"id": "active"}]},
        {"courseWork": [recent_work("changed")]},
        recent_work("changed", state="DELETED"),
    ]
    assert list(ClassroomClient(api).assignments(now=NOW)) == []


def test_google_request_timeout_and_safe_errors():
    session = Mock()
    session.get.side_effect = requests.Timeout("SECRET")
    with pytest.raises(ProviderError) as exc:
        GoogleAPI(None, session).get("https://classroom.googleapis.com/v1/courses")
    assert "SECRET" not in str(exc.value)
    assert session.get.call_args.kwargs["timeout"] == (10, 45)


def test_google_stream_limit():
    session = Mock()
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"a" * 100]
    session.get.return_value.__enter__ = Mock(return_value=response)
    session.get.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(ProviderError, match="limit"):
        GoogleAPI(None, session).get("https://www.googleapis.com/drive/v3/files/file", max_bytes=50)


@pytest.mark.parametrize("month,hour,label", [(1, 15, "CET"), (7, 16, "CEST")])
def test_utc_due_time_converts_across_dst(month, hour, label):
    a = Assignment(
        "c",
        "Course",
        "a",
        "Title",
        {"dueDate": {"year": 2026, "month": month, "day": 20}, "dueTime": {"hours": 14}},
    )
    assert a.due_at.hour == hour
    assert a.due_at.tzname() == label
