# Claude Code Companion — local-mode

A Telegram interface for **Claude Code developers**.

Instead of a general shell, this bot bridges Telegram directly to Claude Code's interactive mode running on your machine. Claude Code already handles running tests, starting servers, committing, pushing, and opening PRs — you just ask it in plain language.

```
You (Telegram) ──► Claude Code (interactive, on your machine) ──► your project
Claude Code output / questions ──► Telegram
Your replies ──► Claude Code stdin
```

---

## Quick start

```bash
# 1. Clone and enter the directory
git clone https://github.com/YOUR_USER/claude_code_bot
cd claude_code_bot

# 2. Run setup (prompts for your 2 required values, installs deps)
bash setup.sh

# 3. Authenticate Claude Code (one-time)
claude auth login

# 4. Start the bot
python bot.py
```

Open Telegram, find your bot, send `/start`.

---

## Requirements

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- A Telegram bot token — create one with [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID — get it from [@userinfobot](https://t.me/userinfobot)

No Anthropic API key needed (Claude Code manages its own auth).
No GitHub PAT needed (your local git credentials are used).

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Yes | — | Your numeric Telegram ID |
| `INITIAL_DIR` | No | `~` | Default working directory on start |
| `RESTRICT_PATHS` | No | `false` | Set `true` to add `--allowedPaths` (restricts Claude to the project dir) |
| `BLOCKED_PATTERNS` | No | — | Comma-separated extra patterns to block before sending to Claude Code |

Copy `.env.example` to `.env` and fill in your values, or just run `./setup.sh`.

---

## Bot commands

| Command | Description |
|---|---|
| `/start` | Welcome message + quick reference |
| `/cd <path>` | Change working directory (full path or saved project name) |
| `/projects` | List saved projects as tappable buttons |
| `/save <name>` | Save current directory as a named project |
| `/status` | Show current directory and whether a session is active |
| `/stop` | Send Ctrl+C to Claude Code (interrupt current task) |
| `/reset` | Kill active session, clear state, stay in same directory |

---

## How sessions work

Send a task in plain language → Claude Code starts in your current directory.

```
You:        add a health check endpoint
Bot:        Starting Claude Code in /home/user/myapp...
Bot:        (Claude Code output streams here)
Bot:        Should I create a new file? 1. Yes  2. No
You:        1
Bot:        (Claude Code continues...)
Bot:        ✅ Session ended.
```

While a session is active, **all your messages are forwarded to Claude Code's stdin** — so you can answer its questions, pick numbered options, or give follow-up instructions.

Use `/stop` to send Ctrl+C (interrupt), or `/reset` to kill the process entirely.

---

## Project navigation

Save your projects once, switch between them with a tap:

```
/cd ~/projects/myapp        → switch by path
/save myapp                 → save it as "myapp"
/projects                   → tap to switch
/cd myapp                   → switch by name
```

Projects are stored in `~/.claude_code_bot/projects.json`.

---

## What Claude Code can do for you

Just ask in plain language — no special commands needed:

- `add a REST endpoint for user login`
- `run the tests and fix any failures`
- `commit and push this branch`
- `create a PR from this branch to main`
- `start the dev server and tell me the port`
- `refactor the auth module to use JWT`

Claude Code handles the git, the files, the shell commands — you just describe the goal.

---

## Security model

| Layer | What it protects against |
|---|---|
| `TELEGRAM_USER_ID` check | Anyone else using the bot (primary gate) |
| `RESTRICT_PATHS=true` (opt-in) | Claude Code writing outside the project directory |
| Blocklist pre-check | Obvious catastrophic prompts (e.g. `rm -rf ~`, fork bombs) |
| Non-root execution | System-level damage |
| Audit log (`~/.claude_code_bot/audit.log`) | Accountability — every prompt logged |

**Blocklist**: before sending a prompt to Claude Code, the bot checks for patterns like `rm -rf /`, `rm -rf ~`, `sudo rm`, `dd if=`, `mkfs`, fork bombs, and `curl | bash`. If matched, it warns and asks for explicit `YES` confirmation.

**Trade-off with `RESTRICT_PATHS=false` (default)**: Claude Code can touch any file your user account can — the same as running it in your terminal. The `TELEGRAM_USER_ID` check ensures only you control it.

---

## Architecture

```
bot.py
  │
  ├─ /cd /projects /save       →  working directory navigation
  │
  ├─ User sends task
  │     ├─ Security pre-check (blocklist)
  │     └─ Spawn: claude --dangerously-skip-permissions [--allowedPaths cwd]
  │               cwd = current project directory
  │
  ├─ PTY bridge (async):
  │     read output ──strip ANSI──buffer──► Telegram messages
  │     Telegram reply ──────────────────► Claude Code stdin
  │
  ├─ /stop    →  Ctrl+C to Claude Code
  ├─ /reset   →  kill session, start fresh
  └─ /status  →  show cwd + session state
```

On Linux/macOS: Claude Code runs in a proper PTY (full terminal UI).
On Windows: falls back to subprocess pipes (functional, no TUI).

---

## Output handling

| Situation | Behavior |
|---|---|
| Output ≤ 3500 chars | Sent as-is in a code block |
| Output > 3500 chars | First 3500 chars + `(output truncated — ask Claude to summarize)` |
| No output for 30s | `⏳ Still working...` |
| Session ended | `✅ Session ended.` + prompt for next task |

---

## Files

| File | Purpose |
|---|---|
| `bot.py` | Main bot — PTY bridge, commands, session state |
| `requirements.txt` | `python-telegram-bot`, `python-dotenv` |
| `.env.example` | Template for environment variables |
| `setup.sh` | Interactive setup script |
| `.github/workflows/agente.yml` | Unchanged — stays on `main` branch |
