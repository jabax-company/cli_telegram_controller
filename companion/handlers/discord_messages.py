"""Discord message handlers (plain text messages in channels).

When a user sends a plain text message in a channel where Claude mode is
active, it is forwarded directly to the AI engine, mirroring the Telegram
handle_message() behaviour.
"""

from __future__ import annotations

from companion.core.claude_runtime import run_task
from companion.core.config import DISCORD_USER_ID
from companion.core.security import blocked_match
from companion.core.send_adapter import DiscordSendAdapter
from companion.core.state import get_state, maybe_inject_resume_prompt
from companion.core.storage import audit


def _is_authorized(user_id: int) -> bool:
    return user_id == DISCORD_USER_ID


async def handle_message(message) -> None:
    """Handle a plain text Discord message in claude_mode."""
    if not _is_authorized(message.author.id):
        return

    # Use channel_id as chat_id for state management
    chat_id = message.channel.id
    state = get_state(chat_id)
    text = (message.content or "").strip()

    if not text:
        return

    # Pending confirmation (blocked pattern confirm)
    if state["pending_confirm"]:
        if text.upper() == "YES":
            prompt = state["pending_confirm"]
            state["pending_confirm"] = None
            prompt = maybe_inject_resume_prompt(state, prompt)
            audit(chat_id, f"DISCORD CONFIRMED_BLOCKED: {prompt}")
            adapter = DiscordSendAdapter(message.channel, chat_id)
            await run_task(chat_id, prompt, adapter)
        else:
            state["pending_confirm"] = None
            await message.channel.send("Cancelled.")
        return

    if state["session_active"]:
        await message.channel.send("Claude is working. Please wait.")
        return

    if not state.get("claude_mode"):
        return

    matched = blocked_match(text)
    if matched is not None:
        state["pending_confirm"] = text
        await message.channel.send(
            f"Blocked pattern detected.\nMatch: `{matched}`\n"
            "Reply **YES** to run anyway, or anything else to cancel."
        )
        return

    prompt = maybe_inject_resume_prompt(state, text)
    audit(chat_id, f"DISCORD CLAUDE_MODE: {prompt}")
    adapter = DiscordSendAdapter(message.channel, chat_id)
    await run_task(chat_id, prompt, adapter)
