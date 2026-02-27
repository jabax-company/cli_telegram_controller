"""Server publishing and Cloudflare tunnel runtime."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.config import (
    BACKEND_RUNBOOK_FILE,
    FULLSTACK_BACK_CMD,
    FULLSTACK_BACK_DIR,
    FULLSTACK_FRONT_CMD,
    FULLSTACK_FRONT_DIR,
    FULLSTACK_PROXY,
    SERVE_PORT,
    SERVER_CONFIG_FILE,
    URL_RE,
)
from companion.core.claude_runtime import run_task
from companion.core.state import get_serve_state, get_state, is_serve_active

logger = logging.getLogger(__name__)


@dataclass
class LaunchSpec:
    command: str
    workdir: str
    source: str


@dataclass
class HttpProbe:
    ok: bool
    status: int
    content_type: str
    body_preview: str


def _kill_process(proc) -> None:
    if proc is None:
        return
    try:
        proc.kill()
    except Exception:
        pass


def _kill_processes(procs: list) -> None:
    for proc in procs:
        _kill_process(proc)


def _probe_http_sync(port: int, path: str = "/", timeout: float = 3.0) -> HttpProbe:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Accept": "*/*", "User-Agent": "claude-code-bot-probe"})
        resp = conn.getresponse()
        raw = resp.read(1200)
        try:
            preview = raw.decode("utf-8", errors="replace")
        except Exception:
            preview = ""
        return HttpProbe(
            ok=True,
            status=int(resp.status or 0),
            content_type=(resp.getheader("Content-Type") or "").strip().lower(),
            body_preview=preview.strip(),
        )
    except Exception:
        return HttpProbe(ok=False, status=0, content_type="", body_preview="")
    finally:
        conn.close()


async def probe_http(port: int, path: str = "/", timeout: float = 3.0) -> HttpProbe:
    return await asyncio.to_thread(_probe_http_sync, port, path, timeout)


def _looks_like_frontend_http(probe: HttpProbe) -> bool:
    if not probe.ok:
        return False
    body = (probe.body_preview or "").lower()
    ctype = (probe.content_type or "").lower()
    if "text/html" in ctype:
        return True
    if "<!doctype html" in body or "<html" in body:
        return True
    return False


def _probe_summary(probe: HttpProbe) -> str:
    if not probe.ok:
        return "no HTTP response"
    ctype = probe.content_type or "unknown content-type"
    body = (probe.body_preview or "").strip().replace("\n", " ")
    if len(body) > 120:
        body = body[:120] + "..."
    if body:
        return f"status={probe.status}, type={ctype}, body='{body}'"
    return f"status={probe.status}, type={ctype}"


def _normalize_workdir(base_cwd: str, raw_dir: str) -> str | None:
    if not raw_dir:
        return None
    candidate = Path(raw_dir)
    if not candidate.is_absolute():
        candidate = Path(base_cwd) / candidate
    try:
        resolved = candidate.resolve()
    except Exception:
        return None
    if resolved.is_dir():
        return str(resolved)
    return None


def _read_package_scripts(package_json: Path) -> dict[str, str]:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})
        return scripts if isinstance(scripts, dict) else {}
    except Exception:
        return {}


def _preferred_node_runner(dir_path: Path) -> str:
    if (dir_path / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (dir_path / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _build_script_command(runner: str, script_name: str) -> str:
    if runner == "npm":
        return f"npm run {script_name}"
    return f"{runner} run {script_name}"


def _get_repo_root(base_cwd: str) -> Path:
    base = Path(base_cwd).resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(base),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            root = Path(completed.stdout.strip()).resolve()
            if root.is_dir():
                return root
    except Exception:
        pass
    return base


def _candidate_backend_dirs(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            return
        seen.add(key)
        candidates.append(resolved)

    for rel in (
        "backend",
        "api",
        "server",
        "services/backend",
        "apps/backend",
        "packages/backend",
    ):
        _add(repo_root / rel)

    try:
        for child in sorted(repo_root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            name = child.name.lower()
            if name.startswith(".") or name in {
                "node_modules",
                "dist",
                "build",
                "frontend",
                "front",
                "client",
                "web",
                "ui",
            }:
                continue
            if any(hint in name for hint in ("backend", "api", "server")):
                _add(child)
    except Exception:
        pass

    return candidates


def _load_backend_runbook(base_cwd: str, backend_port: int) -> LaunchSpec | None:
    repo_root = _get_repo_root(base_cwd)
    runbook = (repo_root / BACKEND_RUNBOOK_FILE).resolve()
    if not runbook.is_file():
        return None
    try:
        data = json.loads(runbook.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Invalid backend runbook '%s': %s", runbook, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Invalid backend runbook '%s': expected JSON object.", runbook)
        return None

    command = str(data.get("command", "")).strip()
    if not command:
        logger.warning("Invalid backend runbook '%s': missing 'command'.", runbook)
        return None

    workdir_raw = str(data.get("workdir", "")).strip()
    if workdir_raw:
        workdir = _normalize_workdir(str(repo_root), workdir_raw)
        if not workdir:
            logger.warning("Invalid backend runbook '%s': bad 'workdir' (%s).", runbook, workdir_raw)
            return None
    else:
        workdir = str(repo_root)

    try:
        runbook_port = int(data.get("port", backend_port))
        if runbook_port != backend_port:
            logger.info(
                "Backend runbook suggests port %s, but /server fullstack requested %s.",
                runbook_port,
                backend_port,
            )
    except Exception:
        pass

    return LaunchSpec(
        command=command,
        workdir=workdir,
        source=f"backend runbook '{BACKEND_RUNBOOK_FILE}'",
    )


def _detect_python_backend_launch(
    root_dir: Path,
    backend_port: int,
    source_label: str,
) -> LaunchSpec | None:
    manage = root_dir / "manage.py"
    if manage.is_file():
        return LaunchSpec(
            command=f"python manage.py runserver 127.0.0.1:{backend_port}",
            workdir=str(root_dir),
            source=f"django manage.py in {source_label}",
        )

    for name in ("app.py", "main.py", "server.py"):
        entry = root_dir / name
        if not entry.is_file():
            continue
        if shutil.which("uv") and (root_dir / "pyproject.toml").is_file():
            return LaunchSpec(
                command=f"uv run python {name}",
                workdir=str(root_dir),
                source=f"python entry '{name}' in {source_label} via uv",
            )
        return LaunchSpec(
            command=f"python {name}",
            workdir=str(root_dir),
            source=f"python entry '{name}' in {source_label}",
        )
    return None


def _load_server_config(base_cwd: str) -> dict | None:
    """Load .claude/server.json from the repo root. Returns the parsed dict or None."""
    repo_root = _get_repo_root(base_cwd)
    config_path = (repo_root / SERVER_CONFIG_FILE).resolve()
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Invalid server config '%s': %s", config_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def detect_frontend_launch(base_cwd: str, frontend_port: int | None = None, server_config: dict | None = None) -> LaunchSpec | None:
    env_cmd = FULLSTACK_FRONT_CMD.strip()
    if env_cmd:
        env_dir = _normalize_workdir(base_cwd, FULLSTACK_FRONT_DIR) or str(Path(base_cwd).resolve())
        return LaunchSpec(command=env_cmd, workdir=env_dir, source="FULLSTACK_FRONT_CMD")
    repo_root = _get_repo_root(base_cwd)

    # 0. .claude/server.json takes priority over all auto-detection
    fe = (server_config or {}).get("frontend")
    if fe and isinstance(fe, dict):
        fe_cmd = str(fe.get("cmd", "")).strip()
        fe_dir = str(fe.get("dir", ".")).strip()
        workdir = _normalize_workdir(str(repo_root), fe_dir) or str(repo_root)
        if fe_cmd:
            return LaunchSpec(command=fe_cmd, workdir=workdir, source=SERVER_CONFIG_FILE)
        # dir specified but no cmd: use static server if index.html exists
        if (Path(workdir) / "index.html").is_file():
            port_str = str(frontend_port) if frontend_port else "8080"
            return LaunchSpec(
                command=f"{sys.executable} -m http.server {port_str} --bind 127.0.0.1",
                workdir=workdir,
                source=f"{SERVER_CONFIG_FILE} (static)",
            )

    # 1. Look for npm-based frontend in common subdirs (frontend/, front/, client/, …)
    for rel in ("frontend", "front", "client", "web", "ui", "app"):
        check_dir = repo_root / rel
        if not check_dir.is_dir():
            continue
        pkg = check_dir / "package.json"
        if not pkg.is_file():
            continue
        scripts = _read_package_scripts(pkg)
        runner = _preferred_node_runner(check_dir)
        for script_name in ("dev", "start", "serve"):
            if script_name in scripts:
                return LaunchSpec(
                    command=_build_script_command(runner, script_name),
                    workdir=str(check_dir),
                    source=f"'{script_name}' script in '{check_dir.name}'",
                )

    # 2. npm-based frontend at repo root
    root_pkg = repo_root / "package.json"
    if root_pkg.is_file():
        scripts = _read_package_scripts(root_pkg)
        runner = _preferred_node_runner(repo_root)
        for script_name in ("dev", "start", "serve"):
            if script_name in scripts:
                return LaunchSpec(
                    command=_build_script_command(runner, script_name),
                    workdir=str(repo_root),
                    source=f"'{script_name}' script at repo root",
                )

    # 3. Static HTML: serve with Python's built-in HTTP server
    port_str = str(frontend_port) if frontend_port else "8080"
    for rel in ("frontend", "front", "client", "public", "web", "ui", ""):
        check_dir = repo_root / rel if rel else repo_root
        if check_dir.is_dir() and (check_dir / "index.html").is_file():
            return LaunchSpec(
                command=f"{sys.executable} -m http.server {port_str} --bind 127.0.0.1",
                workdir=str(check_dir),
                source=f"static HTML in '{check_dir.name or 'repo root'}'",
            )

    # 4. Last resort: npm run dev at repo root
    return LaunchSpec(
        command="npm run dev",
        workdir=str(repo_root),
        source="npm run dev at repo root (fallback)",
    )


def _detect_node_backend_launch(repo_root: Path) -> LaunchSpec | None:
    script_order_root = [
        "dev:backend",
        "dev:api",
        "backend",
        "api",
        "server",
        "start:backend",
        "start:api",
        "start",
        "dev",
    ]
    package_json = repo_root / "package.json"
    scripts = _read_package_scripts(package_json) if package_json.is_file() else {}
    if scripts:
        runner = _preferred_node_runner(repo_root)
        for script_name in script_order_root:
            if script_name in scripts:
                return LaunchSpec(
                    command=_build_script_command(runner, script_name),
                    workdir=str(repo_root),
                    source=f"repo root script '{script_name}'",
                )

    script_order_subdir = [
        "dev:backend",
        "dev:api",
        "backend",
        "api",
        "server",
        "start:backend",
        "start:api",
        "dev",
        "start",
    ]
    for backend_dir in _candidate_backend_dirs(repo_root):
        package_json = backend_dir / "package.json"
        scripts = _read_package_scripts(package_json) if package_json.is_file() else {}
        if not scripts:
            continue
        runner = _preferred_node_runner(backend_dir)
        for script_name in script_order_subdir:
            if script_name in scripts:
                return LaunchSpec(
                    command=_build_script_command(runner, script_name),
                    workdir=str(backend_dir),
                    source=f"backend dir '{backend_dir.name}' script '{script_name}'",
                )
    return None


def detect_backend_launch(base_cwd: str, backend_port: int, server_config: dict | None = None) -> LaunchSpec | None:
    env_cmd = FULLSTACK_BACK_CMD.strip()
    if env_cmd:
        env_dir = _normalize_workdir(base_cwd, FULLSTACK_BACK_DIR) or str(Path(base_cwd).resolve())
        return LaunchSpec(command=env_cmd, workdir=env_dir, source="FULLSTACK_BACK_CMD")
    # .claude/server.json takes priority over runbook and auto-detection
    be = (server_config or {}).get("backend")
    if be and isinstance(be, dict):
        be_cmd = str(be.get("cmd", "")).strip()
        be_dir = str(be.get("dir", ".")).strip()
        repo_root = _get_repo_root(base_cwd)
        workdir = _normalize_workdir(str(repo_root), be_dir) or str(repo_root)
        if be_cmd:
            return LaunchSpec(command=be_cmd, workdir=workdir, source=SERVER_CONFIG_FILE)
    runbook_spec = _load_backend_runbook(base_cwd, backend_port)
    if runbook_spec:
        return runbook_spec
    repo_root = _get_repo_root(base_cwd)
    node_spec = _detect_node_backend_launch(repo_root)
    if node_spec:
        return node_spec

    py_root = _detect_python_backend_launch(repo_root, backend_port, "repo root")
    if py_root:
        return py_root
    for backend_dir in _candidate_backend_dirs(repo_root):
        py_dir = _detect_python_backend_launch(
            backend_dir,
            backend_port,
            f"backend dir '{backend_dir.name}'",
        )
        if py_dir:
            return py_dir

    return None


async def start_local_service(
    spec: LaunchSpec,
    port: int,
    timeout: float = 45.0,
):
    env = os.environ.copy()
    env["PORT"] = str(port)
    env.setdefault("HOST", "127.0.0.1")

    command = spec.command.replace("{port}", str(port))
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=spec.workdir,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    if not await wait_for_port(port, timeout=timeout):
        _kill_process(proc)
        return None, f"Command did not open port {port}: {spec.command}"
    return proc, None


async def stop_serve(chat_id: int) -> None:
    state = get_serve_state(chat_id)
    if state.get("tunnel_proc"):
        _kill_process(state["tunnel_proc"])
        state["tunnel_proc"] = None
    if state.get("app_proc"):
        _kill_process(state["app_proc"])
        state["app_proc"] = None
    _kill_processes(list(state.get("extra_procs") or []))
    state["extra_procs"] = []
    if state["task"] and not state["task"].done():
        state["task"].cancel()
    state["task"] = None
    state["url"] = None
    state["mode"] = None
    state["target"] = None
    state["cwd"] = None


async def watch_serve(chat_id: int, bot) -> None:
    state = get_serve_state(chat_id)
    proc = state["tunnel_proc"]
    url_sent = False
    try:
        while True:
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace")
            logger.info("test_serve[%s]: %s", chat_id, line.rstrip())
            if not url_sent:
                match = URL_RE.search(line)
                if match:
                    url = match.group(0)
                    state["url"] = url
                    url_sent = True
                    try:
                        await bot.send_message(
                            chat_id,
                            f"Tunnel URL: {url}\nUse /server stop to close.",
                        )
                    except Exception as e:
                        logger.warning("serve watcher send error: %s", e)

        state["tunnel_proc"] = None
        state["url"] = None
        if url_sent:
            try:
                await bot.send_message(chat_id, "Tunnel closed.")
            except Exception:
                pass
        else:
            try:
                await bot.send_message(
                    chat_id,
                    "Tunnel exited without URL. Check cloudflared installation.",
                )
            except Exception:
                pass
        if state.get("app_proc"):
            _kill_process(state["app_proc"])
            state["app_proc"] = None
        _kill_processes(list(state.get("extra_procs") or []))
        state["extra_procs"] = []
        state["mode"] = None
        state["target"] = None
        state["cwd"] = None
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("serve watcher[%s] error: %s", chat_id, e)


async def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            await asyncio.sleep(0.4)
    return False


def find_available_port(preferred: int, avoid: set[int] | None = None) -> int:
    avoid = avoid or set()
    candidates = [preferred] + list(range(preferred + 1, preferred + 25)) + [0]
    for candidate in candidates:
        if candidate in avoid:
            continue
        # Check 1: can we bind (avoids ports explicitly bound to 127.0.0.1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        finally:
            sock.close()
        # Check 2: nothing is actually accepting connections (Windows quirk:
        # binding to 127.0.0.1 may succeed even if 0.0.0.0 is occupied)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", candidate))
            # Connection succeeded → port is in use, skip it
            continue
        except (ConnectionRefusedError, OSError):
            pass
        finally:
            try:
                probe.close()
            except Exception:
                pass
        return candidate
    raise RuntimeError("No free local port available for proxy.")


async def start_tunnel(chat_id: int, target_url: str, bot, app_proc=None) -> None:
    cloudflared = shutil.which("cloudflared")
    if cloudflared is None:
        raise RuntimeError("cloudflared not found in PATH.")

    tunnel_proc = await asyncio.create_subprocess_exec(
        cloudflared,
        "tunnel",
        "--url",
        target_url,
        "--no-autoupdate",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    state = get_serve_state(chat_id)
    state["tunnel_proc"] = tunnel_proc
    state["app_proc"] = app_proc
    state["target"] = target_url
    state["task"] = asyncio.get_running_loop().create_task(watch_serve(chat_id, bot))


def _build_server_setup_prompt(cwd: str) -> str:
    return (
        f"Analyze the project in {cwd} and create the file {SERVER_CONFIG_FILE} "
        "at the repo root so the Telegram bot can deploy it automatically.\n\n"
        "The file must follow this exact JSON format "
        "(omit a key entirely if that side does not exist in this project):\n\n"
        "{\n"
        '  "frontend": {\n'
        '    "dir": "<relative path from repo root, e.g. frontend or .>",\n'
        '    "cmd": "<start command; use {port} if port is a positional arg, e.g. python -m http.server {port} --bind 127.0.0.1>",\n'
        '    "port": <preferred port, e.g. 3000>\n'
        "  },\n"
        '  "backend": {\n'
        '    "dir": "<relative path from repo root, e.g. backend or .>",\n'
        '    "cmd": "<start command>",\n'
        '    "port": <preferred port, e.g. 5000>,\n'
        '    "api_prefix": "<API URL prefix, e.g. /api>"\n'
        "  }\n"
        "}\n\n"
        "Rules to follow:\n"
        "1. Static HTML frontend (no package.json with dev/start script): "
        'set cmd to "python -m http.server {port} --bind 127.0.0.1".\n'
        "2. Node.js frontend with dev server: "
        'set cmd to "npm run dev" (the bot sets PORT env var automatically).\n'
        "3. Make the backend read its port from PORT environment variable with a fallback "
        "(e.g. process.env.PORT || 5000 or os.environ.get('PORT', '5000')). "
        "Apply this change to the backend source code if not already present.\n"
        "4. Use ports that do not conflict (default: frontend 3000, backend 5000).\n"
        f"5. Create the .claude/ directory if it does not exist, then write {SERVER_CONFIG_FILE}.\n\n"
        "After creating the file, confirm with the exact deploy command:\n"
        "/server fullstack"
    )


async def cmd_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    args = context.args or []

    if args and args[0].lower() == "status":
        if not is_serve_active(chat_id):
            await update.effective_message.reply_text("No server is running.")
            return
        state = get_serve_state(chat_id)
        url = state.get("url")
        mode = state.get("mode")
        target = state.get("target")
        cwd = state.get("cwd")
        if url:
            await update.effective_message.reply_text(
                f"Server mode: {mode}\n"
                f"Target: {target}\n"
                f"CWD: {cwd}\n"
                f"Public URL: {url}"
            )
        else:
            await update.effective_message.reply_text(
                f"Server mode: {mode}\n"
                f"Target: {target}\n"
                f"CWD: {cwd}\n"
                "URL not available yet."
            )
        return

    if args and args[0].lower() == "stop":
        if not is_serve_active(chat_id):
            await update.effective_message.reply_text("No server is running.")
            return
        await stop_serve(chat_id)
        await update.effective_message.reply_text("Server and tunnel stopped.")
        return

    if is_serve_active(chat_id):
        state = get_serve_state(chat_id)
        url = state.get("url")
        if url:
            await update.effective_message.reply_text(f"Already serving: {url}\nUse /server stop.")
        else:
            await update.effective_message.reply_text("Server is starting, URL not yet available.")
        return

    cwd = str(Path(get_state(chat_id)["cwd"]).resolve())
    get_state(chat_id)["cwd"] = cwd
    state = get_serve_state(chat_id)
    state["cwd"] = cwd
    state["extra_procs"] = []

    if not args:
        port = SERVE_PORT
        await update.effective_message.reply_text(
            f"Starting static server in {cwd}\n"
            f"Local URL: http://127.0.0.1:{port}\n"
            "Public Cloudflare URL will appear in a moment."
        )
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
        )
        await asyncio.sleep(0.7)
        if app_proc.returncode is not None:
            await update.effective_message.reply_text(
                f"Failed to start static server on port {port}."
            )
            return
        state["mode"] = "static"
        try:
            await start_tunnel(
                chat_id, f"http://127.0.0.1:{port}", context.bot, app_proc=app_proc
            )
        except Exception as e:
            try:
                app_proc.kill()
            except Exception:
                pass
            await update.effective_message.reply_text(f"Failed to start tunnel: {e}")
        return

    if args[0].lower() == "proxy":
        if len(args) != 2:
            await update.effective_message.reply_text("Usage: /server proxy <port>")
            return
        try:
            port = int(args[1])
        except ValueError:
            await update.effective_message.reply_text("Port must be an integer.")
            return
        await update.effective_message.reply_text(
            f"Publishing existing local service on port {port}..."
        )
        if not await wait_for_port(port, timeout=8):
            await update.effective_message.reply_text(
                f"No local service detected on 127.0.0.1:{port}."
            )
            return
        state["mode"] = "proxy"
        state["extra_procs"] = []
        try:
            await start_tunnel(
                chat_id, f"http://127.0.0.1:{port}", context.bot, app_proc=None
            )
        except Exception as e:
            await update.effective_message.reply_text(f"Failed to start tunnel: {e}")
        return

    if args[0].lower() == "run":
        if len(args) < 3:
            await update.effective_message.reply_text("Usage: /server run <port> <command>")
            return
        try:
            port = int(args[1])
        except ValueError:
            await update.effective_message.reply_text("Port must be an integer.")
            return
        command = " ".join(args[2:])
        await update.effective_message.reply_text(
            f"Starting app command in {cwd}\n"
            f"Command: {command}\n"
            f"Expected port: {port}"
        )
        app_proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
        )
        if not await wait_for_port(port, timeout=20):
            try:
                app_proc.kill()
            except Exception:
                pass
            await update.effective_message.reply_text(
                f"App did not open port {port}. Check your command."
            )
            return
        state["mode"] = "run"
        state["extra_procs"] = []
        try:
            await start_tunnel(
                chat_id, f"http://127.0.0.1:{port}", context.bot, app_proc=app_proc
            )
        except Exception as e:
            try:
                app_proc.kill()
            except Exception:
                pass
            await update.effective_message.reply_text(f"Failed to start tunnel: {e}")
        return

    if args[0].lower() == "fullstack":
        server_config = _load_server_config(cwd)
        if len(args) == 1:
            # no-arg mode: require .claude/server.json
            if not server_config:
                await update.effective_message.reply_text(
                    "No .claude/server.json found.\n"
                    "Run /server claude set to create it automatically, or provide ports:\n"
                    "/server fullstack <front_port> <back_port> [api_prefix]"
                )
                return
            fe_cfg = server_config.get("frontend") or {}
            be_cfg = server_config.get("backend") or {}
            front_port = int(fe_cfg.get("port", 3000))
            backend_port = int(be_cfg.get("port", 5000))
            api_prefix = str(be_cfg.get("api_prefix", "/api"))
        elif 3 <= len(args) <= 4:
            try:
                front_port = int(args[1])
                backend_port = int(args[2])
            except ValueError:
                await update.effective_message.reply_text("front_port and backend_port must be integers.")
                return
            default_prefix = str((server_config or {}).get("backend", {}).get("api_prefix", "/api"))
            api_prefix = args[3] if len(args) == 4 else default_prefix
        else:
            await update.effective_message.reply_text(
                "Usage: /server fullstack\n"
                "       /server fullstack <front_port> <back_port> [api_prefix]"
            )
            return
        if not api_prefix.startswith("/"):
            api_prefix = "/" + api_prefix
        if api_prefix != "/" and api_prefix.endswith("/"):
            api_prefix = api_prefix.rstrip("/")

        await update.effective_message.reply_text(
            f"Preparing fullstack publish\n"
            f"Frontend: 127.0.0.1:{front_port}\n"
            f"Backend: 127.0.0.1:{backend_port}\n"
            f"API prefix: {api_prefix}\n"
            "If ports are down, I will try to start frontend/backend automatically."
        )

        started_procs: list = []
        effective_front_port = front_port
        effective_backend_port = backend_port

        front_ready = await wait_for_port(front_port, timeout=2.5)
        front_probe = await probe_http(front_port, "/") if front_ready else HttpProbe(False, 0, "", "")
        front_conflict = front_ready and not _looks_like_frontend_http(front_probe)

        if front_conflict:
            candidate_front_port = find_available_port(front_port, avoid={backend_port})
            await update.effective_message.reply_text(
                f"Frontend port {front_port} is already in use but does not look like a web frontend.\n"
                f"Observed: {_probe_summary(front_probe)}\n"
                f"I will start frontend on fallback port {candidate_front_port}."
            )
            effective_front_port = candidate_front_port
            front_ready = False

        if not front_ready:
            front_spec = detect_frontend_launch(cwd, frontend_port=effective_front_port, server_config=server_config)
            if not front_spec:
                await update.effective_message.reply_text(
                    f"Frontend not detected on 127.0.0.1:{effective_front_port} and no startup command "
                    "could be auto-detected.\n"
                    "Set FULLSTACK_FRONT_CMD in .env, or start frontend manually and retry."
                )
                return
            await update.effective_message.reply_text(
                f"Starting frontend automatically\n"
                f"Source: {front_spec.source}\n"
                f"Dir: {front_spec.workdir}\n"
                f"Command: {front_spec.command}"
            )
            front_proc, err = await start_local_service(front_spec, effective_front_port, timeout=50)
            if err or front_proc is None:
                _kill_processes(started_procs)
                await update.effective_message.reply_text(
                    f"Could not start frontend on port {effective_front_port}.\n"
                    f"{err or 'Unknown error.'}"
                )
                return
            started_procs.append(front_proc)

        back_ready = await wait_for_port(backend_port, timeout=2.5)
        if not back_ready:
            back_spec = detect_backend_launch(cwd, backend_port, server_config=server_config)
            if not back_spec:
                _kill_processes(started_procs)
                await update.effective_message.reply_text(
                    f"Backend not detected on 127.0.0.1:{backend_port} and no startup command "
                    "could be auto-detected.\n"
                    "Detection checked: FULLSTACK_BACK_CMD, backend runbook, repo root scripts, "
                    "and backend-like folders (backend/api/server).\n"
                    "Set FULLSTACK_BACK_CMD in .env, or start backend manually and retry."
                )
                return
            await update.effective_message.reply_text(
                f"Starting backend automatically\n"
                f"Source: {back_spec.source}\n"
                f"Dir: {back_spec.workdir}\n"
                f"Command: {back_spec.command}"
            )
            back_proc, err = await start_local_service(back_spec, effective_backend_port, timeout=60)
            if err or back_proc is None:
                _kill_processes(started_procs)
                await update.effective_message.reply_text(
                    f"Could not start backend on port {effective_backend_port}.\n"
                    f"{err or 'Unknown error.'}"
                )
                return
            started_procs.append(back_proc)

        if not await wait_for_port(effective_front_port, timeout=5):
            _kill_processes(started_procs)
            await update.effective_message.reply_text(
                f"Frontend still not reachable on 127.0.0.1:{effective_front_port}."
            )
            return
        if not await wait_for_port(effective_backend_port, timeout=5):
            _kill_processes(started_procs)
            await update.effective_message.reply_text(
                f"Backend still not reachable on 127.0.0.1:{effective_backend_port}."
            )
            return

        final_front_probe = await probe_http(effective_front_port, "/")
        if not _looks_like_frontend_http(final_front_probe):
            _kill_processes(started_procs)
            await update.effective_message.reply_text(
                f"Frontend on 127.0.0.1:{effective_front_port} is reachable but is not serving HTML.\n"
                f"Observed: {_probe_summary(final_front_probe)}\n"
                "Fix: run your frontend dev server on that port (or set FULLSTACK_FRONT_CMD/FULLSTACK_FRONT_DIR) "
                "and retry /server fullstack."
            )
            return

        if not Path(FULLSTACK_PROXY).is_file():
            _kill_processes(started_procs)
            await update.effective_message.reply_text("Missing fullstack_proxy.py.")
            return

        proxy_port = find_available_port(SERVE_PORT, avoid={effective_front_port, effective_backend_port})
        app_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            FULLSTACK_PROXY,
            str(proxy_port),
            str(effective_front_port),
            str(effective_backend_port),
            api_prefix,
            cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
        )
        if not await wait_for_port(proxy_port, timeout=10):
            _kill_process(app_proc)
            _kill_processes(started_procs)
            await update.effective_message.reply_text(
                f"Fullstack proxy failed to start on port {proxy_port}."
            )
            return

        state["mode"] = f"fullstack({api_prefix})"
        state["extra_procs"] = started_procs
        await update.effective_message.reply_text(
            f"Fullstack routing ready.\n"
            f"Frontend source port: {effective_front_port}\n"
            f"Backend source port: {effective_backend_port}\n"
            f"API prefix: {api_prefix}"
        )
        try:
            await start_tunnel(
                chat_id,
                f"http://127.0.0.1:{proxy_port}",
                context.bot,
                app_proc=app_proc,
            )
        except Exception as e:
            _kill_process(app_proc)
            _kill_processes(started_procs)
            state["extra_procs"] = []
            await update.effective_message.reply_text(f"Failed to start tunnel: {e}")
        return

    if args[0].lower() == "claude" and len(args) >= 2 and args[1].lower() == "set":
        state = get_state(chat_id)
        if state.get("session_active"):
            await update.effective_message.reply_text("Claude is already running. Use /stop first.")
            return
        prompt = _build_server_setup_prompt(cwd)
        await update.effective_message.reply_text(
            f"Sending deployment setup prompt to Claude in {cwd}..."
        )
        await run_task(chat_id, prompt, context, update)
        return

    if args[0].lower() == "help":
        server_config = _load_server_config(cwd)
        config_status = (
            f".claude/server.json found — config is active."
            if server_config
            else "No .claude/server.json yet — run /server claude set to create it."
        )
        fmt = (
            "The bot reads .claude/server.json at the repo root to deploy.\n"
            f"{config_status}\n\n"
            "Expected file format:\n"
            "{\n"
            '  "frontend": {\n'
            '    "dir": "frontend",\n'
            '    "cmd": "npm run dev",\n'
            '    "port": 3000\n'
            "  },\n"
            '  "backend": {\n'
            '    "dir": "backend",\n'
            '    "cmd": "npm run dev",\n'
            '    "port": 5000,\n'
            '    "api_prefix": "/api"\n'
            "  }\n"
            "}\n\n"
            "Rules:\n"
            "- Use {port} in cmd if it takes port as argument\n"
            "  (e.g. python -m http.server {port} --bind 127.0.0.1)\n"
            "  Otherwise PORT env var is set automatically.\n"
            "- Omit frontend or backend key if not present.\n\n"
            "Commands:\n"
            "/server claude set  — Claude creates .claude/server.json for this project\n"
            "/server fullstack   — deploy using .claude/server.json\n"
            "/server fullstack <front_port> <back_port> [api_prefix]  — override ports"
        )
        await update.effective_message.reply_text(fmt)
        return

    await update.effective_message.reply_text(
        "Usage:\n"
        "/server\n"
        "/server proxy <port>\n"
        "/server run <port> <command>\n"
        "/server fullstack\n"
        "/server fullstack <front_port> <backend_port> [api_prefix]\n"
        "/server claude set\n"
        "/server help\n"
        "/server status\n"
        "/server stop"
    )
