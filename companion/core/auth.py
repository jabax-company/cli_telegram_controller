"""Authorization helpers."""

from __future__ import annotations

import logging

from telegram import Update

from companion.core.config import PRIVATE_CHAT_ONLY, TELEGRAM_USER_ID
from companion.core.storage import audit

logger = logging.getLogger(__name__)


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if not user or user.id != TELEGRAM_USER_ID:
        _log_unauthorized(update)
        return False
    chat = update.effective_chat
    if PRIVATE_CHAT_ONLY and chat is not None and chat.type != "private":
        logger.warning(
            "Ignoring message from authorized user in non-private chat %s (%s). "
            "Set PRIVATE_CHAT_ONLY=false to allow groups.",
            chat.id,
            chat.type,
        )
        return False
    return True


def _log_unauthorized(update: Update) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    user_desc = f"{user.id} (@{user.username})" if user else "unknown"
    text = ((msg.text or msg.caption or "") if msg else "")[:120]
    chat_id = chat.id if chat else 0
    logger.warning("Unauthorized access attempt from user %s in chat %s", user_desc, chat_id)
    audit(chat_id, f"UNAUTHORIZED: user={user_desc} text={text}")
