import asyncio
import logging
import os
import re

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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# â"€â"€ Config â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_USER_ID = int(os.environ["TELEGRAM_USER_ID"])
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
GH_PAT = os.environ["GH_PAT"]
WORKFLOW_REPO_OWNER = os.environ["GITHUB_WORKFLOW_REPO_OWNER"]
WORKFLOW_REPO_NAME = os.environ["GITHUB_WORKFLOW_REPO_NAME"]

RAMA_BASE = "main"
REPOS_PER_PAGE = 8

anthropic = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# â"€â"€ In-memory sessions â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# {chat_id: {"repo", "repo_context", "messages", "refined_prompt"}}
sessions: dict[int, dict] = {}

# â"€â"€ System prompt â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
SYSTEM_PROMPT = """\
Eres un asistente especializado en refinar tareas de desarrollo de software.

Contexto del repositorio objetivo ({repo}):
--- README ---
{readme}
--- ÃRBOL DE ARCHIVOS ---
{tree}
--- FIN DEL CONTEXTO ---

Tu objetivo es ayudar al usuario a definir claramente una tarea de desarrollo \
para que un agente de cÃ³digo autÃ³nomo (Claude Code) pueda ejecutarla sin ambigÃ¼edades.

Proceso:
1. Analiza la idea del usuario en el contexto del repositorio.
2. Haz UNA sola pregunta de clarificaciÃ³n a la vez. MÃ¡ximo 3 rondas en total.
3. Cuando tengas suficiente informaciÃ³n â€" o tras la tercera respuesta del usuario â€", \
genera el prompt final.

Cuando estÃ©s listo, responde EXACTAMENTE con este formato y nada mÃ¡s fuera de Ã©l:

<prompt_final>
[Prompt detallado y accionable para Claude Code. Incluye: quÃ© hacer, archivos \
relevantes, comportamiento esperado, restricciones importantes.]
</prompt_final>

Hasta ese momento, solo haz preguntas. Una por mensaje. Sin cÃ³digo, sin implementaciones.\
"""


# â"€â"€ Helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
def _fresh_session(
    repo: str | None = None,
    repo_context: dict | None = None,
    rama_activa: str | None = None,
    pending_pr: bool = False,
) -> dict:
    return {
        "repo": repo,
        "repo_context": repo_context,
        "messages": [],
        "refined_prompt": None,
        "rama_activa": rama_activa,
        "pending_pr": pending_pr,
        "repo_options": [],
        "repo_page": 0,
    }


