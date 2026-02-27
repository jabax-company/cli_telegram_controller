"""Authorization helpers."""

from __future__ import annotations

from telegram import Update

from companion.core.config import TELEGRAM_USER_ID


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == TELEGRAM_USER_ID)

