import getpass
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode

import pytest
import requests
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import InvalidGrantError

import authorize_google
from config import google_scopes
from errors import ConfigError, ProviderError
from google_authentication import (
    TERMINAL_REDIRECT_URI,
    authorize,
    credentials,
    save_token,
    terminal_authorization,
)


def mock_flow():
    flow = Mock(client_type="installed")
    flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?test",
        "state-test",
    )
    return flow


def test_terminal_authorization_pastes_complete_redirect(monkeypatch, capsys):
    flow = mock_flow()
    monkeypatch.setattr(
        "google_authentication.getpass.getpass",
        lambda _: TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE&state=state-test",
    )
    assert terminal_authorization(flow) is flow.credentials
    flow.run_local_server.assert_not_called()
    flow.fetch_token.assert_called_once_with(code="PRIVATE_CODE", timeout=30)
    assert flow.redirect_uri == TERMINAL_REDIRECT_URI
    flow.authorization_url.assert_called_once_with(access_type="offline", prompt="consent")
    output = capsys.readouterr().out
    assert "https://accounts.google.com" in output
    assert "connection-refused" in output
    assert "PRIVATE_CODE" not in output


@pytest.mark.parametrize(
    "response",
    [
        "bare-code",
        "https://accounts.google.com/?code=PRIVATE_CODE&state=state-test",
        "http://127.0.0.1:8765.evil.example/?code=PRIVATE_CODE&state=state-test",
        "http://localhost:8765/?code=PRIVATE_CODE&state=state-test",
        TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE&state=wrong",
        TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE",
        TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE&state=state-test&state=state-test",
        TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE&code=another&state=state-test",
        TERMINAL_REDIRECT_URI + "?code=&state=state-test",
        TERMINAL_REDIRECT_URI + "?state=state-test",
        TERMINAL_REDIRECT_URI + "?error=access_denied&state=state-test",
        TERMINAL_REDIRECT_URI + "?code=PRIVATE_CODE&state=state-test#fragment",
    ],
)
def test_invalid_redirect_never_exchanges_tokens(monkeypatch, response):
    flow = mock_flow()
    monkeypatch.setattr("google_authentication.getpass.getpass", lambda _: response)
    with pytest.raises(ProviderError) as exc:
        terminal_authorization(flow)
    assert "PRIVATE_CODE" not in str(exc.value)
    flow.fetch_token.assert_not_called()


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_cancelled_input_has_safe_diagnostic(monkeypatch, error):
    monkeypatch.setattr("google_authentication.getpass.getpass", Mock(side_effect=error))
    with pytest.raises(ProviderError, match="cancelled"):
        terminal_authorization(mock_flow())


def test_noninteractive_console_does_not_echo_redirect(monkeypatch):
    monkeypatch.setattr(
        "google_authentication.getpass.getpass", Mock(side_effect=getpass.GetPassWarning())
    )
    with pytest.raises(ProviderError, match="cannot hide"):
        terminal_authorization(mock_flow())


def test_real_library_uses_pkce_and_https_token_exchange(monkeypatch):
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": "offline-test",
                "client_secret": "offline-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        ["scope-test"],
        autogenerate_code_verifier=True,
    )
    exchange = []

    def token_send(request, **kwargs):
        exchange.append((request, kwargs))
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response._content = json.dumps(
            {
                "access_token": "offline-access",
                "refresh_token": "offline-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "scope-test",
            }
        ).encode()
        return response

    monkeypatch.setattr(flow.oauth2session, "send", token_send)
    monkeypatch.setattr(
        "google_authentication.getpass.getpass",
        lambda _: (
            TERMINAL_REDIRECT_URI
            + "?"
            + urlencode(
                {
                    "code": "offline-code",
                    "state": flow.oauth2session._state,
                }
            )
        ),
    )
    result = terminal_authorization(flow)
    assert result.refresh_token == "offline-refresh"
    assert exchange[0][0].url == "https://oauth2.googleapis.com/token"
    params = parse_qs(exchange[0][0].body)
    assert params["code_verifier"] == [flow.code_verifier]
    assert params["redirect_uri"] == [TERMINAL_REDIRECT_URI]
    assert flow.code_verifier
    assert exchange[0][1]["timeout"] == 30


@pytest.mark.parametrize(
    "error,expected",
    [
        (InvalidGrantError(description="PRIVATE_CODE"), "expired or already used"),
        (requests.Timeout("PRIVATE_TOKEN"), "network"),
    ],
)
def test_exchange_errors_are_actionable_and_sanitized(monkeypatch, error, expected):
    flow = mock_flow()
    monkeypatch.setattr(
        "google_authentication.getpass.getpass",
        lambda _: TERMINAL_REDIRECT_URI + "?code=code&state=state-test",
    )
    flow.fetch_token.side_effect = error
    with pytest.raises(ProviderError, match=expected) as exc:
        authorize(flow, "console")
    assert "PRIVATE" not in str(exc.value)


def test_optional_local_server_uses_loopback_and_honest_success_page():
    flow = mock_flow()
    assert authorize(flow, "local-server") is flow.run_local_server.return_value
    kwargs = flow.run_local_server.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert "token exchange" in kwargs["success_message"]


def test_web_client_rejected_before_authorization():
    flow = Mock(client_type="web")
    with pytest.raises(ConfigError, match="Desktop"):
        authorize(flow, "console")
    flow.authorization_url.assert_not_called()