def get_session(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = _fresh_session()
    return sessions[chat_id]


def is_authorized(update: Update) -> bool:
    return update.effective_user.id == TELEGRAM_USER_ID


def _repo_selector_text(repos: list[str], page: int) -> str:
    total = len(repos)
    total_pages = max(1, (total + REPOS_PER_PAGE - 1) // REPOS_PER_PAGE)
    start = page * REPOS_PER_PAGE
    end = min(start + REPOS_PER_PAGE, total)
    lines = [
        "En que repo quieres trabajar?",
        f"Pagina {page + 1}/{total_pages} ({total} repos visibles)",
        "",
    ]
    for idx in range(start, end):
        lines.append(f"{idx + 1}. {repos[idx]}")
    lines.extend(
        [
            "",
            "Selecciona un repo con los botones, o escribe `/repo owner/nombre`.",
            "Tambien puedes responder con un numero de la lista.",
        ]
    )
    return "\n".join(lines)


def _repo_selector_markup(repos: list[str], page: int) -> InlineKeyboardMarkup:
    start = page * REPOS_PER_PAGE
    end = min(start + REPOS_PER_PAGE, len(repos))
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(start, end):
        rows.append([InlineKeyboardButton(repos[idx], callback_data=f"repo_pick:{idx}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("<<", callback_data=f"repo_page:{page - 1}"))
    if end < len(repos):
        nav.append(InlineKeyboardButton(">>", callback_data=f"repo_page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def list_available_repos(limit: int = 200) -> list[str]:
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {
        "per_page": 100,
        "sort": "updated",
        "direction": "desc",
        "affiliation": "owner,collaborator,organization_member",
    }
    repos: list[str] = []
    page = 1

    async with httpx.AsyncClient(timeout=20) as client:
        while len(repos) < limit:
            r = await client.get(
                "https://api.github.com/user/repos",
                headers=headers,
                params={**params, "page": page},
            )
            if r.status_code != 200:
                raise RuntimeError(f"No se pudieron listar repos: {r.status_code} - {r.text}")
            batch = r.json()
            if not batch:
                break
            repos.extend(
                item["full_name"]
                for item in batch
                if isinstance(item, dict) and "full_name" in item
            )
            if len(batch) < 100:
                break
            page += 1

    return repos[:limit]


async def configure_repo_session(chat_id: int, repo: str) -> None:
    owner, repo_name = repo.split("/", 1)
    readme, tree = await fetch_repo_context(owner, repo_name)
    sessions[chat_id] = _fresh_session(
        repo=repo,
        repo_context={"readme": readme, "tree": tree},
    )


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
                tree = "(sin Ã¡rbol de archivos)"
        except Exception:
            tree = "(error al obtener Ã¡rbol)"

    return readme, tree


async def trigger_workflow(
    chat_id: int,
    prompt: str,
    repo: str,
    rama_existente: str | None = None,
    abrir_pr: bool = False,
) -> tuple[str, str]:
    """Fires workflow_dispatch. Returns (run_url, branch_name)."""
    from datetime import datetime
    branch = rama_existente or f"agent/{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

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
            "rama_existente": rama_existente or "",
            "abrir_pr": "true" if abrir_pr else "false",
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        dispatch_url = (
            f"https://api.github.com/repos/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}"
            f"/actions/workflows/agente.yml/dispatches"
        )
        r = await client.post(
            dispatch_url,
            headers=headers,
            json=payload,
        )

        # Backward compatibility: some deployed workflow files may not yet
        # declare newer inputs like rama_existente / abrir_pr.
        if r.status_code == 422:
            try:
                message = str(r.json().get("message", ""))
            except Exception:
                message = r.text or ""

            if "Unexpected inputs provided" in message:
                match = re.search(r"Unexpected inputs provided:\s*\[(.*?)\]", message)
                unsupported: set[str] = set()
                if match:
                    unsupported = {
                        item.strip().strip('"\'')
                        for item in match.group(1).split(",")
                        if item.strip()
                    }

                if unsupported:
                    retry_inputs = {
                        key: value
                        for key, value in payload["inputs"].items()
                        if key not in unsupported
                    }
                    r = await client.post(
                        dispatch_url,
                        headers=headers,
                        json={"ref": "main", "inputs": retry_inputs},
                    )

        if r.status_code not in (200, 204):
            raise RuntimeError(f"workflow_dispatch fallo: {r.status_code} - {r.text}")

        await asyncio.sleep(3)
        r2 = await client.get(
            f"https://api.github.com/repos/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}"
            f"/actions/runs?per_page=1",
            headers=headers,
        )
        runs = r2.json().get("workflow_runs", [])
        run_url = (
            runs[0]["html_url"]
            if runs
            else f"https://github.com/{WORKFLOW_REPO_OWNER}/{WORKFLOW_REPO_NAME}/actions"
        )

    return run_url, branch


# â"€â"€ Command handlers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "*Mobile Coding Agent*\n\n"
        "Comandos:\n"
        "- `/repo` - Lista y selecciona repos disponibles\n"
        "- `/repo owner/nombre` - Establece el repositorio objetivo\n"
        "- Escribe tu idea - Inicia el refinamiento\n"
        "- `/rama` - Ver rama activa (los mensajes se acumulan en la misma rama)\n"
        "- `/rama nueva` - Empezar en una rama nueva\n"
        "- `/pr` - La proxima ejecucion abre o reutiliza el PR de la rama activa\n"
        "- `/cancelar` - Resetea la conversacion actual\n\n"
        "Empieza con `/repo` para configurar el repositorio.",
        parse_mode="Markdown",
    )

async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    s = get_session(chat_id)
    args = context.args

    if not args:
        loading = await update.message.reply_text("Listando repos disponibles...")
        try:
            repos = await list_available_repos()
        except Exception as e:
            await loading.edit_text(f"Error al listar repos: {e}")
            return
        if not repos:
            await loading.edit_text(
                "No encontre repos visibles con este GH_PAT.\n"
                "Tambien puedes enviarme `/repo owner/nombre`.",
                parse_mode="Markdown",
            )
            return
        s["repo_options"] = repos
        s["repo_page"] = 0
        await loading.edit_text(
            _repo_selector_text(repos, 0),
            reply_markup=_repo_selector_markup(repos, 0),
        )
        return

    candidate = args[0].strip()
    if candidate.isdigit() and s.get("repo_options"):
        idx = int(candidate) - 1
        repos = s["repo_options"]
        if 0 <= idx < len(repos):
            candidate = repos[idx]
        else:
            await update.message.reply_text("Ese numero no existe en la lista actual.")
            return

    if "/" not in candidate:
        await update.message.reply_text(
            "Uso: `/repo` para listar o `/repo owner/nombre-repo` para seleccionar.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(
        f"Obteniendo contexto de `{candidate}`...", parse_mode="Markdown"
    )
    try:
        await configure_repo_session(chat_id, candidate)
    except Exception as e:
        await msg.edit_text(f"Error al acceder al repo: {e}")
        return

    await msg.edit_text(
        f"Repositorio configurado: `{candidate}`\n\nAhora dime que quieres implementar.",
        parse_mode="Markdown",
    )

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_session(update.effective_chat.id)
    sessions[update.effective_chat.id] = _fresh_session(
        repo=s["repo"],
        repo_context=s["repo_context"],
        rama_activa=s.get("rama_activa"),
        pending_pr=False,
    )
    await update.message.reply_text(
        "ConversaciÃ³n reseteada. Dime quÃ© quieres implementar."
    )


async def cmd_rama(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_session(update.effective_chat.id)
    rama = s.get("rama_activa")
    pending_pr = s.get("pending_pr", False)
    if context.args and context.args[0] == "nueva":
        sessions[update.effective_chat.id]["rama_activa"] = None
        sessions[update.effective_chat.id]["pending_pr"] = False
        await update.message.reply_text("Rama reseteada. El prÃ³ximo workflow crearÃ¡ una rama nueva.")
    elif rama:
        pr_line = "\nCierre pendiente: `/pr` activado." if pending_pr else ""
        await update.message.reply_text(
            f"Rama activa: `{rama}`\n\nEl prÃ³ximo mensaje seguirÃ¡ en esta rama.\n"
            f"Usa `/rama nueva` para empezar en una rama nueva.{pr_line}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("No hay rama activa. El prÃ³ximo workflow crearÃ¡ una nueva.")


async def cmd_pr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_session(update.effective_chat.id)
    if not s["repo"]:
        await update.message.reply_text(
            "Primero configura el repositorio con `/repo owner/nombre`",
            parse_mode="Markdown",
        )
        return
    sessions[update.effective_chat.id]["pending_pr"] = True
    rama = s.get("rama_activa")
    if rama:
        await update.message.reply_text(
            f"âœ... Cierre con PR activado.\nLa prÃ³xima ejecuciÃ³n trabajarÃ¡ en `{rama}` y abrirÃ¡/reutilizarÃ¡ el PR.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "âœ... Cierre con PR activado.\nLa prÃ³xima ejecuciÃ³n crearÃ¡ una rama y abrirÃ¡ PR.",
        )


# â"€â"€ Message handler â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    s = get_session(chat_id)
    user_text = (update.message.text or "").strip()

    if not s["repo"]:
        candidate: str | None = None

        if user_text.isdigit() and s.get("repo_options"):
            idx = int(user_text) - 1
            repos = s["repo_options"]
            if 0 <= idx < len(repos):
                candidate = repos[idx]
            else:
                await update.message.reply_text("Ese numero no existe en la lista actual.")
                return
        elif "/" in user_text and " " not in user_text:
            candidate = user_text

        if candidate:
            msg = await update.message.reply_text(
                f"Obteniendo contexto de `{candidate}`...", parse_mode="Markdown"
            )
            try:
                await configure_repo_session(chat_id, candidate)
            except Exception as e:
                await msg.edit_text(f"Error al acceder al repo: {e}")
                return
            await msg.edit_text(
                f"Repositorio configurado: `{candidate}`\n\nAhora dime que quieres implementar.",
                parse_mode="Markdown",
            )
            return

        loading = await update.message.reply_text("Necesito que me digas en que repo trabajamos. Listando repos...")
        try:
            repos = await list_available_repos()
        except Exception as e:
            await loading.edit_text(f"Error al listar repos: {e}\nUsa `/repo owner/nombre`.", parse_mode="Markdown")
            return

        if not repos:
            await loading.edit_text(
                "No encontre repos visibles con este GH_PAT.\n"
                "Usa `/repo owner/nombre`.",
                parse_mode="Markdown",
            )
            return

        s["repo_options"] = repos
        s["repo_page"] = 0
        await loading.edit_text(
            _repo_selector_text(repos, 0),
            reply_markup=_repo_selector_markup(repos, 0),
        )
        return

    if s["refined_prompt"]:
        await update.message.reply_text(
            "Hay un prompt pendiente de ejecutar. "
            "Usa el boton Ejecutar para continuar o Cancelar para descartarlo."
        )
        return

    s["messages"].append({"role": "user", "content": update.message.text})

    ctx = s["repo_context"]
    system = SYSTEM_PROMPT.format(
        repo=s["repo"],
        readme=ctx["readme"],
        tree=ctx["tree"],
    )

    placeholder = await update.message.reply_text("Escribiendo...")
    try:
        response = await anthropic.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system,
            messages=s["messages"],
        )
        reply = response.content[0].text
    except Exception as e:
        await placeholder.edit_text(f"Error Anthropic: {e}")
        return

    s["messages"].append({"role": "assistant", "content": reply})

    if "<prompt_final>" in reply and "</prompt_final>" in reply:
        start = reply.index("<prompt_final>") + len("<prompt_final>")
        end = reply.index("</prompt_final>")
        refined = reply[start:end].strip()
        s["refined_prompt"] = refined

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Ejecutar", callback_data="ejecutar"),
                InlineKeyboardButton("Cancelar", callback_data="cancelar"),
            ]
        ])
        await placeholder.edit_text(
            f"Prompt generado:\n\n```\n{refined}\n```\n\nEjecutamos esto?",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        await placeholder.edit_text(reply)

# â"€â"€ Callback handler (inline buttons) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    chat_id = update.effective_chat.id
    s = get_session(chat_id)

    if query.data.startswith("repo_page:"):
        try:
            page = int(query.data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("No pude leer la pagina de repos.")
            return

        repos = s.get("repo_options") or []
        if not repos:
            try:
                repos = await list_available_repos()
            except Exception as e:
                await query.edit_message_text(f"Error al listar repos: {e}")
                return
            s["repo_options"] = repos

        if not repos:
            await query.edit_message_text("No hay repos visibles para seleccionar.")
            return

        max_page = max(0, (len(repos) - 1) // REPOS_PER_PAGE)
        page = max(0, min(page, max_page))
        s["repo_page"] = page
        await query.edit_message_text(
            _repo_selector_text(repos, page),
            reply_markup=_repo_selector_markup(repos, page),
        )
        return

    if query.data.startswith("repo_pick:"):
        try:
            idx = int(query.data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("No pude leer el repo seleccionado.")
            return

        repos = s.get("repo_options") or []
        if not repos or idx < 0 or idx >= len(repos):
            await query.edit_message_text("Ese repo ya no esta disponible. Usa /repo para listar de nuevo.")
            return

        selected = repos[idx]
        await query.edit_message_text(
            f"Obteniendo contexto de `{selected}`...", parse_mode="Markdown"
        )
        try:
            await configure_repo_session(chat_id, selected)
        except Exception as e:
            await query.edit_message_text(f"Error al acceder al repo: {e}")
            return

        await query.edit_message_text(
            f"Repositorio configurado: `{selected}`\n\nAhora dime que quieres implementar.",
            parse_mode="Markdown",
        )
        return

    if query.data == "cancelar":
        sessions[chat_id] = _fresh_session(
            repo=s["repo"],
            repo_context=s["repo_context"],
            rama_activa=s.get("rama_activa"),
            pending_pr=s.get("pending_pr", False),
        )
        await query.edit_message_text("Cancelado. Dime que quieres implementar.")
        return

    if query.data == "ejecutar":
        if not s["refined_prompt"]:
            await query.edit_message_text("No hay prompt para ejecutar.")
            return

        await query.edit_message_text("Disparando workflow...")
        abrir_pr = bool(s.get("pending_pr", False))
        try:
            run_url, branch = await trigger_workflow(
                chat_id=chat_id,
                prompt=s["refined_prompt"],
                repo=s["repo"],
                rama_existente=s.get("rama_activa"),
                abrir_pr=abrir_pr,
            )
        except Exception as e:
            await query.edit_message_text(f"Error al disparar el workflow: {e}")
            return

        sessions[chat_id] = _fresh_session(
            repo=s["repo"],
            repo_context=s["repo_context"],
            rama_activa=branch,
            pending_pr=False,
        )
        cierre_line = "\nModo cierre: este run abrira/reutilizara PR." if abrir_pr else ""
        await query.edit_message_text(
            f"Workflow en marcha.\n\n"
            f"Siguelo aqui:\n{run_url}\n\n"
            f"Rama: `{branch}`\n"
            f"Te aviso cuando termine. El siguiente mensaje seguira en esta rama.{cierre_line}",
            parse_mode="Markdown",
        )

# â"€â"€ Entry point â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("repo", cmd_repo))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("rama", cmd_rama))
    app.add_handler(CommandHandler("pr", cmd_pr))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()




