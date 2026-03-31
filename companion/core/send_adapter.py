"""Platform-agnostic message-sending abstraction.

TelegramSendAdapter wraps a python-telegram-bot Bot + chat_id.
DiscordSendAdapter wraps a discord.py channel.

Both expose:
  - send_text(text)       – plain text
  - send_html(html_text)  – HTML (Telegram) or plain-stripped (Discord)
  - chat_id               – integer identifier used as state key
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = [
    "SendAdapter",
    "TelegramSendAdapter",
    "DiscordSendAdapter",
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CODE_RE = re.compile(r"<code>(.*?)</code>", re.DOTALL)
_B_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL)
_I_RE = re.compile(r"<i>(.*?)</i>", re.DOTALL)


def _html_to_markdown(html: str) -> str:
    """Very light HTML→Markdown conversion for Discord."""
    text = _CODE_RE.sub(r"`\1`", html)
    text = _B_RE.sub(r"**\1**", text)
    text = _I_RE.sub(r"*\1*", text)
    text = _HTML_TAG_RE.sub("", text)
    # Unescape basic HTML entities
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


# Discord message limit
_DISCORD_MAX = 1990


def _split_discord(text: str) -> list[str]:
    """Split text into Discord-safe chunks (≤ 1990 chars)."""
    chunks: list[str] = []
    while len(text) > _DISCORD_MAX:
        split_at = text.rfind("\n", 0, _DISCORD_MAX)
        if split_at < 200:
            split_at = _DISCORD_MAX
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


class SendAdapter:
    """Base adapter – subclass and implement _send_text / _send_html."""

    def __init__(self, chat_id: int) -> None:
        self._chat_id = chat_id

    @property
    def chat_id(self) -> int:
        return self._chat_id

    async def send_text(self, text: str) -> None:
        raise NotImplementedError

    async def send_html(self, html: str) -> None:
        raise NotImplementedError


class TelegramSendAdapter(SendAdapter):
    """Wraps a python-telegram-bot Bot instance."""

    def __init__(self, bot, chat_id: int) -> None:
        super().__init__(chat_id)
        self._bot = bot

    async def send_text(self, text: str) -> None:
        if not text.strip():
            return
        try:
            await self._bot.send_message(self._chat_id, text)
        except Exception:
            pass

    async def send_html(self, html: str) -> None:
        if not html.strip():
            return
        try:
            await self._bot.send_message(self._chat_id, html, parse_mode="HTML")
        except Exception:
            pass

    @classmethod
    def from_context(cls, context, chat_id: int) -> "TelegramSendAdapter":
        """Build from a Telegram ContextTypes.DEFAULT_TYPE."""
        return cls(context.bot, chat_id)


class DiscordSendAdapter(SendAdapter):
    """Wraps a discord.py TextChannel / Thread / DMChannel."""

    def __init__(self, channel, chat_id: int) -> None:
        super().__init__(chat_id)
        self._channel = channel

    async def send_text(self, text: str) -> None:
        if not text.strip():
            return
        for chunk in _split_discord(text):
            try:
                await self._channel.send(chunk)
            except Exception:
                pass

    async def send_html(self, html: str) -> None:
        """Convert HTML to Markdown and send."""
        text = _html_to_markdown(html)
        await self.send_text(text)
