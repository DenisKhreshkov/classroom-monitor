# Classroom Monitor

Classroom Monitor reads your Google Classroom coursework, uses Gemini to identify school assessments, and sends Telegram notifications with Russian headers and labels. Assignment titles and descriptions retain their original Classroom language. Once configured in a **private GitHub repository**, it runs remotely each day at approximately **16:07 Europe/Berlin**, including German daylight saving changes. Your computer can stay off.

The application has been built and tested with mocked services. **The development checks do not verify live Google authorization, Gemini classification, real Telegram delivery, or GitHub deployment.** Those steps require your own account setup and authorization below. Never send passwords, keys, or token files into chat.

## What is ready

- Read-only Google OAuth with terminal-assisted browser authorization once, then automatic access-token refresh.
- Pagination over active courses, with a seven-day/open-deadline filter before full coursework retrieval, attachment reads and Gemini analysis. Archived courses are excluded.
- Semantic classification of tests, Klassenarbeiten, quizzes, graded presentations/projects, practical assessments and ambiguous assessment announcements. Routine homework points alone do not imply an exam.
- Authorized Google Docs/Slides exports, spreadsheet first-sheet CSV, text files, PDFs, and Forms where the API permits access. Unsupported or inaccessible material is explicitly marked unread.
- Structured, validated JSON with the six requested fields plus `category`: `definite`, `possible`, or `homework`.
- Telegram alerts with Russian headers and labels, original assignment title and description, course, dates, type, confidence, explanation and Classroom link; Russian daily summaries and important failure alerts.
- SQLite analysis cache, processing timestamps, outbox status, and last fully successful synchronization.
- Persistent private GitHub state branch, checkpoints around every message, interrupted-delivery review, and automated CI tests.

The project lives directly in the current workspace so `.github/workflows/` is at the repository root. See [PROJECT_FILES.md](PROJECT_FILES.md) for every project file and [VERIFICATION.md](VERIFICATION.md) for local checks.

## 1. Local installation

On this computer, Python 3.14 and a `.venv` are already available and the dependencies are installed. You can use the commands below without activating the environment; this also avoids PowerShell execution-policy issues.

