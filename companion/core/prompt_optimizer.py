"""Prompt intake state and optimization helpers."""

from __future__ import annotations

from typing import Final

from companion.core.config import BACKEND_RUNBOOK_FILE

QUESTION_TEXTS: Final[dict[str, str]] = {
    "goal": "Cual es el resultado final exacto que quieres?",
    "paths": "Que archivos/rutas concretas puedo tocar y cuales no?",
    "constraints": "Que restricciones tecnicas debo respetar (framework, versiones, estilo, etc.)?",
    "output_format": "Como quieres el formato de salida (solo cambios, explicacion breve, pasos, etc.)?",
    "validation": "Como validamos que quedo bien (tests, comportamiento esperado, criterios de aceptacion)?",
}

QUESTION_LABELS: Final[dict[str, str]] = {
    "goal": "Resultado final",
    "paths": "Archivos/rutas permitidas y prohibidas",
    "constraints": "Restricciones tecnicas",
    "output_format": "Formato de salida esperado",
    "validation": "Validacion/criterios de aceptacion",
}

CORE_QUESTION_IDS: Final[list[str]] = ["goal", "paths", "constraints"]
OPTIONAL_QUESTION_IDS: Final[list[str]] = ["output_format", "validation"]

FORMAT_HINTS: Final[tuple[str, ...]] = (
    "formato",
    "output",
    "respuesta",
    "json",
    "markdown",
    "tabla",
    "solo cambios",
    "pasos",
    "explicacion",
    "resumen",
)

VALIDATION_HINTS: Final[tuple[str, ...]] = (
    "test",
    "tests",
    "valid",
    "criterio",
    "aceptacion",
    "esperado",
    "verifica",
    "verificar",
    "comprobar",
    "prueba",
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
    return f"Pregunta {idx + 1}/{len(qids)}:\n{text}"


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
    lines.append("Contexto de trabajo")
    lines.append(f"- Directorio actual: {cwd}")
    lines.append("")
    lines.append("Solicitud base del usuario")
    lines.append(draft)
    lines.append("")
    lines.append("Requisitos refinados")
    for qid in ["goal", "paths", "constraints", "output_format", "validation"]:
        label = QUESTION_LABELS[qid]
        value = answers_by_id.get(qid, "").strip() or "No especificado"
        lines.append(f"- {label}: {value}")
    lines.append("")
    lines.append("Instrucciones de ejecucion")
    lines.append(
        "- Implementa los cambios en codigo de forma directa, manteniendo funcionalidad existente."
    )
    lines.append("- Explica brevemente que cambiaste y como validarlo.")
    lines.append(
        "- Si la tarea incluye backend/API/servidor, define y usa un comando explicito para "
        "levantar backend (ejemplo: uv run python main.py o npm run dev). "
        "Si no hay comando claro, pide confirmacion antes de continuar."
    )
    lines.append(
        f"- Si hay backend/API, actualiza el archivo '{BACKEND_RUNBOOK_FILE}' en la raiz del repo "
        "con JSON valido: {\"command\":\"...\", \"workdir\":\"...\", \"port\":..., \"api_prefix\":\"...\"}."
    )

    return "\n".join(lines).strip()
