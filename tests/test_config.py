import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from config import Config, google_scopes
from errors import ConfigError, ProviderError
from google_authentication import credentials


def test_missing_configuration(config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        Config.load(dotenv=False)


def test_gemini_key_and_model_configuration(config, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  test-gemini-key  ")
    monkeypatch.setenv("GEMINI_MODEL", "custom-gemini-model")
    loaded = Config.load(dotenv=False)
    assert loaded.gemini_api_key == "test-gemini-key"
    assert loaded.model == "custom-gemini-model"


def test_gemini_timeout_and_retry_configuration(config, monkeypatch):
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("GEMINI_RETRY_BASE_SECONDS", "7")
    loaded = Config.load(dotenv=False)
    assert loaded.gemini_timeout_seconds == 180
    assert loaded.gemini_max_attempts == 4
    assert loaded.gemini_retry_base_seconds == 7


@pytest.mark.parametrize(
    "name,value",
    [
        ("MIN_CONFIDENCE", "101"),
        ("LOOKAHEAD_DAYS", "bad"),
        ("DAILY_SUMMARY", "yes"),
        ("STATE_BACKEND", "artifact"),
        ("STATE_BRANCH", "main"),
        ("GEMINI_TIMEOUT_SECONDS", "0"),
        ("GEMINI_TIMEOUT_SECONDS", "301"),
        ("GEMINI_MAX_ATTEMPTS", "0"),
        ("GEMINI_MAX_ATTEMPTS", "6"),
        ("GEMINI_RETRY_BASE_SECONDS", "0"),
    ],
)
def test_invalid_configuration(config, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Config.load(dotenv=False)


def test_credentials_do_not_appear_in_repr(config):
    assert "test-gemini-key" not in repr(config)
    assert "123:fake" not in repr(config)


def test_minimal_scopes(config):
    scopes = google_scopes(replace(config, read_attachments=False))
    assert len(scopes) == 2
    assert all(scope.endswith("readonly") for scope in scopes)
    assert not any("drive" in scope or "forms" in scope for scope in scopes)


def test_missing_token_and_invalid_token(config):
    with pytest.raises(ConfigError, match="token missing"):
        credentials(config)
    with pytest.raises(ProviderError, match="authorization failed"):
        credentials(replace(config, google_token_json="{INVALID"))


def test_partial_scopes_load_without_inventing_permissions(config, monkeypatch, caplog):
    result = Mock(valid=True)
    factory = Mock(return_value=result)
    monkeypatch.setattr("google_authentication.Credentials.from_authorized_user_info", factory)
    assert credentials(replace(config, google_token_json=json.dumps({"scopes": []}))) is result
    assert factory.call_args.args[0]["scopes"] == []
    assert "fewer permissions" in caplog.text


def test_expired_credentials_refresh_without_interaction(config, monkeypatch):
    fake = Mock(valid=False)
    factory = Mock(return_value=fake)
    monkeypatch.setattr("google_authentication.Credentials.from_authorized_user_info", factory)
    info = {"scopes": google_scopes(config), "token_uri": "https://evil.example/steal"}
    assert credentials(replace(config, google_token_json=json.dumps(info))) is fake
    fake.refresh.assert_called_once()
    assert factory.call_args.args[0]["token_uri"] == "https://oauth2.googleapis.com/token"


def test_local_auth_dotenv_overrides_stale_scope_flags(tmp_path, monkeypatch):
    monkeypatch.setattr("config.ROOT", tmp_path)
    (tmp_path / ".env").write_text("READ_ATTACHMENTS=false\nTEACHER_MODE=true\n", encoding="utf-8")
    monkeypatch.setenv("READ_ATTACHMENTS", "true")
    monkeypatch.setenv("TEACHER_MODE", "false")
    monkeypatch.setenv("STATE_BACKEND", "local")
    config = Config.load(auth_only=True, dotenv_override=True)
    assert not config.read_attachments
    assert config.teacher_mode
    assert set(google_scopes(config)) == {
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    }