For a fresh Windows installation, install Python 3.11 or newer from [python.org](https://www.python.org/downloads/), then open the project folder in VS Code and choose **Terminal → New Terminal**:

```powershell
cd 'D:\Classroom Monitor'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Only run the copy command if you have not already created `.env`; do not overwrite your configured file. For runtime dependencies alone, use `requirements.txt`. On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.

Open `.env` in VS Code and fill in values as you complete the next steps. Keep `STATE_BACKEND=local` for local runs. Relative paths are resolved from the project folder, regardless of where the command is started.

## 2. Google Cloud and OAuth

These account actions must be performed by you. The program cannot approve Google's consent screen on your behalf.

1. Sign in to [Google Cloud Console](https://console.cloud.google.com/) and create a project named **Classroom Monitor**.
2. In **APIs & Services → Library**, enable **Google Classroom API**. With the default `READ_ATTACHMENTS=true`, also enable **Google Drive API** and **Google Forms API**.
3. Open **Google Auth platform**. Configure the app name and contact email under **Branding**, choose **External** under **Audience** for a personal Google account, and add your own Google address as a test user.
4. Under **Clients**, create an OAuth client with application type **Desktop app**. Download its JSON and save it as `credentials/client_secret.json`.

Google's [Classroom Python quickstart](https://developers.google.com/workspace/classroom/quickstart/python) describes these console areas. A school administrator can restrict API access or prohibit your personal account from seeing school courses; the program cannot bypass that policy.

The following permissions are read-only. Configure these under **Data Access**, and authorize the same set requested by the program:

| Scope suffix after `https://www.googleapis.com/auth/` | Purpose |
| --- | --- |
| `classroom.courses.readonly` | List courses |
| `classroom.coursework.me.readonly` | Read coursework as a student |
| `drive.readonly` | Optional authorized attachment downloads and document exports |
| `forms.body.readonly` | Optional form questions and settings, never student responses |
| `classroom.coursework.students.readonly` | Added only if `TEACHER_MODE=true` |

`drive.readonly` grants broad read access to the authorized account's Drive. The application only requests files referenced in coursework. For narrower account permissions, set `READ_ATTACHMENTS=false` **before authorizing**; only the two student Classroom scopes are then requested. See Google's [Classroom scopes](https://developers.google.com/workspace/classroom/guides/auth), [Drive export API](https://developers.google.com/workspace/drive/api/guides/manage-downloads), and [Forms read scopes](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/get).

Authorize locally:

```powershell
.\.venv\Scripts\python.exe authorize_google.py
```

The default mode prints a Google sign-in link in the terminal and waits for you to paste the full browser redirect URL. It starts no local server:

1. Keep the command running in VS Code's **Terminal**, rather than the Output panel or Debug Console.
2. Open the printed link in your browser. Select the account that actually has your courses and grant the requested read-only permissions. Only approve a project you created and recognize.
3. After consent, Google redirects to `http://127.0.0.1:8765/`. The browser will usually display **connection refused** or **this site cannot be reached**. That is expected because this mode starts no listener.
4. Copy the **entire address from the browser address bar**, including `code=...` and `state=...`. Paste it at the terminal's **Full redirect URL:** prompt and press Enter. Input is hidden, so pasted characters do not appear. Do not paste just the code or share the redirect URL in chat.
5. The program validates the redirect and OAuth state, exchanges the code over HTTPS using PKCE, and saves the private refresh-token file to `credentials/token.json` (or your configured `GOOGLE_TOKEN_FILE`). Wait for the terminal to confirm that it saved the file.

The authorization command reloads the project's `.env` with file values taking precedence over stale exported environment variables. It prints the effective `READ_ATTACHMENTS` and `TEACHER_MODE` flags and the exact requested scopes before sign-in. Normal monitor runs retain environment-variable precedence so GitHub Secrets and Variables continue to work.

Google can return extra permissions from an earlier consent or grant only part of a request. The authorization session accepts Google's scope metadata and writes the **actual granted scopes** to the token's `scopes` field; it does not discard an otherwise successful token because the sets differ. If Google omits scope metadata, the requested set is retained according to OAuth's unchanged-scope convention. Missing configured permissions produce an informational warning after saving, not a failed authorization. They do not grant access to the corresponding APIs: unreadable attachments remain unread, and missing Classroom permissions can still prevent a full synchronization. See Google's guidance to [examine granted scopes](https://developers.google.com/identity/protocols/oauth2#3.-examine-scopes-of-access-granted-by-the-user).

You can explicitly select this mode with `python authorize_google.py --console`. Google's old OOB verification-code flow used by `flow.run_console()` was [retired for production apps](https://developers.google.com/identity/protocols/oauth2/resources/oob-migration); this implementation uses a Desktop client's loopback redirect with manual URL capture instead. It is a workaround for environments where automatic callback reception is unavailable. It still requires browser consent and outbound HTTPS to Google's token endpoint.

If automatic loopback callbacks work on your machine, `python authorize_google.py --local-server` retains that option. Its browser page only confirms that consent was received: token exchange and saving must still finish in the terminal. A success page followed by a terminal failure can indicate a token-exchange, scope, or file-permission problem rather than a blocked callback port. Specific terminal messages now distinguish those failures.

Do not upload either credentials file as repository source.

Alternatively, you can set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` instead of downloading the client file. They must belong to a Desktop client.

**For unattended operation:** external OAuth apps left in **Testing** generally issue refresh tokens that expire after seven days for these scopes. In Google's Audience settings, move your own personal-use app to **Production**, following any verification requirements Google presents, then authorize again. Production does not make a refresh token permanent: revocation, school policies, inactivity and other Google limits can still require reauthorization. Google's [token-expiration documentation](https://developers.google.com/identity/protocols/oauth2#expiration) explains the conditions. Enabling `READ_ATTACHMENTS` or `TEACHER_MODE` needs another authorization if the existing token lacks the corresponding grants; disabling a feature does not revoke previously granted Google permissions.

## 3. Gemini API

1. Open [Google AI Studio](https://aistudio.google.com/apikey) and create a Gemini API key for a project eligible for the free Developer API tier. The Classroom OAuth client secret is a different credential and cannot be used as this key.
2. Put your new key in `GEMINI_API_KEY` in `.env`. Old provider keys are not used by this application.
3. Select an available structured-output model with `GEMINI_MODEL`.

The selected default is **`gemini-3.1-flash-lite`**, which Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing) lists with free text input/output. Availability, free quotas and eligibility depend on your account and Google's current policies. The application never silently switches models or providers. To avoid charges, use a project without paid billing and confirm the model's free-tier eligibility in AI Studio; choosing a model name alone does not make a billed project free.

The implementation uses the official [`google-genai` SDK](https://googleapis.github.io/python-genai/), `client.models.generate_content()`, and a JSON schema validated locally with Pydantic. It rejects incomplete, blocked, empty and invalid output; transient service failures use bounded retries, and quota/model errors get clear messages. It uses the Gemini Developer API, with no local model or alternate provider. No claim of live classification quality is made by the offline tests.

Gemini requests now use a **120-second timeout per attempt**, with at most **3 total attempts**. Temporary HTTP errors (408, 429, 500, 502, 503 and 504) and network failures wait **5 seconds, then 10 seconds** before retrying. The application's own logs show attempts and delays without exposing provider responses. Hidden SDK retries are disabled to avoid multiplying the number of requests. Ordinary invalid-key, permission, request and model errors are not retried immediately. Compact JSON reduces unnecessary request whitespace while retaining all evidence.

A `504 DEADLINE_EXCEEDED` means Google did not finish within the deadline; increasing a client timeout cannot guarantee that Google's service will complete. If every attempt fails, no failed analysis is cached or notified as an assessment. The run reports an error, continues with other assignments, and retries the unfinished assignment on a later run. The SDK also sends the configured timeout as a server deadline hint, as implemented in its [HTTP client](https://github.com/googleapis/python-genai/blob/main/google/genai/_api_client.py).

To adjust these settings locally, set `GEMINI_TIMEOUT_SECONDS` (10–300), `GEMINI_MAX_ATTEMPTS` (1–5), and `GEMINI_RETRY_BASE_SECONDS` (1–30) in `.env`. Retry delays double and are capped at 30 seconds. For example, use `GEMINI_TIMEOUT_SECONDS=180` if the default deadline still fails regularly. More attempts can consume additional API quota. The selected model stays unchanged during recovery.

Assignment descriptions and extracted material text are sent to Google's Gemini API for analysis. Notification content goes to Telegram. The private state branch stores assignment titles, course names, AI results and notification history. Review the free service's data-handling terms before sending school material, and ensure this use fits your school's requirements. Credentials are never included in the AI prompt or state database.

## 4. Telegram

Notification headings, field labels, category captions, missing-value placeholders, section headings, link captions and review instructions are in Russian. The assignment title, description and course name come directly from Classroom and are not translated. HTML is escaped so source text cannot change the message formatting. Long source text is shortened to fit Telegram; an excerpted description includes a Russian notice directing you to the original assignment link. Future AI-generated types, explanations and details are requested in Russian; existing cached analysis values retain their previously generated language without extra classification calls.

1. In Telegram, open the official **@BotFather** account, run `/newbot`, and choose a name and bot username.
2. Copy the bot token into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Open your new bot's chat, press **Start**, and send `hello`.
4. Run the helper below. It retrieves updates using the Bot API and prints chat IDs without printing the token:

```powershell
.\.venv\Scripts\python.exe telegram_setup.py
```

Set `TELEGRAM_CHAT_ID` to your private chat's ID. If several IDs appear, send a message from only your own private chat and identify it carefully. The helper does not clear updates or change webhooks. A bot already used by another service may have conflicting updates/webhooks; create a dedicated bot for this project. Telegram's [Bot API](https://core.telegram.org/bots/api#sendmessage) documents delivery and message limits.

## 5. First local check

With the three service values and Google token ready:

```powershell
.\.venv\Scripts\python.exe main.py --check-config
.\.venv\Scripts\python.exe main.py --dry-run
```

`--check-config` checks required settings and token presence without network calls. It does not verify whether the credentials work. `--dry-run` reads Google and calls Gemini, so the request consumes Gemini API quota (and follows your project's billing tier); it sends no Telegram messages and does not record a successful synchronization. It can cache analyses in your local database.

To send real notifications locally:

```powershell
.\.venv\Scripts\python.exe main.py
```

The command runs once and exits. It does not install a background service. Before fetching full assignment details or reading attachments, it considers only published coursework in **active courses** that meets either condition:

- Its `creationTime`, or its already-elapsed `scheduledTime` for publication, falls within the last **seven rolling days** (168 hours, inclusive of the cutoff).
- Its due deadline has not passed, even if the item was created more than seven days ago. Explicit due times are compared as instants; date-only metadata remains eligible through its due day in Europe/Berlin.

Archived courses are never queried for coursework. Items with neither a valid recent timestamp nor an open deadline are excluded. A recent `updateTime` alone does not make historical coursework eligible. Recent work with a past deadline still passes this initial OR filter; notification relevance is checked after classification. Google does not expose a distinct actual publication timestamp, so elapsed scheduled publication dates are used where available, rather than treating every edit as publication.

The [Classroom coursework list API](https://developers.google.com/workspace/classroom/reference/rest/v1/courses.courseWork/list) has no creation-date or due-date predicate. Each run therefore lists **only lightweight ID/date metadata** for active-course coursework, filters it locally, and fetches full details only for eligible items. Metadata pagination continues so an old item with a future deadline is not accidentally missed. Full details are checked again before attachments and Gemini in case an item changed between requests. Log summaries show how many metadata items were excluded. The analysis cache prevents repeat Gemini calls for unchanged eligible evidence, and cached historical items cannot bypass the date gate.

On the first real run, existing eligible assessments can notify even if posted before installation. The later notification filter excludes known assessment dates before today or more than `LOOKAHEAD_DAYS` ahead. AI-announced assessment dates take precedence over the coursework deadline. Undated assessments can notify only if they passed the recent-item filter above.

## 6. Upload safely to a private GitHub repository

A source-only archive is prepared at `dist/ClassroomMonitor.zip`. Rebuild it after code changes with:

```powershell
.\.venv\Scripts\python.exe package_project.py
```

The packager uses an explicit file allowlist: `.env`, tokens, client credentials, databases, caches and `.venv` are excluded. GitHub's browser upload does not apply `.gitignore`, so upload **only the archive's extracted contents**, never your entire configured working folder. Do not upload the ZIP itself as the project: GitHub Actions needs the extracted files.

Because Git is not available on PATH on this computer, a browser upload is sufficient:

1. On GitHub, create a repository named **ClassroomMonitor**, select **Private**, and initialize it with a README so there is a `main` branch.
2. Extract `dist/ClassroomMonitor.zip` into a separate folder.
3. Choose **Add file → Upload files** on `main`. Upload the extracted files and folders, including `.github`, `.gitignore` and `.env.example`. Commit the upload to `main`. If prompted, replace the initial README.
4. Check that `.github/workflows/daily_monitor.yml` exists on the default branch, directly under the repository root.

If you prefer GitHub Desktop, add this folder as a local repository, review the file list against `.gitignore`, and publish it as private. No GitHub repository has been created or pushed automatically during this build.

## 7. GitHub Secrets and variables

Open your repository's **Settings → Secrets and variables → Actions → Secrets → New repository secret**. Add:

| Secret | What to paste |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key |
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | Your bot conversation's numeric ID |
| `GOOGLE_TOKEN_JSON` | The **entire contents** of `credentials/token.json`, including braces |
| `GOOGLE_CLIENT_ID` | Your Desktop OAuth client's ID |
| `GOOGLE_CLIENT_SECRET` | The matching client secret |

The token JSON also contains client information; the separate client secrets override it if supplied. Always keep all three from the same OAuth client. Do not create a personal GitHub token for the workflow: it uses GitHub's built-in, short-lived `GITHUB_TOKEN` with `contents: write`.

On the **Variables** tab, you may create:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Structured-output model |
| `GEMINI_TIMEOUT_SECONDS` | `120` | Timeout and server deadline hint per attempt |
| `GEMINI_MAX_ATTEMPTS` | `3` | Total request attempts, including the first |
| `GEMINI_RETRY_BASE_SECONDS` | `5` | Initial retry delay; doubles, capped at 30 seconds |
| `READ_ATTACHMENTS` | `true` | Authorized Drive/Forms text reading |
| `TEACHER_MODE` | `false` | Also request teacher coursework scope |
| `DAILY_SUMMARY` | `false` | Notify when a complete run finds no new assessment |
| `NOTIFY_POSSIBLE` | `true` | Include uncertain assessment announcements |
| `MIN_CONFIDENCE` | `50` | Minimum confidence in the chosen category |
| `LOOKAHEAD_DAYS` | `180` | Maximum known assessment-date horizon |

Use the literal strings `true` or `false`. Match attachment/teacher settings to your authorized token. Confidence describes the model's certainty in its **category**, not a calibrated probability that an exam exists. An ordinary-homework result is never notified, regardless of its confidence.

## 8. Activate remote automation

1. Open **Settings → Actions → General**. Allow the official checkout/setup-python actions and permit **Read and write permissions** for workflows if your account or organization requires this setting. Branch rules must allow the workflow to create and update `classroom-monitor-state`.
2. Open **Actions → Daily Classroom Monitor → Run workflow** on the default branch. Choose operation **initialize-state** once. This creates an independent state branch containing an empty SQLite database; it sends no messages and does not contact Google or Gemini.
3. Run operation **dry-run** to test your Google/Gemini access remotely. Check the logs and confirm it completes.
4. Run operation **monitor** to send genuine assessment notifications. Confirm receipt in Telegram.
5. Leave the workflow enabled. It automatically runs daily around 16:07 Berlin time without your computer.

The workflow explicitly sets `timezone: Europe/Berlin`. Current [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) supports IANA timezones; UTC is the default when no timezone is specified. Therefore both CET and CEST are handled by GitHub. Scheduled starts may be delayed or occasionally dropped by GitHub, and this is not a guaranteed-to-the-minute service. Monitor failed runs and use manual dispatch when needed. GitHub-hosted execution consumes your account's Actions allowance; check your plan rather than assuming unlimited usage.

The **Tests** workflow runs mocked tests on Python 3.11 and 3.14 on source pushes and pull requests, without account secrets. Only the local Python 3.14 run has been executed during this build; the GitHub matrix runs after upload.

## 9. Persistence and duplicate prevention

Runners are temporary. The program restores SQLite from the private `classroom-monitor-state` branch using the Git Data API. State has no artifact-retention expiry. The branch is independent of source history and must never be made the default branch or merged into `main`.

Every notification has a unique key; assessments use **course ID + coursework ID**. Reclassification and later edits do not resend an already delivered assessment. A previously ordinary assignment can notify if changed into an assessment. Optional daily summaries and error alerts each have separate per-day keys.

Before contacting Telegram, the program commits the `sending` state remotely. After success it commits `sent` and the message ID. GitHub requests that mutate state are not blindly retried, and non-forced branch updates reject competing writers. Workflow runs share one concurrency group; a file lock also prevents overlapping runs on one machine.

There is no transaction spanning SQLite/GitHub and Telegram. A timeout or runner crash can leave delivery ambiguous. To prevent automatic duplicates, interrupted/ambiguous attempts become **uncertain** and are not resent automatically. This means an alert can require manual review even if it was never received. Explicit Telegram rejections, including bounded rate limits, can safely remain pending for a later run. This favors duplicate prevention over guaranteed delivery.

For remote review, manually dispatch **list-uncertain**. Check the actual Telegram conversation. Dispatch **resolve** with the listed notification key and choose **sent** if received, or **retry** only if absent. A retry is reconsidered on the next normal run if the assignment is still accessible and relevant. Local equivalents:

```powershell
.\.venv\Scripts\python.exe main.py --list-uncertain
.\.venv\Scripts\python.exe main.py --resolve 'assessment:COURSE_ID:ASSIGNMENT_ID' --resolution sent
# Only after confirming no message was received:
.\.venv\Scripts\python.exe main.py --resolve 'assessment:COURSE_ID:ASSIGNMENT_ID' --resolution retry
```

Local mode and GitHub mode have independent histories. Running live notifications from both can produce one notification in each environment. Use GitHub as the single sender after deployment; use local `--dry-run` for later experiments.

### Missing or corrupt state

Normal runs refuse to initialize a missing remote branch. `initialize-state` is for the first installation only. If the branch disappears later, restore it rather than initializing an empty history. Corrupt local files are preserved with a `.quarantine-...` suffix and execution stops; future local runs also refuse to start fresh while that quarantine exists. Unsupported schema versions are preserved unchanged.

To recover remote state, pause the workflow, open the state branch's commit history and locate the latest sound `classroom.sqlite3`. Download it privately. Restore that file on the **state branch** using a private repository upload or Git, retaining the branch history, then resume. Before restoring an older snapshot, reconcile messages received since that snapshot: restoring old history can otherwise resend already delivered alerts. Keep periodic private backups and never delete state to fix an API error.

Database size is limited to 25 MB for snapshots. State history contains private school information and grows with checkpoints; very large long-lived installations may need archival or another durable backend. State failures stop notification sending; logs and GitHub's failed-run indication remain available when a safe Telegram error notification cannot be persisted.

## 10. Material and classification limits

- Reads only content accessible to the authorized account through the selected APIs. No assignment is changed, submitted, deleted or marked complete.
- Many student Forms links are respondent-only or use a published `/d/e/` identifier. Those do not provide a Forms API form ID or permissions; the actual questions may remain unread. Short links, arbitrary websites, external PDFs, YouTube transcripts, images and office-file formats are not scraped.
- Google Docs and Slides use Drive text export. Spreadsheet export covers the first sheet. PDFs use text extraction on at most 20 pages; there is no OCR. Scanned PDFs may supply no usable text.
- Each assignment reads up to 10 distinct materials, 5 MB per download, 16,000 text characters per material and 48,000 total material characters. Truncation is included in the evidence notes. Oversized/unsupported files are marked unread.
- Only published `courseWork` is monitored. Stream announcements, `courseWorkMaterials` posts, student submissions, grades and calendars are separate resources and are not scanned. An exam announced only in those places can be missed.
- Course access errors are logged and other courses continue; incomplete synchronization exits with failure and never sends a success summary.
- AI can misclassify tasks or dates. Check possible assessments yourself. Tests check code behavior against mocked outputs, not empirical model accuracy. Undated coursework older than seven days is excluded before analysis; recently created undated assessments may still require manual date confirmation.
- The first run of a large account may take longer or hit provider quotas. Completed notification checkpoints survive an interrupted run; rerun manually. No promise of complete live integration is made before your own first successful authorized run.

## 11. Logging, tests and troubleshooting

The application logs safe operational summaries to the console. API bodies, assignment text and credential-bearing URLs are not logged. Local `LOG_FILE=monitor.log` additionally writes a file; it is gitignored. Do not enable provider HTTP/debug logging when handling live credentials. Error messages report the failing service without echoing its raw response.

Run checks without real credentials:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
```

| Symptom | Next action |
| --- | --- |
| Missing configuration | Fill the named `.env` values or GitHub Secrets. Keep secrets out of repository files. |
| Google authorization failed | Verify the Desktop client, account, scopes and enabled APIs; reauthorize locally and replace `GOOGLE_TOKEN_JSON`. |
| Browser cannot reach `127.0.0.1:8765` in console mode | Expected. Copy the entire redirect URL from the address bar into the waiting terminal. |
| OAuth state did not match | Restart authorization and use the newly printed link and its redirect in the same still-running terminal. Do not reuse a previous run's URL. |
| Expired or already used code | Restart authorization and paste the new redirect promptly; codes are single-use. |
| Google token has fewer permissions than configured | The token is saved with its actual grants. Reauthorize to grant missing permissions or disable optional features. The command prints the flags and requested scopes selected from `.env`. |
| Google authorized but token could not be saved | Check `GOOGLE_TOKEN_FILE` and credentials-folder write permissions, then authorize again. Existing tokens are preserved on failed writes. |
| Works for a week, then fails | Check whether your external OAuth app is still in Testing; see the Production/token-expiry discussion above. |
| Google HTTP 403 for a course | Check enrollment and school policy; teacher access needs `TEACHER_MODE=true` and new consent. |
| Attached file unread | Verify Drive/Forms APIs, attachment setting and actual API permissions. Access in a respondent browser page may not grant API access. |
| Gemini failure | Check the Gemini key, project free-tier quotas and configured model availability. Failed classifications are retried on later runs. |
| Gemini 504 DEADLINE_EXCEEDED | The application retries with backoff and a 120-second deadline. If failures persist, try `GEMINI_TIMEOUT_SECONDS=180` (maximum 300) and rerun later. Google can still enforce its own service deadlines. |
| Telegram rejects messages | Start the bot, verify token/chat ID, and ensure it is not blocked. Use `telegram_setup.py` to identify the chat. |
| Uncertain delivery | Inspect Telegram and use the review commands; do not delete notification history. |
| GitHub state HTTP 403/422 | Check Contents write permission, branch rules, repository privacy and competing writers. Never force-push stale state. |
| Workflow skipped | Confirm private repository, default-branch dispatch, uploaded workflow files and enabled Actions. |
| Nothing at exactly 16:07 | Check GitHub Actions for queuing/delay; manually run monitor if needed. |
| No assessments notified | Try dry-run; inspect permissions, assessment date horizon, confidence and possible-assessment settings. |

Automatic work completed: source modules, tests, dependencies, state strategy, workflows and safe deployment packaging. Remaining account work: Google project/consent, Gemini key, Telegram bot, private repository, Secrets, first state initialization and your live test run.
