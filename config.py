"""Environment-only configuration, with explicit validation."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from errors import ConfigError

ROOT = Path(__file__).resolve().parent
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMINI_TIMEOUT_SECONDS = 120
DEFAULT_GEMINI_MAX_ATTEMPTS = 3
DEFAULT_GEMINI_RETRY_BASE_SECONDS = 5


def flag(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigError(f"{name} must be true or false.")
    return value in {"true", "1"}


def number(name: str, default: int, low: int, high: int) -> int:
    try:
        result = int(os.getenv(name, str(default)))
    except ValueError:
        raise ConfigError(f"{name} must be an integer.") from None
    if not low <= result <= high:
        raise ConfigError(f"{name} must be between {low} and {high}.")
    return result


def local_path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Config:
    gemini_api_key: str = field(repr=False)
    telegram_token: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    google_client_id: str = field(repr=False)
    google_client_secret: str = field(repr=False)
    google_token_json: str = field(repr=False)
    client_file: Path
    token_file: Path
    database_path: Path
    model: str
    read_attachments: bool
    teacher_mode: bool
    daily_summary: bool
    notify_possible: bool
    min_confidence: int
    lookahead_days: int
    state_backend: str
    state_branch: str
    github_repository: str
    github_token: str = field(repr=False)
    gemini_timeout_seconds: int = DEFAULT_GEMINI_TIMEOUT_SECONDS
    gemini_max_attempts: int = DEFAULT_GEMINI_MAX_ATTEMPTS
    gemini_retry_base_seconds: int = DEFAULT_GEMINI_RETRY_BASE_SECONDS
    log_file: str = ""

    @classmethod
    def load(
        cls, *, auth_only: bool = False, dotenv: bool = True, dotenv_override: bool = False
    ) -> "Config":
        if dotenv:
            load_dotenv(ROOT / ".env", override=dotenv_override)
        required = [] if auth_only else ["GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
        missing = [key for key in required if not os.getenv(key, "").strip()]
        if missing:
            raise ConfigError(
                "Missing configuration: " + ", ".join(missing) + ". See .env.example."
            )
        backend = os.getenv("STATE_BACKEND", "local")
        if backend not in {"local", "github"}:
            raise ConfigError("STATE_BACKEND must be local or github.")
        branch = os.getenv("STATE_BRANCH", "classroom-monitor-state")
        if branch != "classroom-monitor-state":
            raise ConfigError("Use the dedicated STATE_BRANCH=classroom-monitor-state.")
        if backend == "github" and not all(
            os.getenv(k) for k in ["GITHUB_TOKEN", "GITHUB_REPOSITORY"]
        ):
            raise ConfigError("GitHub state requires GITHUB_TOKEN and GITHUB_REPOSITORY.")
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            google_token_json=os.getenv("GOOGLE_TOKEN_JSON", ""),
            client_file=local_path("GOOGLE_CLIENT_FILE", "credentials/client_secret.json"),
            token_file=local_path("GOOGLE_TOKEN_FILE", "credentials/token.json"),
            database_path=local_path("DATABASE_PATH", "data/classroom.sqlite3"),
            model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL,
            read_attachments=flag("READ_ATTACHMENTS", True),
            teacher_mode=flag("TEACHER_MODE", False),
            daily_summary=flag("DAILY_SUMMARY", False),
            notify_possible=flag("NOTIFY_POSSIBLE", True),
            min_confidence=number("MIN_CONFIDENCE", 50, 0, 100),
            lookahead_days=number("LOOKAHEAD_DAYS", 180, 1, 730),
            state_backend=backend,
            state_branch=branch,
            github_repository=os.getenv("GITHUB_REPOSITORY", ""),
            github_token=os.getenv("GITHUB_TOKEN", ""),
            gemini_timeout_seconds=number(
                "GEMINI_TIMEOUT_SECONDS", DEFAULT_GEMINI_TIMEOUT_SECONDS, 10, 300
            ),
            gemini_max_attempts=number("GEMINI_MAX_ATTEMPTS", DEFAULT_GEMINI_MAX_ATTEMPTS, 1, 5),
            gemini_retry_base_seconds=number(
                "GEMINI_RETRY_BASE_SECONDS", DEFAULT_GEMINI_RETRY_BASE_SECONDS, 1, 30
            ),
            log_file=os.getenv("LOG_FILE", ""),
        )


def google_scopes(config: Config) -> list[str]:
    base = "https://www.googleapis.com/auth/"
    scopes = [base + "classroom.courses.readonly", base + "classroom.coursework.me.readonly"]
    if config.teacher_mode:
        scopes.append(base + "classroom.coursework.students.readonly")
    if config.read_attachments:
        scopes += [base + "drive.readonly", base + "forms.body.readonly"]
    return scopes
