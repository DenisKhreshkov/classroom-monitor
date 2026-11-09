"""Telegram formatting and delivery without ambiguous automatic retries."""

import html
import time
from urllib.parse import urlparse

import requests

from errors import DeliveryRejected, DeliveryUncertain


def shorten(value: str, limit: int) -> str:
    # Bound UTF-16 units conservatively for Telegram, including emoji. Truncate
    # before escaping so neither Unicode surrogate pairs nor HTML entities break.
    text = str(value)
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def escape(value: str, limit: int = 500) -> str:
    return html.escape(shorten(value, limit), quote=True)


def format_assessment(assignment, analysis) -> str:
    label = (
        "📝 Новая проверочная работа"
        if analysis.category == "definite"
        else "🔎 Возможная проверочная работа"
    )
    due = (
        assignment.due_at.strftime("%d.%m.%Y %H:%M") + " (Берлин)"
        if assignment.due_at
        else "Не указан"
    )
    category = {
        "definite": "подтверждено",
        "possible": "нужна проверка",
        "homework": "домашнее задание",
    }[analysis.category]
    description = assignment.raw.get("description") or ""
    parts = [
        f"<b>{label}</b>",
        f"<b>{escape(assignment.title, 200)}</b>",
        f"Предмет: {escape(assignment.course_name, 120)}",
        f"Тип работы: {escape(analysis.assessment_type, 80)}",
        f"Срок сдачи: {due}",
        f"Дата по анализу: {escape(analysis.relevant_date or 'Неизвестно', 30)}",
        f"Уверенность ИИ: {analysis.confidence}% ({category})",
        "<b>Описание задания:</b>",
        escape(description, 1400) if description else "Не указано",
    ]
    if len(description.encode("utf-16-le")) > 2800:
        parts.append("Полный текст описания — по ссылке на задание.")
    parts.append(f"Обоснование: {escape(analysis.reason, 500)}")
    if analysis.important_details:
        parts.append("<b>Важные сведения:</b>")
    for detail in analysis.important_details[:6]:
        parts.append("• " + escape(detail, 90))
    parsed = urlparse(assignment.link)
    if (
        parsed.scheme == "https"
        and parsed.hostname == "classroom.google.com"
        and len(assignment.link) < 800
    ):
        parts.append(
            f'<a href="{html.escape(assignment.link, quote=True)}">Открыть в Google Classroom</a>'
        )
    if analysis.category == "possible":
        parts.append("Проверьте дату и условия оценивания самостоятельно.")
    return "\n".join(parts)


class TelegramBot:
    def __init__(self, token: str, chat_id: str, session=None):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.session = session or requests.Session()

    def send(self, text: str) -> str:
        for attempt in range(3):
            try:
                response = self.session.post(
                    self.url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "link_preview_options": {"is_disabled": True},
                    },
                    timeout=(10, 30),
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise DeliveryUncertain(
                    "Telegram delivery is uncertain after a network failure. Check the chat before resolving it."
                ) from None
            try:
                data = response.json()
            except ValueError:
                raise DeliveryUncertain(
                    "Telegram returned an unreadable response. Check the chat before resolving delivery."
                ) from None
            if response.status_code == 429 and data.get("ok") is False:
                delay = data.get("parameters", {}).get("retry_after", 5)
                if attempt < 2 and isinstance(delay, (int, float)) and 0 <= delay <= 30:
                    time.sleep(delay)
                    continue
                raise DeliveryRejected(
                    "Telegram rate limit reached. Delivery will retry on a later run."
                )
            if response.status_code == 200 and data.get("ok") is True:
                message_id = data.get("result", {}).get("message_id")
                if message_id is None:
                    raise DeliveryUncertain(
                        "Telegram accepted the request without a message ID; check the chat."
                    )
                return str(message_id)
            if 400 <= response.status_code < 500 and data.get("ok") is False:
                raise DeliveryRejected(
                    "Telegram rejected the message. Check bot token, chat ID and that you started the bot."
                )
            raise DeliveryUncertain(
                "Telegram delivery is uncertain after a server error. Check the chat."
            )
        raise DeliveryRejected("Telegram rate limited the message.")


def deliver(database, state, bot: TelegramBot, key: str) -> bool:
    if not database.claim(key):
        return False
    # A failed checkpoint MUST abort before making any Telegram request.
    state.save(database)
    try:
        message_id = bot.send(database.notification(key)["body"])
    except DeliveryRejected:
        database.finish(key, "pending")
        state.save(database)
        raise
    except DeliveryUncertain:
        database.finish(key, "uncertain")
        state.save(database)
        raise
    database.finish(key, "sent", message_id)
    state.save(database)
    return True
