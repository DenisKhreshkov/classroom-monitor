from dataclasses import replace
from datetime import date

import pytest

from errors import DeliveryUncertain, ProviderError
from main import main, relevant, run_monitor
from state_store import LocalState


def test_two_complete_runs_notify_once(config, database, services, today):
    for _ in range(2):
        assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    classroom, reader, analyzer, bot = services
    assert analyzer.analyze.call_count == 1
    assert bot.send.call_count == 1
    assert database.get_metadata("last_successful_sync")


def test_edit_after_notification_never_resends(config, database, services, assignment, today):
    run_monitor(config, database, LocalState(), *services, today=today)
    assignment.raw["description"] += " Bring a calculator."
    run_monitor(config, database, LocalState(), *services, today=today)
    assert services[2].analyze.call_count == 2
    assert services[3].send.call_count == 1


def test_homework_can_later_become_assessment(
    config, database, services, assignment, analysis, today
):
    services[2].analyze.return_value = analysis.model_copy(
        update={"category": "homework", "is_assessment": False}
    )
    run_monitor(config, database, LocalState(), *services, today=today)
    services[3].send.assert_not_called()
    assignment.raw["description"] = "New exam announcement"
    services[2].analyze.return_value = analysis
    run_monitor(config, database, LocalState(), *services, today=today)
    assert services[3].send.call_count == 1


def test_dry_run_then_live_run_still_sends(config, database, services, today):
    run_monitor(config, database, LocalState(), *services, dry_run=True, today=today)
    services[3].send.assert_not_called()
    assert database.get_metadata("last_successful_sync") is None
    run_monitor(config, database, LocalState(), *services, today=today)
    assert services[3].send.call_count == 1


@pytest.mark.parametrize("archived", [False, True])
def test_ineligible_coursework_never_reads_materials_or_calls_gemini(
    config, database, services, assignment, today, archived
):
    assignment.raw["creationTime"] = "2020-01-01T00:00:00Z"
    assignment.raw["dueDate"] = {"year": 2020, "month": 1, "day": 2}
    if archived:
        assignment.course_state = "ARCHIVED"
        assignment.raw["dueDate"] = {"year": 2026, "month": 11, "day": 20}
    assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    services[1].read.assert_not_called()
    services[2].analyze.assert_not_called()
    services[3].send.assert_not_called()
    assert database.get_assignment(assignment.course_id, assignment.id) is None


def test_old_future_deadline_still_reaches_gemini(config, database, services, assignment, today):
    assignment.raw["creationTime"] = "2020-01-01T00:00:00Z"
    assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    services[1].read.assert_called_once()
    services[2].analyze.assert_called_once()
    services[3].send.assert_called_once()


def test_new_no_deadline_work_is_eligible_before_gemini(
    config, database, services, assignment, today
):
    assignment.raw.pop("dueDate")
    assignment.raw.pop("dueTime")
    assignment.raw["creationTime"] = "2026-11-08T10:00:00Z"
    assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    services[1].read.assert_called_once()
    services[2].analyze.assert_called_once()


def test_cached_historical_item_cannot_bypass_filter(
    config, database, services, assignment, analysis, today
):
    assignment.raw.pop("dueDate")
    assignment.raw.pop("dueTime")
    assignment.raw["creationTime"] = "2020-01-01T00:00:00Z"
    database.store_assignment(assignment, assignment.fingerprint(config.model), analysis)
    assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    services[1].read.assert_not_called()
    services[2].analyze.assert_not_called()
    services[3].send.assert_not_called()


def test_summary_deduplicates_by_local_day(config, database, services, today):
    config = replace(config, daily_summary=True)
    services[0].assignments.return_value = []
    for _ in range(2):
        assert run_monitor(config, database, LocalState(), *services, today=today) == 0
    assert services[3].send.call_count == 1
    assert database.notification("summary:" + today.isoformat())["status"] == "sent"
    assert "Ежедневная сводка" in services[3].send.call_args.args[0]
    assert "Проверено заданий: 0" in services[3].send.call_args.args[0]


def test_analysis_failure_not_cached_or_reported_success(config, database, services, today):
    services[2].analyze.side_effect = ProviderError("AI unavailable")
    assert run_monitor(config, database, LocalState(), *services, today=today) == 1
    assert database.get_assignment("course1", "work1") is None
    assert database.get_metadata("last_successful_sync") is None
    assert services[3].send.call_count == 1  # one safe error alert
    assert "Ошибка проверки заданий" in services[3].send.call_args.args[0]


def test_gemini_deadline_failure_is_retried_next_run_without_duplicate_notifications(
    config, database, services, today, analysis, monkeypatch
):
    from unittest.mock import Mock

    from google.genai import errors, types

    from assignment_analyzer import AssignmentAnalyzer

    monkeypatch.setattr("assignment_analyzer.time.sleep", lambda _: None)
    client = Mock()
    timeout = errors.ServerError(
        504, {"error": {"status": "DEADLINE_EXCEEDED", "message": "SECRET"}}
    )
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason="STOP",
                content=types.Content(parts=[types.Part(text=analysis.model_dump_json())]),
            )
        ]
    )
    client.models.generate_content.side_effect = [timeout, timeout, timeout, response]
    classroom, reader, _, bot = services
    analyzer = AssignmentAnalyzer("fake", client=client)
    assert (
        run_monitor(config, database, LocalState(), classroom, reader, analyzer, bot, today=today)
        == 1
    )
    assert database.get_assignment("course1", "work1") is None
    assert database.get_metadata("last_successful_sync") is None
    assert (
        run_monitor(config, database, LocalState(), classroom, reader, analyzer, bot, today=today)
        == 0
    )
    assert database.get_assignment("course1", "work1") is not None
    assert bot.send.call_count == 2  # first-run error alert, then one assessment
    assert (
        run_monitor(config, database, LocalState(), classroom, reader, analyzer, bot, today=today)
        == 0
    )
    assert bot.send.call_count == 2
    assert client.models.generate_content.call_count == 4


def test_partial_course_failure_suppresses_success_summary(config, database, services, today):
    services[0].failures = 1
    services[0].assignments.return_value = []
    assert (
        run_monitor(
            replace(config, daily_summary=True), database, LocalState(), *services, today=today
        )
        == 1
    )
    assert database.notification("summary:" + today.isoformat()) is None
    assert database.notification("error:" + today.isoformat())


def test_uncertain_send_is_not_retried(config, database, services, today):
    services[3].send.side_effect = DeliveryUncertain("Timeout")
    run_monitor(config, database, LocalState(), *services, today=today)
    first_count = services[3].send.call_count
    run_monitor(config, database, LocalState(), *services, today=today)
    assert services[3].send.call_count == first_count


@pytest.mark.parametrize(
    "target,expected", [("2026-11-08", False), ("2026-11-09", True), ("2027-12-01", False)]
)
def test_assessment_date_filter(config, assignment, analysis, target, expected):
    analysis.relevant_date = target
    assert relevant(assignment, analysis, config, date(2026, 11, 9)) is expected


def test_possible_and_homework_filters(config, assignment, analysis, today):
    analysis.category = "possible"
    assert not relevant(assignment, analysis, replace(config, notify_possible=False), today)
    analysis.is_assessment = False
    assert not relevant(assignment, analysis, config, today)


def test_missing_config_is_explained_without_traceback(config, monkeypatch, caplog):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert main(["--check-config"]) == 1
    assert "GEMINI_API_KEY" in caplog.text