def test_interactive_credentials_write_token_with_console_default(config, monkeypatch, tmp_path):
    flow = mock_flow()
    flow.credentials = Mock(refresh_token="private-refresh", granted_scopes=google_scopes(config))
    flow.credentials.to_json.return_value = '{"refresh_token":"private-refresh"}'
    factory = Mock(return_value=flow)
    monkeypatch.setattr("google_authentication.InstalledAppFlow.from_client_config", factory)
    monkeypatch.setattr(
        "google_authentication.getpass.getpass",
        lambda _: TERMINAL_REDIRECT_URI + "?code=code&state=state-test",
    )
    path = tmp_path / "credentials/token.json"
    cfg = replace(
        config,
        google_client_id="offline",
        google_client_secret="offline-secret",
        token_file=path,
        client_file=tmp_path / "missing-client.json",
    )
    assert credentials(cfg, interactive=True) is flow.credentials
    assert json.loads(path.read_text())["refresh_token"] == "private-refresh"
    assert factory.call_args.kwargs["autogenerate_code_verifier"] is True
    flow.run_local_server.assert_not_called()


def test_failed_save_preserves_existing_token_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    path.write_text("existing-token", encoding="utf-8")
    result = Mock()
    result.to_json.return_value = '{"refresh_token":"new-token"}'
    result.granted_scopes = None
    monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("PRIVATE")))
    with pytest.raises(ProviderError, match="could not be saved") as exc:
        save_token(result, path)
    assert "PRIVATE" not in str(exc.value)
    assert path.read_text() == "existing-token"
    assert not list(tmp_path.glob(".oauth-token-*.tmp"))


@pytest.mark.parametrize(
    "arguments,method",
    [([], "console"), (["--console"], "console"), (["--local-server"], "local-server")],
)
def test_cli_selects_method_without_running_live_oauth(config, monkeypatch, arguments, method):
    monkeypatch.setattr("authorize_google.Config.load", Mock(return_value=config))
    authenticate = Mock()
    monkeypatch.setattr("authorize_google.credentials", authenticate)
    assert authorize_google.main(arguments) == 0
    authenticate.assert_called_once_with(config, interactive=True, authorization_method=method)
    authorize_google.Config.load.assert_called_once_with(auth_only=True, dotenv_override=True)


@pytest.mark.parametrize(
    "read_attachments,teacher_mode", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("response_kind", ["extra", "partial", "reordered", "omitted"])
def test_real_scope_changes_save_actual_grants_and_reload(
    config, monkeypatch, tmp_path, read_attachments, teacher_mode, response_kind
):
    """Exercise OAuthlib's real mismatch parser and Google credential serialization."""
    cfg = replace(
        config,
        read_attachments=read_attachments,
        teacher_mode=teacher_mode,
        google_client_id="offline-client",
        google_client_secret="offline-secret",
        google_token_json="",
        token_file=tmp_path / "credentials/token.json",
    )
    requested = google_scopes(cfg)
    if response_kind == "extra":
        granted = requested + ["https://www.googleapis.com/auth/classroom.announcements.readonly"]
    elif response_kind == "partial":
        granted = requested[:1]
    else:
        granted = list(reversed(requested))
    flows = []
    original_factory = InstalledAppFlow.from_client_config

    def build_flow(client, scopes, **kwargs):
        assert scopes == requested
        flow = original_factory(client, scopes, **kwargs)
        flows.append(flow)

        def send(request, **request_kwargs):
            assert request.url == "https://oauth2.googleapis.com/token"
            payload = {
                "access_token": "offline-access",
                "refresh_token": "offline-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            if response_kind != "omitted":
                payload["scope"] = " ".join(granted)
            response = requests.Response()
            response.status_code = 200
            response.request = request
            response._content = json.dumps(payload).encode()
            return response

        monkeypatch.setattr(flow.oauth2session, "send", send)
        return flow

    monkeypatch.setattr("google_authentication.InstalledAppFlow.from_client_config", build_flow)
    monkeypatch.setattr(
        "google_authentication.getpass.getpass",
        lambda _: (
            TERMINAL_REDIRECT_URI
            + "?"
            + urlencode(
                {
                    "code": "offline-code",
                    "state": flows[0].oauth2session._state,
                }
            )
        ),
    )
    result = credentials(cfg, interactive=True)
    expected = set(requested if response_kind == "omitted" else granted)
    stored = json.loads(cfg.token_file.read_text(encoding="utf-8"))
    assert set(result.scopes) == expected
    assert set(stored["scopes"]) == expected
    assert stored["refresh_token"] == "offline-refresh"
    reloaded = credentials(cfg)
    assert set(reloaded.scopes) == expected
    assert reloaded.refresh_token == "offline-refresh"


def test_serializer_prefers_grants_over_requested_scopes(tmp_path):
    from google.oauth2.credentials import Credentials

    result = Credentials(
        token="offline-access",
        refresh_token="offline-refresh",
        client_id="offline-client",
        client_secret="offline-secret",
        scopes=["requested", "denied"],
        granted_scopes=["requested"],
    )
    path = tmp_path / "credentials/token.json"
    save_token(result, path)
    assert json.loads(path.read_text())["scopes"] == ["requested"]
