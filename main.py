"""Classroom Monitor CLI. Run once locally or daily in GitHub Actions."""

import argparse
import logging
import sys
from datetime import date, datetime, time, timedelta

from filelock import FileLock, Timeout

from assignment_analyzer import AssignmentAnalyzer
from classroom_client import ClassroomClient, GoogleAPI
from config import Config
from database import Database, timestamp
from errors import DeliveryRejected, DeliveryUncertain, MonitorError, ProviderError
from google_authentication import credentials
from materials import MaterialReader
from models import BERLIN
from state_store import GitHubState, LocalState
from telegram_bot import TelegramBot, deliver, format_assessment

log = logging.getLogger("classroom_monitor")


def configure_logging(config):
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.log_file:
        handlers.append(logging.FileHandler(config.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )
    # Provider debug/HTTP logs can contain token-bearing URLs. Never enable them here.
    for name in [
        "httpx",
        "httpcore",
        "urllib3",
        "requests",
        "google",
        "google_auth_oauthlib",
        "oauthlib",
        "google.genai",
        "pypdf",
    ]:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def relevant(assignment, analysis, config, today: date) -> bool:
    if not analysis.is_assessment or analysis.confidence < config.min_confidence:
        return False
    if analysis.category == "possible" and not config.notify_possible:
        return False
    target = (
        date.fromisoformat(analysis.relevant_date)
        if analysis.relevant_date
        else (assignment.due_at.date() if assignment.due_at else None)
    )
    return target is None or today <= target <= today + timedelta(days=config.lookahead_days)


def run_monitor(
    config,
    database,
    state,
    classroom,
    reader,
    analyzer,
    bot,
    *,
    dry_run=False,
    today=None,
    now=None,
):
    current = now or (
        datetime.combine(today, time.min, tzinfo=BERLIN) if today else datetime.now(BERLIN)
    )
    today = today or current.astimezone(BERLIN).date()
    processed = sent = failures = 0
    skipped = 0
    database.recover_interrupted()
    try:
        for assignment in classroom.assignments(now=current):
            try:
                # Apply the same gate before all attachment/AI work, including
                # alternative Classroom sources and already-cached assignments.
                if not assignment.eligible_for_analysis(current):
                    skipped += 1
                    continue
                assignment.material_text = reader.read(assignment)
                fingerprint = assignment.fingerprint(config.model)
                analysis = database.cached_analysis(assignment, fingerprint)
                if analysis is None:
                    analysis = analyzer.analyze(assignment)
                database.store_assignment(assignment, fingerprint, analysis)
                processed += 1
                if not relevant(assignment, analysis, config, today):
                    continue
                if dry_run:
                    log.info(
                        "Dry run: found a relevant %s assessment (%s%% confidence).",
                        analysis.category,
                        analysis.confidence,
                    )
                    continue
                key = f"assessment:{assignment.course_id}:{assignment.id}"
                database.enqueue(key, format_assessment(assignment, analysis))
                sent += int(deliver(database, state, bot, key))
            except (ProviderError, DeliveryRejected, DeliveryUncertain) as exc:
                failures += 1
                log.error("One assignment could not be completed: %s", exc)
    except ProviderError as exc:
        failures += 1
        log.error("Course synchronization incomplete: %s", exc)
    failures += classroom.failures
    uncertain = len(database.uncertain())
    if uncertain:
        log.error(
            "%s notification(s) have uncertain delivery. Use --list-uncertain and check Telegram.",
            uncertain,
        )
        failures += 1
    if not dry_run:
        if failures:
            key = "error:" + today.isoformat()
            database.enqueue(
                key,
                "⚠️ <b>Ошибка проверки заданий</b>\nПроверка выполнена не полностью. Посмотрите журнал запуска в GitHub Actions. Если статус доставки неизвестен, сначала проверьте переписку в Telegram.",
            )
        elif sent == 0 and config.daily_summary:
            key = "summary:" + today.isoformat()
            database.enqueue(
                key,
                f"✅ <b>Ежедневная сводка</b>\nПроверено заданий: {processed}. Новых проверочных работ не найдено.",
            )
        else:
            key = None
        if key:
            try:
                deliver(database, state, bot, key)
            except (DeliveryRejected, DeliveryUncertain) as exc:
                failures += 1
                log.error("Status notification failed: %s", exc)
        if not failures:
            database.set_metadata("last_successful_sync", timestamp())
    state.save(database)
    if skipped:
        log.info(
            "%d ineligible coursework items skipped before attachment reads and Gemini.", skipped
        )
    log.info(
        "Finished: %s assignments processed, %s new assessments sent, %s failures. Dry run: %s.",
        processed,
        sent,
        failures,
        dry_run,
    )
    return 1 if failures else 0


def parser():
    result = argparse.ArgumentParser(
        description="Read Google Classroom and notify about new assessments."
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Google Classroom and call Gemini, but send no Telegram messages.",
    )
    result.add_argument(
        "--check-config", action="store_true", help="Validate settings without calling APIs."
    )
    result.add_argument(
        "--initialize-state",
        action="store_true",
        help="First-time setup only: initialize the dedicated remote state branch without sending messages.",
    )
    result.add_argument(
        "--list-uncertain",
        action="store_true",
        help="List delivery attempts requiring manual review.",
    )
    result.add_argument(
        "--resolve", metavar="KEY", help="Resolve an uncertain delivery after checking Telegram."
    )
    result.add_argument(
        "--resolution", choices=["sent", "retry"], help="Mark as sent or allow a future resend."
    )
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if bool(args.resolve) != bool(args.resolution):
        parser().error("--resolve and --resolution must be supplied together")
    database = None
    state = None
    try:
        maintenance = args.list_uncertain or bool(args.resolve) or args.initialize_state
        config = Config.load(auth_only=maintenance)
        configure_logging(config)
        if args.check_config:
            if not config.google_token_json and not config.token_file.exists():
                raise ProviderError("Google token missing. Run python authorize_google.py first.")
            log.info("Required configuration is present. No external API connection was tested.")
            return 0
        config.database_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(config.database_path) + ".lock", timeout=0):
            state = (
                GitHubState(
                    config.github_repository,
                    config.github_token,
                    config.state_branch,
                    allow_initialize=args.initialize_state,
                )
                if config.state_backend == "github"
                else LocalState()
            )
            state.restore(config.database_path)
            database = Database(config.database_path)
            if maintenance:
                database.recover_interrupted()
                if args.resolve:
                    row = database.notification(args.resolve)
                    if not row or row["status"] != "uncertain":
                        raise ProviderError("That notification does not have uncertain delivery.")
                    database.finish(
                        args.resolve, "sent" if args.resolution == "sent" else "pending"
                    )
                    log.info("Delivery review saved. The next normal run applies this decision.")
                for row in database.uncertain():
                    print(f"{row['key']} | {row['status']} | {row['updated_at']}")
                state.save(database)
                if args.initialize_state:
                    log.info("State initialized or verified. No messages were sent.")
                return 0
            try:
                api = GoogleAPI(credentials(config))
                with AssignmentAnalyzer(
                    config.gemini_api_key,
                    config.model,
                    timeout_seconds=config.gemini_timeout_seconds,
                    max_attempts=config.gemini_max_attempts,
                    retry_base_seconds=config.gemini_retry_base_seconds,
                ) as analyzer:
                    return run_monitor(
                        config,
                        database,
                        state,
                        ClassroomClient(api),
                        MaterialReader(api, config.read_attachments),
                        analyzer,
                        TelegramBot(config.telegram_token, config.telegram_chat_id),
                        dry_run=args.dry_run,
                    )
            except ProviderError as exc:
                # Authentication can fail before we even obtain a course list.
                log.error("%s", exc)
                key = "error:" + datetime.now(BERLIN).date().isoformat()
                database.recover_interrupted()
                database.enqueue(
                    key,
                    "⚠️ <b>Ошибка запуска</b>\nНе удалось подключиться к Google Classroom или Gemini. Проверьте журнал выполнения и настройки доступа.",
                )
                if not args.dry_run:
                    deliver(
                        database,
                        state,
                        TelegramBot(config.telegram_token, config.telegram_chat_id),
                        key,
                    )
                state.save(database)
                return 1
    except Timeout:
        log.error("Another local monitor is running. Wait for it to finish.")
        return 1
    except MonitorError as exc:
        # All MonitorError strings are authored locally, never raw upstream responses.
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error(
            "Unexpected failure (%s). No provider response was logged to protect credentials. Run tests and check configuration and state permissions.",
            type(exc).__name__,
        )
        return 1
    finally:
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
