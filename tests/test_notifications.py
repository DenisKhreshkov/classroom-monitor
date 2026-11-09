import html
import re
from unittest.mock import Mock

import pytest
import requests

from errors import DeliveryRejected, DeliveryUncertain, StateError
from state_store import LocalState
from telegram_bot import TelegramBot, deliver, format_assessment


def session_response(status=200, payload=None):
    session = Mock()
    session.post.return_value = Mock(status_code=status)
    session.post.return_value.json.return_value = payload or {
        "ok": True,
        "result": {"message_id": 123},
    }
    return session


def test_format(assignment, analysis):
    assignment.title = '<script>alert("x")</script>'
    assignment.raw["description"] = (
        "Please read <chapter 3> & bring a calculator.\nKeine Übersetzung."
    )
    body = format_assessment(assignment, analysis)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "Mathematik" in body
    assert "Klassenarbeit" in body
    assert "20.11.2026 15:00 (Берлин)" in body
    assert "95%" in body
    assert html.escape(assignment.raw["description"], quote=True) in body
    assert "Новая проверочная работа" in body
    assert "Предмет:" in body
    assert "Описание задания:" in body
    assert "Обоснование:" in body
    assert "(подтверждено)" in body
    assert "(definite)" not in body
    assert "classroom.google.com" in body
    assignment.raw["alternateLink"] = "javascript:alert(1)"
    assert "javascript" not in format_assessment(assignment, analysis)


def test_format_possible_missing_date(assignment, analysis):
    assignment.raw.pop("dueDate")
    analysis.category = "possible"
    assignment.raw.pop("description")
    analysis.relevant_date = None
    body = format_assessment(assignment, analysis)
    assert "Возможная проверочная работа" in body
    assert "Срок сдачи: Не указан" in body
    assert "Дата по анализу: Неизвестно" in body
    assert "Не указано" in body
    assert "(нужна проверка)" in body
    assert "Проверьте дату" in body


def test_description_excerpt_keeps_message_within_telegram_limit(assignment, analysis):
    assignment.title = "😀" * 3000
    assignment.course_name = "😀" * 300
    assignment.raw["description"] = "Please read: <&>😀" * 3000
    analysis.reason = "😀" * 2000
    analysis.important_details = ["😀" * 200] * 6
    body = format_assessment(assignment, analysis)
    visible = html.unescape(re.sub(r"<[^>]*>", "", body))
    assert len(visible.encode("utf-16-le")) // 2 <= 4096
    assert "Please read: &lt;&amp;&gt;😀" in body
    assert "Полный текст описания — по ссылке на задание." in body
    assert "Открыть в Google Classroom" in body


def test_send_success_and_no_token_in_error():
    session = session_response()
    bot = TelegramBot("SECRET", "456", session=session)
    assert bot.send("hello") == "123"
    assert session.post.call_args.kwargs["json"]["chat_id"] == "456"
    session.post.side_effect = requests.Timeout("https://api.telegram.org/botSECRET")
    with pytest.raises(DeliveryUncertain) as exc:
        bot.send("hello")
    assert "SECRET" not in str(exc.value)


def test_timeout_never_automatically_retries():
    session = session_response()
    session.post.side_effect = requests.Timeout()
    with pytest.raises(DeliveryUncertain):
        TelegramBot("token", "chat", session).send("hello")
    assert session.post.call_count == 1


def test_explicit_rejection_and_ambiguous_server_error():
    with pytest.raises(DeliveryRejected):
        TelegramBot("token", "chat", session_response(400, {"ok": False})).send("hello")
    with pytest.raises(DeliveryUncertain):
        TelegramBot("token", "chat", session_response(503, {"ok": False})).send("hello")


def test_rate_limit_retries_only_explicit_rejection(monkeypatch):
    monkeypatch.setattr("telegram_bot.time.sleep", lambda _: None)
    session = session_response(429, {"ok": False, "parameters": {"retry_after": 1}})
    with pytest.raises(DeliveryRejected):
        TelegramBot("token", "chat", session).send("hello")
    assert session.post.call_count == 3


def test_checkpoint_precedes_send_and_duplicate_is_blocked(database):
    database.enqueue("a", "body")
    events = []
    state, bot = Mock(), Mock()
    state.save.side_effect = lambda db: events.append(db.notification("a")["status"])
    bot.send.side_effect = lambda _: events.append("telegram") or "42"
    assert deliver(database, state, bot, "a")
    assert events == ["sending", "telegram", "sent"]
    assert not deliver(database, state, bot, "a")
    assert bot.send.call_count == 1


def test_failed_pre_send_checkpoint_blocks_telegram(database):
    database.enqueue("a", "body")
    state, bot = Mock(), Mock()
    state.save.side_effect = StateError("Cannot persist")
    with pytest.raises(StateError):
        deliver(database, state, bot, "a")
    bot.send.assert_not_called()


@pytest.mark.parametrize(
    "error,status", [(DeliveryRejected("no"), "pending"), (DeliveryUncertain("maybe"), "uncertain")]
)
def test_delivery_failure_state(database, error, status):
    database.enqueue("a", "body")
    bot = Mock()
    bot.send.side_effect = error
    with pytest.raises(type(error)):
        deliver(database, LocalState(), bot, "a")
    assert database.notification("a")["status"] == status


def test_crash_after_telegram_success_is_not_resent(database):
    database.enqueue("a", "body")
    state, bot = Mock(), Mock()
    bot.send.return_value = "42"
    persisted = []

    def save(db):
        if persisted:
            raise StateError("post-send failure")
        persisted.append(db.snapshot())

    state.save.side_effect = save
    with pytest.raises(StateError):
        deliver(database, state, bot, "a")
    from database import Database

    path = database.path.with_name("remote.sqlite3")
    path.write_bytes(persisted[0])
    restored = Database(path)
    restored.recover_interrupted()
    assert restored.notification("a")["status"] == "uncertain"
    assert not deliver(restored, Mock(), bot, "a")
    assert bot.send.call_count == 1
    restored.close()
