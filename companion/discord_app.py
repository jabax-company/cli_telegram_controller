"""Discord bot application bootstrap.

Starts a discord.py bot that exposes all the same commands as the Telegram
bot.  Telegram and Discord can run simultaneously from the same process.

Only one Discord user (DISCORD_USER_ID) is authorised to interact with the
bot.  All commands are registered as Discord application (slash) commands.

Entry point: run_discord_bot(loop)  – called from companion/app.py alongside
the Telegram polling loop.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_discord_bot() -> None:
    """Start the Discord bot.  Exits silently if discord.py is not installed
    or if DISCORD_TOKEN / DISCORD_USER_ID are not configured."""
    from companion.core.config import DISCORD_TOKEN, DISCORD_USER_ID

    if not DISCORD_TOKEN:
        logger.info("DISCORD_TOKEN not set – Discord bot disabled.")
        return
    if not DISCORD_USER_ID:
        logger.warning("DISCORD_USER_ID not set – Discord bot disabled.")
        return

    try:
        import discord
        from discord import app_commands
    except ImportError:
        logger.warning(
            "discord.py not installed – Discord bot disabled. "
            "Run 'uv sync' to install it."
        )
        return

    from companion.handlers.discord_commands import (
        cmd_bash,
        cmd_branch,
        cmd_cd,
        cmd_claude,
        cmd_codex,
        cmd_engine,
        cmd_exit,
        cmd_help,
        cmd_projects,
        cmd_reset,
        cmd_save,
        cmd_start,
        cmd_status,
        cmd_stop,
    )
    from companion.handlers.discord_messages import handle_message

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    # ── Slash commands ─────────────────────────────────────────────────────

    @tree.command(name="start", description="Inicializar el bot")
    async def slash_start(interaction: discord.Interaction) -> None:
        await cmd_start(interaction)

    @tree.command(name="help", description="Mostrar referencia de comandos")
    async def slash_help(interaction: discord.Interaction) -> None:
        await cmd_help(interaction)

    @tree.command(name="status", description="Estado de la sesión actual")
    async def slash_status(interaction: discord.Interaction) -> None:
        await cmd_status(interaction)

    @tree.command(name="cd", description="Cambiar directorio de trabajo")
    @app_commands.describe(path="Ruta destino (vacío = dir base)")
    async def slash_cd(interaction: discord.Interaction, path: str = "") -> None:
        await cmd_cd(interaction, path)

    @tree.command(name="claude", description="Ejecutar prompt con Claude Code")
    @app_commands.describe(prompt="Prompt a enviar a Claude Code (vacío = activar modo)")
    async def slash_claude(interaction: discord.Interaction, prompt: str = "") -> None:
        await cmd_claude(interaction, prompt)

    @tree.command(name="codex", description="Ejecutar prompt con OpenAI Codex")
    @app_commands.describe(prompt="Prompt a enviar a Codex (vacío = activar modo)")
    async def slash_codex(interaction: discord.Interaction, prompt: str = "") -> None:
        await cmd_codex(interaction, prompt)

    @tree.command(name="exit", description="Salir del modo Claude/Codex")
    async def slash_exit(interaction: discord.Interaction) -> None:
        await cmd_exit(interaction)

    @tree.command(name="bash", description="Ejecutar comando shell en el directorio actual")
    @app_commands.describe(command="Comando a ejecutar")
    async def slash_bash(interaction: discord.Interaction, command: str) -> None:
        await cmd_bash(interaction, command)

    @tree.command(name="stop", description="Interrumpir ejecución actual")
    async def slash_stop(interaction: discord.Interaction) -> None:
        await cmd_stop(interaction)

    @tree.command(name="reset", description="Resetear sesión y contexto")
    async def slash_reset(interaction: discord.Interaction) -> None:
        await cmd_reset(interaction)

    @tree.command(name="branch", description="Crear/cambiar rama Git y activar bloqueo")
    @app_commands.describe(branch_name="Nombre de rama ('off' para quitar bloqueo)")
    async def slash_branch(interaction: discord.Interaction, branch_name: str = "") -> None:
        await cmd_branch(interaction, branch_name)

    @tree.command(name="save", description="Guardar directorio actual como proyecto")
    @app_commands.describe(name="Nombre del proyecto")
    async def slash_save(interaction: discord.Interaction, name: str) -> None:
        await cmd_save(interaction, name)

    @tree.command(name="projects", description="Listar proyectos guardados")
    async def slash_projects(interaction: discord.Interaction) -> None:
        await cmd_projects(interaction)

    @tree.command(name="engine", description="Cambiar motor de IA")
    @app_commands.describe(engine="Motor: claude / codex / claude-channel")
    @app_commands.choices(engine=[
        app_commands.Choice(name="Claude Code (subprocess)", value="claude"),
        app_commands.Choice(name="Claude Code (Remote Control)", value="claude-channel"),
        app_commands.Choice(name="OpenAI Codex", value="codex"),
    ])
    async def slash_engine(interaction: discord.Interaction, engine: str) -> None:
        await cmd_engine(interaction, engine)

    # ── Event handlers ─────────────────────────────────────────────────────

    @client.event
    async def on_ready() -> None:
        await tree.sync()
        logger.info(
            "Discord bot connected as %s (authorised user ID: %s).",
            client.user,
            DISCORD_USER_ID,
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        # Ignore bot messages and messages from other users
        if message.author.bot:
            return
        if message.author.id != DISCORD_USER_ID:
            return
        # Ignore messages that look like slash commands (handled by tree)
        if (message.content or "").startswith("/"):
            return
        await handle_message(message)

    # ── Start ──────────────────────────────────────────────────────────────

    try:
        await client.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error("Discord login failed – check DISCORD_TOKEN.")
    except Exception as exc:
        logger.error("Discord bot error: %s", exc)
    finally:
        if not client.is_closed():
            await client.close()
