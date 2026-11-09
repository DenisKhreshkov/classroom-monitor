# Verification

Local platform: Windows, Python 3.14.5, isolated `.venv`.

Development checks do not verify live Google authorization, Gemini classification, real Telegram delivery or GitHub deployment. The user's private configuration and client files are excluded from test fixtures and deployment packaging.

Automated checks use mocks and temporary SQLite databases. They cover persistence, classification schemas, notification formatting, repeated-run deduplication, interrupted delivery, API errors, OAuth refresh, pagination, attachment limits, German daylight saving dates, and conflicting GitHub state writers. The CI workflow also targets Python 3.11; that interpreter is not installed locally.

Verified on 2026-11-09:

| Check | Actual result |
| --- | --- |
| `python -m pytest -q` | **198 passed** (3.19 seconds after the Russian notification update) |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | All 30 Python files already formatted |
| `python -m pip check` | No broken requirements |
| `python main.py --help` | All supported CLI operations displayed; exit 0 |
| `python main.py --check-config` without credentials | Expected exit 1; clearly names missing keys, no traceback or secret values |
| Python 3.11 grammar parsing | All project Python files accepted |
| Real installed Gemini SDK over an offline HTTP transport | Structured schema emitted and JSON response parsed successfully |
| Source ZIP allowlist test | Credential files excluded even when present in the input folder |
| `python authorize_google.py --help` | Terminal mode is the default; optional automatic local-server mode is available |

The deployment archive is created with `python package_project.py`. All account credentials, `.env`, SQLite files and the virtual environment are excluded. The source inventory is [PROJECT_FILES.md](PROJECT_FILES.md).

The initial tests exposed an incorrectly configured mock message ID; it was corrected before the final successful run. No failed test remains. No live API result is inferred from these mocked or offline checks.

## Terminal OAuth update

The authorization command now defaults to printing a sign-in link and accepting the full loopback redirect URL through hidden terminal input. It does not start a callback server or use the retired OOB `run_console()` method. OAuth state and PKCE remain enforced. `--local-server` retains automatic callback capture, and token files are replaced atomically only after successful authorization.

The new tests exercise valid/invalid callbacks, state mismatch, cancellation, hidden-input availability, the installed library's PKCE token request, network/OAuth/scope diagnostics, token-file failure handling, and CLI mode selection. They use fake credentials and mocked transports only. Existing `.env` and Google client/token files are never read by these test fixtures.

## Granted-scope handling

The local authorization command now gives `.env` values precedence and requests scopes from the same configuration function as the monitor. A session-specific OAuth response hook adopts Google's returned grants before OAuthlib's scope comparison, avoiding loss of a valid token on a scope change without changing global OAuthlib settings. Serialization persists actual grants. Partial grants also load successfully later; missing configured permissions are reported rather than invented.

Regression tests run the installed Google/OAuthlib code with mocked token responses for all four attachment/teacher combinations, including additional grants, partial grants, reordered grants, and absent scope metadata. Each test checks the written token file and reloads it. A separate test checks `.env` precedence over stale exported scope flags. No real authorization or private token file is used in these checks.

## Gemini analysis migration

Analysis now uses the official `google-genai` SDK against the Gemini Developer API. Runtime configuration and the Actions workflow use `GEMINI_API_KEY` and `GEMINI_MODEL`. The former provider SDK is no longer a project dependency and was uninstalled from this workspace's virtual environment.

Offline tests exercise the actual installed SDK's JSON-schema request and response parsing, strict analysis validation, blocked/incomplete/empty/malformed responses, enclosing JSON fences, sanitized HTTP errors (including unavailable models and quota limits), network timeouts, and bounded retry/client lifecycle configuration. All tests use fake keys and mocked transports; no live Gemini call or free-tier eligibility check has been performed.

The user selected `gemini-3.1-flash-lite`. It is the default in configuration, the local `.env`, the template and the GitHub workflow. See README section 3 for free-tier setup. Unavailable-model responses produce an actionable error; the program does not silently switch providers, models or billing tiers.

## Gemini timeout recovery

The default per-attempt timeout is 120 seconds. The application performs at most three attempts for temporary HTTP/network failures, logging safe status summaries and waiting 5 then 10 seconds between retries. SDK retry attempts are set to one so the application limit is not multiplied. Environment settings and Actions variables can adjust the timeout, attempt count and initial retry delay within validated bounds.

Regression tests use Google's actual `ServerError(504)` class, simulate timeout-then-success and persistent failure, and verify retry delays, attempt limits, non-retryable errors, and client read timeouts. A real SDK call over an offline HTTP transport checks that `X-Server-Timeout: 120` and the HTTP client timeout reach each request, including the retry. A complete monitor test confirms that an exhausted timeout is not cached, can succeed next run, and does not cause duplicate assessment notifications. No live Google timeout or availability claim is made by these tests.

## Coursework age and publication filter

Course listing now requests active courses only. Published coursework is first listed with an ID/date-only field mask. Full details are requested only when creation or an elapsed scheduled publication timestamp falls within seven rolling days, or a due deadline remains open. Historical undated/overdue work is excluded before attachment reads and Gemini; update timestamps alone do not qualify. A second gate in the monitor protects the same rule for cached assignments and alternative data sources.

Tests cover the exact seven-day cutoff, timezone offsets, recent scheduled publication, old edited work, missing/invalid timestamps, past and future deadlines, date-only deadlines today, archived courses, source pagination with stable filters, full-detail request suppression for historical items, changes between list/get, and per-item API failures. End-to-end mocked monitor tests assert that excluded work does not read materials, call Gemini or send assessment notifications. Live Classroom requests were not needed to verify these code paths.

## Russian notification structure

Assessment headers and labels, category captions, missing-value text, description/details headings, link captions, review guidance, daily summaries and error notifications use Russian. Titles and descriptions are copied directly from Classroom and HTML-escaped. The newly included description is excerpted with a Russian notice when needed to fit Telegram's message limit. New Gemini-generated type/reason/detail values are requested in Russian without forcing reclassification of previously cached analyses.

Updated tests check original-language description preservation, HTML escaping, Russian category/placeholder text, Russian summaries and error alerts, and conservative UTF-16 message-length bounds for long descriptions containing emoji. No real Telegram messages were sent for verification.
