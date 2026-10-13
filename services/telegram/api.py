"""The Telegram Bot API, the few calls the bot needs.

The token is part of every request URL, so it must never reach a log or an error: errors are
rebuilt without the URL, and the bot process keeps the HTTP library's request log quiet.
"""

from typing import Any

import httpx

POLL_SECONDS = 10  # long poll: Telegram holds the request open until a message or this timeout


class TelegramError(Exception):
    """A call failed. The message never contains the token."""


class TelegramApi:
    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.Client(timeout=POLL_SECONDS + 15)

    def __repr__(self) -> str:
        return "TelegramApi(token=***)"

    def get_updates(self, offset: int | None, timeout: int = POLL_SECONDS) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout,
                                  "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params)

    def send(self, chat_id: int, text: str,
             buttons: list[tuple[str, str]] | None = None) -> None:
        """`buttons` are (label, callback data) shown under the message, in one row."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000],
                                  "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in buttons]]}
        self._call("sendMessage", params)

    def answer_button(self, callback_id: str, text: str) -> None:
        """Tell Telegram the button press was handled, with a short note on the screen."""
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    def drop_buttons(self, chat_id: int, message_id: int) -> None:
        """Take the buttons off a message, so a question is answered once."""
        self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id})

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        try:
            response = self._client.post(f"{self._base}/{method}", json=params)
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError(f"Telegram {method} did not answer") from None
        if not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description", "error") if isinstance(data, dict) else "error"
            raise TelegramError(f"Telegram {method}: {description}")
        return data["result"]
