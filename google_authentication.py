"""One-time local consent; remote execution only refreshes existing credentials."""

import getpass
import json
import logging
import os
import secrets
import tempfile
import warnings
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import OAuth2Error
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config, google_scopes
from errors import ConfigError, ProviderError

log = logging.getLogger(__name__)


def normalize_scopes(value) -> list[str]:
    """Google may represent scope metadata as a space-separated string or list."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, (list, tuple, set)) or any(
        not isinstance(scope, str) or not scope or any(char.isspace() for char in scope)
        for scope in value
    ):
        raise ProviderError(
            "Google token has invalid scope metadata. Authorize again to obtain a valid token."
        )
    return sorted(set(value))


def accept_granted_scopes(flow: InstalledAppFlow):
    """Adopt Google's grants before OAuthlib compares scopes during token parsing.

    This public response hook is local to this OAuth session. It does not disable
    OAuth state, PKCE, HTTPS validation, or modify global OAuthlib settings.
    """
    session = flow.oauth2session

    def use_granted_scopes(response):
        try:
            data = response.json()
        except ValueError:
            return response  # Let the library handle malformed token responses.
        if (
            200 <= response.status_code < 300
            and isinstance(data, dict)
            and data.get("access_token")
            and "error" not in data
            and data.get("scope") is not None
        ):
            session.scope = normalize_scopes(data["scope"])
        return response

    session.register_compliance_hook("access_token_response", use_granted_scopes)


def report_missing_scopes(requested: list[str], granted: list[str]):
    missing = sorted(set(requested) - set(granted))
    if missing:
        log.warning(
            "Google token has fewer permissions than configured. Unavailable permissions: %s. "
            "The granted token is retained; related API features may be unavailable. "
            "Authorize again to grant them, or disable optional features in configuration.",
            ", ".join(missing),
        )


class TimedRequest(Request):
    def __init__(self):
        session = requests.Session()
        session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=3,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"],
                    raise_on_status=False,
                    backoff_max=30,
                    respect_retry_after_header=False,
                )
            ),
        )
        super().__init__(session=session)

    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 30
        return super().__call__(*args, **kwargs)


TERMINAL_REDIRECT_URI = "http://127.0.0.1:8765/"


def terminal_authorization(flow: InstalledAppFlow) -> Credentials:
    """Manually receive a loopback redirect, retaining PKCE and OAuth state checks.

    Google's retired OOB/run_console flow must not be used. No listener is started;
    the user copies the complete redirect from the browser's address bar instead.
    """
    flow.redirect_uri = TERMINAL_REDIRECT_URI
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    print("Open this Google authorization link in your browser:\n")
    print(url)
    print(
        "\nAfter consent, the browser will redirect to http://127.0.0.1:8765/.\n"
        "A connection-refused page is expected: this mode starts no local server.\n"
        "Copy the ENTIRE address from the browser address bar, including code and state.\n"
        "Paste it below in this same terminal (input is hidden), then press Enter.\n"
        "Keep this terminal running. Do not share the redirect URL or paste it into chat."
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            response = getpass.getpass("Full redirect URL: ").strip()
    except getpass.GetPassWarning:
        raise ProviderError(
            "This console cannot hide pasted input. Run authorize_google.py in the VS Code Terminal or PowerShell, not the Debug Console or a redirected process."
        ) from None
    except (EOFError, KeyboardInterrupt):
        raise ProviderError(
            "Authorization cancelled. Run authorize_google.py in an interactive terminal to try again."
        ) from None
    try:
        parsed = urlsplit(response)
        redirect = urlsplit(TERMINAL_REDIRECT_URI)
        if (
            parsed.scheme != redirect.scheme
            or parsed.netloc != redirect.netloc
            or parsed.path != redirect.path
            or parsed.fragment
        ):
            raise ValueError
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
    except ValueError:
        raise ProviderError(
            "Paste the complete http://127.0.0.1:8765/ redirect URL from the browser, not a bare code or the Google sign-in link."
        ) from None
    received_state = query.get("state", [])
    if len(received_state) != 1 or not secrets.compare_digest(
        received_state[0].encode("utf-8"), state.encode("utf-8")
    ):
        raise ProviderError(
            "OAuth state did not match this authorization attempt. Start again and use the new link in the same terminal."
        )
    if "error" in query:
        raise ProviderError(
            "Google consent was denied or could not be completed. Start again and grant the selected read-only permissions."
        )
    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise ProviderError(
            "The redirect URL has no single authorization code. Copy the entire address after completing Google consent."
        )
    print("Exchanging the authorization code with Google...")
    # State was checked above. Passing code avoids globally relaxing oauthlib's
    # HTTPS checks just to parse an HTTP loopback URL. PKCE remains enabled.
    flow.fetch_token(code=codes[0], timeout=30)
    return flow.credentials


def save_token(result: Credentials, path: Path):
    """Replace token files atomically; a failed save preserves any existing token."""
    temp_path = None
    info = json.loads(result.to_json())
    # Credentials.to_json() normally serializes requested scopes, not the
    # separate granted_scopes property. Persist the actual grants when present.
    if result.granted_scopes is not None:
        info["scopes"] = normalize_scopes(result.granted_scopes)
    elif "scopes" in info:
        info["scopes"] = normalize_scopes(info["scopes"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".oauth-token-",
            suffix=".tmp",
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            os.chmod(temp_path, 0o600)
            output.write(json.dumps(info, ensure_ascii=False))
            output.flush()
            os.fsync(output.fileno())
        temp_path.replace(path)
    except OSError:
        raise ProviderError(
            "Google authorized successfully, but the token file could not be saved. Check GOOGLE_TOKEN_FILE and folder write permissions, then authorize again."
        ) from None
    finally:
        if temp_path:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)


def authorize(flow: InstalledAppFlow, method: str) -> Credentials:
    if flow.client_type != "installed":
        raise ConfigError(
            "Google credentials must be for a Desktop app, not a Web client. Download a Desktop OAuth client JSON."
        )
    try:
        accept_granted_scopes(flow)
        if method == "console":
            return terminal_authorization(flow)
        if method != "local-server":
            raise ConfigError("Authorization method must be console or local-server.")
        return flow.run_local_server(
            host="127.0.0.1",
            port=0,
            access_type="offline",
            prompt="consent",
            authorization_prompt_message="Authorize in the browser that opens.",
            success_message="Google consent received. Return to the terminal to finish token exchange and saving.",
            timeout_seconds=300,
        )
    except OAuth2Error as exc:
        explanations = {
            "invalid_grant": "Google rejected an expired or already used code. Run authorization again and paste the redirect promptly.",
            "invalid_client": "Google rejected the OAuth client. Check the Desktop client ID and secret.",
            "access_denied": "Google consent was denied. Authorize again and grant the selected permissions.",
            "redirect_uri_mismatch": "Google rejected the redirect URI. Use a Desktop OAuth client JSON.",
            "invalid_scope": "Google rejected the requested scopes. Check READ_ATTACHMENTS, TEACHER_MODE and Google Data Access settings.",
        }
        raise ProviderError(
            explanations.get(
                exc.error,
                "Google rejected the OAuth token exchange. Check the Desktop client and restart authorization.",
            )
        ) from None
    except requests.RequestException:
        raise ProviderError(
            "Google token exchange failed over the network. Check outbound HTTPS, proxy settings and connectivity to oauth2.googleapis.com; then authorize again."
        ) from None
    except OSError:
        raise ProviderError(
            "Could not start the local authorization server. Run authorize_google.py --console to paste the browser redirect instead."
        ) from None


def credentials(
    config: Config, *, interactive: bool = False, authorization_method: str = "console"
) -> Credentials:
    scopes = google_scopes(config)
    try:
        if interactive:
            if config.client_file.exists():
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(config.client_file), scopes, autogenerate_code_verifier=True
                )
            elif config.google_client_id and config.google_client_secret:
                flow = InstalledAppFlow.from_client_config(
                    {
                        "installed": {
                            "client_id": config.google_client_id,
                            "client_secret": config.google_client_secret,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": ["http://localhost"],
                        }
                    },
                    scopes,
                    autogenerate_code_verifier=True,
                )
            else:
                raise ConfigError(
                    "Download the Desktop OAuth client to credentials/client_secret.json first."
                )
            result = authorize(flow, authorization_method)
            if not result.refresh_token:
                raise ProviderError(
                    "Google did not issue a refresh token. Reauthorize with consent."
                )
            save_token(result, config.token_file)
            granted = result.granted_scopes if result.granted_scopes is not None else result.scopes
            report_missing_scopes(scopes, normalize_scopes(granted))
            return result
        if config.google_token_json:
            info = json.loads(config.google_token_json)
        elif config.token_file.exists():
            info = json.loads(config.token_file.read_text(encoding="utf-8"))
        else:
            raise ConfigError("Google token missing. Run python authorize_google.py locally first.")
        # Never send a refresh token to a token endpoint supplied in a token file.
        info["token_uri"] = "https://oauth2.googleapis.com/token"
        info["client_id"] = config.google_client_id or info.get("client_id")
        info["client_secret"] = config.google_client_secret or info.get("client_secret")
        info["scopes"] = normalize_scopes(info.get("scopes"))
        report_missing_scopes(scopes, info["scopes"])
        result = Credentials.from_authorized_user_info(info)
        if not result.valid:
            result.refresh(TimedRequest())
        return result
    except (ValueError, KeyError, OSError, GoogleAuthError, requests.RequestException):
        raise ProviderError(
            "Google authorization failed. Check OAuth settings; reauthorize if the refresh token expired."
        ) from None
