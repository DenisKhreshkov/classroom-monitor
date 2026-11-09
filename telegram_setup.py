"""Read your bot's updates and print chat IDs without putting its token in a URL bar."""

import requests

from config import Config
from errors import MonitorError, ProviderError


def find_chat_ids(token: str, session=None) -> list[str]:
    if not token:
        raise ProviderError(
            "Set TELEGRAM_BOT_TOKEN in .env first, then message your bot in Telegram."
        )
    session = session or requests.Session()
    try:
        response = session.post(
            f"https://api.telegram.org/bot{token}/getUpdates",
            json={"timeout": 0, "allowed_updates": ["message"]},
            timeout=(10, 30),
            allow_redirects=False,
        )
        data = response.json()
        if response.status_code != 200 or data.get("ok") is not True:
            raise ProviderError(
                "Telegram rejected getUpdates. Check the bot token and remove any existing bot webhook."
            )
        return sorted(
            {
                str(item["message"]["chat"]["id"])
                for item in data.get("result", [])
                if "message" in item
            }
        )
    except (requests.RequestException, ValueError, KeyError):
        raise ProviderError(
            "Could not read Telegram updates. Check your network and bot configuration."
        ) from None


def main() -> int:
    try:
        config = Config.load(auth_only=True)
        ids = find_chat_ids(config.telegram_token)
        if not ids:
            print(
                "No messages found. Open your bot in Telegram, press Start, send hello, and run this command again."
            )
            return 1
        print("Chat IDs (choose your own private conversation and set TELEGRAM_CHAT_ID in .env):")
        print("\n".join(ids))
        return 0
    except MonitorError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
