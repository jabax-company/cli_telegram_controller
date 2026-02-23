import asyncio
import logging
import os

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_USER_ID = int(os.environ["TELEGRAM_USER_ID"])
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
GH_PAT = os.environ["GH_PAT"]
WORKFLOW_REPO_OWNER = os.environ["GITHUB_WORKFLOW_REPO_OWNER"]
WORKFLOW_REPO_NAME = os.environ["GITHUB_WORKFLOW_REPO_NAME"]

RAMA_BASE = "main"

anthropic = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ── In-memory sessions ────────────────────────────────────────────────────────
# {chat_id: {"repo", "repo_context", "messages", "refined_prompt"}}
sessions: dict[int, dict] = {}

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
Eres un asistente especializado en refinar tareas de desarrollo de software.

Contexto del repositorio objetivo ({repo}):
--- README ---
{readme}
--- ÁRBOL DE ARCHIVOS ---
{tree}
--- FIN DEL CONTEXTO ---

Tu objetivo es ayudar al usuario a definir claramente una tarea de desarrollo \
para que un agente de código autónomo (Claude Code) pueda ejecutarla sin ambigüedades.

Proceso:
1. Analiza la idea del usuario en el contexto del repositorio.
2. Haz UNA sola pregunta de clarificación a la vez. Máximo 3 rondas en total.
3. Cuando tengas suficiente información — o tras la tercera respuesta del usuario —, \
genera el prompt final.

Cuando estés listo, responde EXACTAMENTE con este formato y nada más fuera de él:

<prompt_final>
[Prompt detallado y accionable para Claude Code. Incluye: qué hacer, archivos \
relevantes, comportamiento esperado, restricciones importantes.]
</prompt_final>

Hasta ese momento, solo haz preguntas. Una por mensaje. Sin código, sin implementaciones.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fresh_session(repo: str | None = None, repo_context: dict | None = None) -> dict:
    return {
        "repo": repo,
        "repo_context": repo_context,
        "messages": [],
        "refined_prompt": None,
    }


def get_session(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = _fresh_session()
    return sessions[chat_id]


def is_authorized(update: Update) -> bool:
    return update.effective_user.id == TELEGRAM_USER_ID


async def fetch_repo_context(owner: str, repo: str) -> tuple[str, str]:
    """Returns (readme, file_tree) from the GitHub API."""
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        # README
        try:
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers={**headers, "Accept": "application/vnd.github.raw+json"},
            )
            readme = r.text[:3000] if r.status_code == 200 else "(sin README)"
        except Exception:
            readme = "(error al obtener README)"

        # File tree (blobs only, up to 200 entries)
        try:
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
                headers=headers,
            )
            if r.status_code == 200:
                paths = [
                    item["path"]
                    for item in r.json().get("tree", [])
                    if item["type"] == "blob"
                ]
                tree = "\n".join(paths[:200])
            else:
                tree = "(sin árbol de archivos)"
        except Exception:
            tree = "(error al obtener árbol)"

    return readme, tree


async def trigger_workflow(chat_id: int, prompt: str, repo: str) -> str:
    """Fires workflow_dispatch and returns the run URL."""
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": "main",
        "inputs": {
            "prompt": prompt,
            "repo_objetivo": repo,
            "rama_base": RAMA_BASE,
            "chat_id": str(chat_id),
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.github.com/repos/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}"
            f"/actions/workflows/agente.yml/dispatches",
            headers=headers,
            json=payload,
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"workflow_dispatch falló: {r.status_code} — {r.text}")

        # Brief wait so the run appears in the list
        await asyncio.sleep(3)
        r2 = await client.get(
            f"https://api.github.com/repos/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}"
            f"/actions/runs?per_page=1",
            headers=headers,
        )
        runs = r2.json().get("workflow_runs", [])
        if runs:
            return runs[0]["html_url"]

    return f"https://github.com/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}/actions"


# ── Command handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "*Mobile Coding Agent*\n\n"
        "Comandos:\n"
        "• `/repo owner/nombre` — Establece el repositorio objetivo\n"
        "• Escribe tu idea — Inicia el refinamiento\n"
        "• `/cancelar` — Resetea la conversación actual\n\n"
        "Empieza con `/repo` para configurar el repositorio.",
        parse_mode="Markdown",
    )


