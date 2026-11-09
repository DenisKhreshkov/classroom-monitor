# Created project files

The project is rooted at `D:\Classroom Monitor`. Generated caches, the virtual environment and deployment archive are not source files.

| File | Purpose |
| --- | --- |
| `main.py` | Monitor orchestration and command-line operations |
| `config.py` | Environment configuration and scope selection |
| `models.py` | Assignment model, Berlin dates and analysis validation |
| `errors.py` | Safe application errors |
| `google_authentication.py` | OAuth consent and token refresh |
| `authorize_google.py` | Terminal Google authorization, with an optional automatic loopback mode |
| `classroom_client.py` | Google read-only HTTP client and paginated coursework |
| `materials.py` | Authorized attachment text extraction |
| `assignment_analyzer.py` | Gemini structured semantic classification |
| `telegram_bot.py` | Formatting, safe delivery and duplicate prevention |
| `telegram_setup.py` | Retrieve Telegram chat IDs without printing tokens |
| `database.py` | SQLite assignments, outbox and metadata |
| `state_store.py` | Persistent private GitHub state branch |
| `package_project.py` | Safe source-only ZIP builder |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | Test and lint dependencies |
| `pyproject.toml` | Pytest and Ruff configuration |
| `.env.example` | Annotated configuration template |
| `.gitignore` | Exclude secrets and generated files |
| `credentials/.gitkeep` | Preserve the empty credentials directory |
| `.github/workflows/daily_monitor.yml` | Berlin-time daily monitoring and manual operations |
| `.github/workflows/tests.yml` | Secret-free Python 3.11/3.14 CI |
| `tests/conftest.py` | Shared mocked fixtures |
| `tests/test_database.py` | Storage, caching, corruption and deduplication |
| `tests/test_analyzer.py` | Structured responses, validation and failures |
| `tests/test_notifications.py` | Formatting, send failures and durable delivery |
| `tests/test_config.py` | Missing configuration, OAuth refresh and scopes |
| `tests/test_monitor.py` | Repeated complete runs, filtering and failures |
| `tests/test_classroom.py` | Pagination, permissions, HTTP bounds and DST |
| `tests/test_materials.py` | Attachment extraction, bounds and access restrictions |
| `tests/test_state_store.py` | GitHub restore, checkpoints and conflicting writers |
| `tests/test_workflow.py` | Workflow configuration checks |
| `tests/test_telegram_setup.py` | Chat ID helper and safe errors |
| `tests/test_packaging.py` | Ensure deployment archives exclude credentials |
| `tests/test_google_authorization.py` | Terminal redirects, PKCE, state validation and safe token persistence |
| `README.md` | Beginner setup, deployment and troubleshooting |
| `VERIFICATION.md` | Actual local verification and limits |
| `PROJECT_FILES.md` | This complete source-file inventory |

Generated during development: `.venv/`, Python/pytest/Ruff caches, and `dist/ClassroomMonitor.zip`. No real `.env`, OAuth client/token file, or live account database was created.
