"""Prompt intake state and optimization helpers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Final

from companion.core.config import BACKEND_RUNBOOK_FILE

logger = logging.getLogger(__name__)


async def enhance_prompt_with_ai(raw_text: str, cwd: str) -> str:
    """Call Claude to rewrite raw_text as a precise, actionable Claude Code prompt.

    Returns the enhanced prompt, or the original text if enhancement fails.
    """
    meta_prompt = (
        "You are an expert in prompt engineering for Claude Code. "
        "Your only task is to rewrite the following text as a clear, precise, and actionable prompt "
        "for a code agent to execute directly. "
        "Do not add explanations, headers, or extra text — return ONLY the improved prompt.\n\n"
        f"Working directory: {cwd}\n\n"
        f"Original text:\n{raw_text}"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--dangerously-skip-permissions",
            "-p", meta_prompt,
            "--output-format", "json",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        raw = stdout.decode("utf-8", errors="replace").strip()
        obj = json.loads(raw)
        enhanced = (obj.get("result") or "").strip()
        if enhanced:
            return enhanced
    except Exception as e:
        logger.warning("enhance_prompt_with_ai failed: %s", e)
    return raw_text

QUESTION_TEXTS: Final[dict[str, str]] = {
    "goal": "What is the exact final result you want?",
    "paths": "Which specific files/paths can I touch and which ones cannot?",
    "constraints": "What technical constraints must I respect (framework, versions, style, etc.)?",
    "output_format": "How do you want the output format (changes only, brief explanation, steps, etc.)?",
    "validation": "How do we validate it is correct (tests, expected behavior, acceptance criteria)?",
}

QUESTION_LABELS: Final[dict[str, str]] = {
    "goal": "Final result",
    "paths": "Allowed and forbidden files/paths",
    "constraints": "Technical constraints",
    "output_format": "Expected output format",
    "validation": "Validation/acceptance criteria",
}

CORE_QUESTION_IDS: Final[list[str]] = ["goal", "paths", "constraints"]
OPTIONAL_QUESTION_IDS: Final[list[str]] = ["output_format", "validation"]

FORMAT_HINTS: Final[tuple[str, ...]] = (
    "format",
    "output",
    "response",
    "json",
    "markdown",
    "table",
    "changes only",
    "steps",
    "explanation",
    "summary",
)

VALIDATION_HINTS: Final[tuple[str, ...]] = (
    "test",
    "tests",
    "valid",
    "criteria",
    "acceptance",
    "expected",
    "verify",
    "check",
    "validate",
    "proof",
)


def _has_any_hint(text: str, hints: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(h in lower for h in hints)


def _select_optional_question_ids(state: dict) -> list[str]:
    draft = (state.get("prompt_draft") or "").strip()
    answers = list(state.get("prompt_answers") or [])
    combined = "\n".join([draft, *answers]).strip()

    optional: list[str] = []
    if not _has_any_hint(combined, FORMAT_HINTS):
        optional.append("output_format")
    if not _has_any_hint(combined, VALIDATION_HINTS):
        optional.append("validation")
    return optional


def start_prompt_intake(state: dict) -> None:
    state["prompt_intake_active"] = True
    state["prompt_draft"] = None
    state["prompt_answers"] = []
    state["prompt_question_ids"] = list(CORE_QUESTION_IDS)
    state["prompt_q_index"] = 0


def clear_prompt_intake(state: dict) -> None:
    state["prompt_intake_active"] = False
    state["prompt_draft"] = None
    state["prompt_answers"] = []
    state["prompt_question_ids"] = []
    state["prompt_q_index"] = 0


def question_count_range() -> tuple[int, int]:
    return len(CORE_QUESTION_IDS), len(CORE_QUESTION_IDS) + len(OPTIONAL_QUESTION_IDS)


def answered_question_count(state: dict) -> int:
    return len(list(state.get("prompt_answers") or []))


def next_question_text(state: dict) -> str | None:
    idx = int(state.get("prompt_q_index", 0))
    qids = list(state.get("prompt_question_ids") or CORE_QUESTION_IDS)
    if idx < 0 or idx >= len(qids):
        return None
    qid = qids[idx]
    text = QUESTION_TEXTS.get(qid)
    if not text:
        return None
    return f"Question {idx + 1}/{len(qids)}:\n{text}"


def record_answer_and_advance(state: dict, answer: str) -> bool:
    answers = list(state.get("prompt_answers") or [])
    answers.append(answer.strip())
    state["prompt_answers"] = answers
    state["prompt_q_index"] = int(state.get("prompt_q_index", 0)) + 1

    qids = list(state.get("prompt_question_ids") or CORE_QUESTION_IDS)
    if (
        state["prompt_q_index"] >= len(CORE_QUESTION_IDS)
        and len(qids) == len(CORE_QUESTION_IDS)
    ):
        qids.extend(_select_optional_question_ids(state))
        state["prompt_question_ids"] = qids

    return int(state["prompt_q_index"]) >= len(qids)


def build_optimized_prompt(state: dict, cwd: str) -> str:
    draft = (state.get("prompt_draft") or "").strip()
    answers = list(state.get("prompt_answers") or [])
    qids = list(state.get("prompt_question_ids") or CORE_QUESTION_IDS)
    answers_by_id: dict[str, str] = {}
    for i, qid in enumerate(qids):
        answers_by_id[qid] = answers[i].strip() if i < len(answers) else ""

    lines: list[str] = []
    lines.append("Working context")
    lines.append(f"- Current directory: {cwd}")
    lines.append("")
    lines.append("User base request")
    lines.append(draft)
    lines.append("")
    lines.append("Refined requirements")
    for qid in ["goal", "paths", "constraints", "output_format", "validation"]:
        label = QUESTION_LABELS[qid]
        value = answers_by_id.get(qid, "").strip() or "Not specified"
        lines.append(f"- {label}: {value}")
    lines.append("")
    lines.append("Execution instructions")
    lines.append(
        "- Implement code changes directly, preserving existing functionality."
    )
    lines.append("- Briefly explain what you changed and how to validate it.")
    lines.append(
        "- If the task involves a backend/API/server, define and use an explicit command to "
        "start the backend (e.g. uv run python main.py or npm run dev). "
        "If no clear command exists, ask for confirmation before continuing."
    )
    lines.append(
        f"- If there is a backend/API, update the '{BACKEND_RUNBOOK_FILE}' file at the repo root "
        "with valid JSON: {\"command\":\"...\", \"workdir\":\"...\", \"port\":..., \"api_prefix\":\"...\"}."
    )

    return "\n".join(lines).strip()