async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    args = context.args
    if not args or "/" not in args[0]:
        await update.message.reply_text(
            "Uso: `/repo owner/nombre-repo`", parse_mode="Markdown"
        )
        return

    repo = args[0].strip()
    owner, repo_name = repo.split("/", 1)

    msg = await update.message.reply_text(
        f"⏳ Obteniendo contexto de `{repo}`…", parse_mode="Markdown"
    )
    try:
        readme, tree = await fetch_repo_context(owner, repo_name)
    except Exception as e:
        await msg.edit_text(f"❌ Error al acceder al repo: {e}")
        return

    sessions[update.effective_chat.id] = _fresh_session(
        repo=repo,
        repo_context={"readme": readme, "tree": tree},
    )
    await msg.edit_text(
        f"✅ Repositorio configurado: `{repo}`\n\nAhora dime qué quieres implementar.",
        parse_mode="Markdown",
    )


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_session(update.effective_chat.id)
    sessions[update.effective_chat.id] = _fresh_session(
        repo=s["repo"], repo_context=s["repo_context"]
    )
    await update.message.reply_text(
        "Conversación reseteada. Dime qué quieres implementar."
    )


# ── Message handler ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    s = get_session(chat_id)

    if not s["repo"]:
        await update.message.reply_text(
            "Primero configura el repositorio con `/repo owner/nombre`",
            parse_mode="Markdown",
        )
        return

    if s["refined_prompt"]:
        await update.message.reply_text(
            "Hay un prompt pendiente de ejecutar. "
            "Usa el botón ✅ para ejecutar o ❌ para cancelar."
        )
        return

    s["messages"].append({"role": "user", "content": update.message.text})

    ctx = s["repo_context"]
    system = SYSTEM_PROMPT.format(
        repo=s["repo"],
        readme=ctx["readme"],
        tree=ctx["tree"],
    )

    placeholder = await update.message.reply_text("✍️")
    try:
        response = await anthropic.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system,
            messages=s["messages"],
        )
        reply = response.content[0].text
    except Exception as e:
        await placeholder.edit_text(f"❌ Error Anthropic: {e}")
        return

    s["messages"].append({"role": "assistant", "content": reply})

    if "<prompt_final>" in reply and "</prompt_final>" in reply:
        start = reply.index("<prompt_final>") + len("<prompt_final>")
        end = reply.index("</prompt_final>")
        refined = reply[start:end].strip()
        s["refined_prompt"] = refined

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ejecutar", callback_data="ejecutar"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancelar"),
            ]
        ])
        await placeholder.edit_text(
            f"📋 *Prompt generado:*\n\n```\n{refined}\n```\n\n¿Ejecutamos esto?",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        await placeholder.edit_text(reply)


# ── Callback handler (inline buttons) ────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    chat_id = update.effective_chat.id
    s = get_session(chat_id)

    if query.data == "cancelar":
        sessions[chat_id] = _fresh_session(
            repo=s["repo"], repo_context=s["repo_context"]
        )
        await query.edit_message_text("Cancelado. Dime qué quieres implementar.")
        return

    if query.data == "ejecutar":
        if not s["refined_prompt"]:
            await query.edit_message_text("❌ No hay prompt para ejecutar.")
            return

        await query.edit_message_text("⏳ Disparando workflow…")
        try:
            run_url = await trigger_workflow(
                chat_id=chat_id,
                prompt=s["refined_prompt"],
                repo=s["repo"],
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Error al disparar el workflow: {e}")
            return

        sessions[chat_id] = _fresh_session(
            repo=s["repo"], repo_context=s["repo_context"]
        )
        await query.edit_message_text(
            f"🚀 Workflow en marcha.\n\n"
            f"Síguelo aquí:\n{run_url}\n\n"
            f"Te aviso cuando el PR esté listo."
        )


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("repo", cmd_repo))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot iniciado")
    app.run_polling()


if __name__ == "__main__":
    main()
