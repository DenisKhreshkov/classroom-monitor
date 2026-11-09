from datetime import date
from unittest.mock import Mock

import pytest

from config import Config
from database import Database
from models import Analysis, Assignment


@pytest.fixture
def assignment():
    return Assignment(
        "course1",
        "Mathematik",
        "work1",
        "Klassenarbeit Kapitel 3",
        {
            "id": "work1",
            "title": "Klassenarbeit Kapitel 3",
            "state": "PUBLISHED",
            "description": "Am 20. November schreiben wir eine benotete Klassenarbeit.",
            "dueDate": {"year": 2026, "month": 11, "day": 20},
            "dueTime": {"hours": 14, "minutes": 0},
            "alternateLink": "https://classroom.google.com/c/123/a/456/details",
            "materials": [],
        },
    )


@pytest.fixture
def analysis():
    return Analysis(
        is_assessment=True,
        assessment_type="Klassenarbeit",
        confidence=95,
        reason="Die Beschreibung kündigt eine benotete Klassenarbeit an.",
        relevant_date="2026-11-20",
        important_details=["Kapitel 3"],
        category="definite",
    )


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    yield db
    db.close()


@pytest.fixture
def config(monkeypatch, tmp_path):
    # A user's configured account must never be loaded by an offline test.
    monkeypatch.setattr("config.load_dotenv", lambda *args, **kwargs: None)
    for name in list(__import__("os").environ):
        if name.startswith(("GOOGLE_", "GEMINI_", "TELEGRAM_", "STATE_", "GITHUB_")) or name in {
            "DAILY_SUMMARY",
            "READ_ATTACHMENTS",
            "TEACHER_MODE",
            "NOTIFY_POSSIBLE",
            "MIN_CONFIDENCE",
            "LOOKAHEAD_DAYS",
            "LOG_FILE",
        }:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("GOOGLE_CLIENT_FILE", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    return Config.load(dotenv=False)


@pytest.fixture
def services(assignment, analysis):
    classroom = Mock(failures=0)
    classroom.assignments.return_value = [assignment]
    reader = Mock()
    reader.read.return_value = []
    analyzer = Mock()
    analyzer.analyze.return_value = analysis
    bot = Mock()
    bot.send.return_value = "42"
    return classroom, reader, analyzer, bot


@pytest.fixture
def today():
    return date(2026, 11, 9)
