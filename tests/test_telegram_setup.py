from unittest.mock import Mock

import pytest
import requests

from errors import ProviderError
from telegram_setup import find_chat_ids


def test_get_chat_ids_without_printing_tokens():
    session = Mock()
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {
        "ok": True,
        "result": [{"message": {"chat": {"id": 456}}}, {"message": {"chat": {"id": 456}}}],
    }
    assert find_chat_ids("fake-token", session) == ["456"]


def test_missing_bot_token():
    with pytest.raises(ProviderError, match="TELEGRAM_BOT_TOKEN"):
        find_chat_ids("")


def test_failed_update_does_not_expose_token():
    session = Mock()
    session.post.side_effect = requests.Timeout("SECRET")
    with pytest.raises(ProviderError) as exc:
        find_chat_ids("SECRET", session)
    assert "SECRET" not in str(exc.value)
