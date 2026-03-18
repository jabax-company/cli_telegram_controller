# AI Code Companion — Bot de Telegram para Claude Code y Codex

Bot de Telegram que controla **Claude Code** (Anthropic) o **Codex CLI** (OpenAI) desde tu móvil. Corre completamente en local: el bot se ejecuta en tu ordenador y tú lo manejas desde Telegram en cualquier lugar.

---

## Características

- Ejecuta **Claude Code** o **Codex CLI** desde Telegram con texto, audio o imágenes
- **Transcripción de audio local** con faster-whisper (sin APIs externas)
- **Explorador de carpetas interactivo** con botones inline
- **Proyectos guardados** para cambiar entre repos con un toque
- **Tareas programadas** — programa prompts a una hora concreta; acumula mensajes y audios
- **Ejecución multi-repo concurrente** — cada proyecto corre en su propio proceso
- **Publicar webs locales** vía Cloudflare Tunnel (estático, proxy, fullstack)
- **Bloqueo de rama Git** — Claude trabaja siempre en la rama correcta
- **Modo seguro** — bloquea comandos destructivos (rm -rf, git reset --hard, etc.)
- **Icono en bandeja** (Windows) con botón de parada

---

## Requisitos

| Requisito | Instalación |
|-----------|-------------|
| Python 3.13 | `uv python install 3.13` |
| uv | [https://docs.astral.sh/uv](https://docs.astral.sh/uv) |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` |
| — o — Codex CLI | `npm install -g @openai/codex` |
| Bot de Telegram | Crea uno con [@BotFather](https://t.me/BotFather) |
| cloudflared | Solo para `/server` — [descarga](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) |

---

## Instalación

### 1. Clonar y entrar al repo

```bash
git clone https://github.com/TU_USUARIO/claude_code_bot
cd claude_code_bot
```

### 2. Instalar dependencias

```powershell
uv python install 3.13
uv python pin 3.13
uv sync
```

### 3. Crear el bot en Telegram

1. Abre [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copia el **token** (formato `123456789:ABC-DEF...`)
3. Abre [@userinfobot](https://t.me/userinfobot) para obtener tu **User ID** numérico

### 4. Configurar `.env`

```powershell
Copy-Item .env.example .env
```

Edita `.env` y pon como mínimo:

```env
TELEGRAM_TOKEN=tu_token_de_botfather
TELEGRAM_USER_ID=tu_user_id_numerico
```

Para usar **Codex** en vez de Claude (opcional):

```env
AI_ENGINE=codex
CODEX_MODEL=o4-mini
```

### 5. Autenticar el CLI

```powershell
# Claude Code:
claude auth login

# Codex:
codex   # sigue las instrucciones en pantalla
```

### 6. Arrancar el bot

```powershell
uv run python bot.py
```

Verás: `Claude Code Companion (local-mode) started. Authorized user: ...`

---

## Comandos

### Navegación

| Comando | Descripción |
|---------|-------------|
| `/cd` | Ir al directorio base |
| `/cd <ruta>` | Cambiar directorio (absoluta, relativa o nombre de proyecto) |
| `/base` | Ver directorio base |
| `/base <ruta>` | Cambiar directorio base |
| `/base reset` | Restaurar directorio base inicial |
| `/paths` | Explorador de carpetas con botones |
| `/projects` | Listar proyectos guardados |
| `/save <nombre>` | Guardar directorio actual como proyecto |

### Ejecución con IA

| Comando | Descripción |
|---------|-------------|
| `/claude` | Modo asistido (preguntas de optimización del prompt) |
| `/claude <prompt>` | Ejecutar prompt directamente |
| `/bash <cmd>` | Ejecutar comando shell en el directorio actual |
| `/branch <nombre>` | Crear/cambiar rama Git y bloquear cambios a esa rama |
| `/branch off` | Quitar bloqueo de rama |
| `/exit` | Salir del modo Claude |
| `/stop` | Interrumpir ejecución activa |
| `/reset` | Matar proceso y borrar contexto de sesión |

### Tareas programadas

| Comando | Descripción |
|---------|-------------|
| `/at HH:MM <prompt>` | Programar tarea a una hora |
| `/at HH:MM` | Modo acumulación — envía mensajes y audios; `/at done` para guardar |
| `/at HH:MM DD/MM <prompt>` | Programar en fecha específica |
| `/at HH:MM /bash <cmd>` | Programar un comando bash |
| `/at done` | Guardar el borrador acumulado como tarea |
| `/scheduled` | Ver tareas pendientes con sus IDs |
| `/unschedule <id>` | Cancelar una tarea programada |

**Flujo multi-repo** (cada proyecto en su propio proceso):

```
/cd ~/proyecto_A  →  /at 14:00  →  [envías mensajes/audio]  →  /at done
/cd ~/proyecto_B  →  /at 15:30  →  [envías mensajes/audio]  →  /at done
```

### Publicar web (Cloudflare Tunnel)

| Comando | Descripción |
|---------|-------------|
| `/server` | Publicar directorio actual como web estática |
| `/server proxy <puerto>` | Túnel a app ya corriendo en local |
| `/server run <puerto> <cmd>` | Arrancar app y publicarla |
| `/server fullstack` | Auto-detectar y publicar frontend + backend |
| `/server fullstack <fp> <bp>` | Con puertos explícitos |
| `/server status` | Ver estado del túnel |
| `/server stop` | Parar túnel y procesos |

### Control del bot

| Comando | Descripción |
|---------|-------------|
| `/status` | Estado completo: sesión, directorio, modo activo |
| `/status bot` | Forzar estado del bot (aunque estés en modo Claude) |
| `/bot stop` | Apagar el bot remotamente |
| `/help` | Referencia completa de comandos |
| `/start` | Bienvenida con botones de acceso rápido |

---

## Entrada multimedia

### Audio
Envía una nota de voz o archivo de audio:
1. Se transcribe localmente con faster-whisper
2. El texto queda como prompt pendiente
3. Envía `/claude` para ejecutarlo

En **modo acumulación** (`/at HH:MM`), los audios se añaden al borrador de la tarea programada.

### Imágenes
- Se guardan en `images/` en la raíz del bot
- Sin caption: se encolan para el siguiente prompt
- Con caption (en modo Claude): ejecutan inmediatamente con la imagen como contexto
- Usa `<image>` en el prompt para referenciar la última imagen

---

## Variables de entorno

### Obligatorias

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_TOKEN` | Token del bot de @BotFather |
| `TELEGRAM_USER_ID` | Tu ID numérico de Telegram |

### Motor de IA

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `AI_ENGINE` | `claude` | `claude` o `codex` |
| `CODEX_MODEL` | `""` | Modelo de Codex: `o4-mini`, `o3`, etc. |

### Audio

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `WHISPER_MODEL` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | Tipo de cómputo |
| `WHISPER_PRIMARY_LANGUAGE` | `es` | Idioma principal |
| `WHISPER_ALLOW_AUTO_FALLBACK` | `true` | Detección automática de idioma |

### Comportamiento

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `INITIAL_DIR` | raíz del bot | Directorio de trabajo inicial |
| `SAFE_MODE` | `true` | Bloquear comandos destructivos |
| `RESTRICT_PATHS` | `false` | Restringir IA al directorio actual |
| `INACTIVITY_TIMEOUT_SECS` | `1800` | Segundos de inactividad antes de parar sesión |
| `TRAY_ICON_ENABLED` | `true` | Icono en bandeja de Windows |

### Servidor

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `FULLSTACK_FRONT_CMD` | `""` | Comando para arrancar el frontend |
| `FULLSTACK_FRONT_DIR` | `""` | Carpeta del frontend |
| `FULLSTACK_BACK_CMD` | `""` | Comando para arrancar el backend |
| `FULLSTACK_BACK_DIR` | `""` | Carpeta del backend |
| `BLOCKED_PATTERNS` | `""` | Patrones extra a bloquear (separados por coma) |

---

## Solución de problemas

| Síntoma | Solución |
|---------|----------|
| El bot no arranca | Comprueba `TELEGRAM_TOKEN` y `TELEGRAM_USER_ID` en `.env` |
| `claude` no encontrado | `claude --version`; asegúrate de que está en el PATH |
| `codex` no encontrado | `codex --version`; `npm install -g @openai/codex` |
| Audio falla | `uv sync` para instalar faster-whisper; `WHISPER_DEVICE=cpu` si no hay GPU |
| `/server` no funciona | Instala `cloudflared` y añádelo al PATH |

---

## Seguridad

- Solo el usuario con `TELEGRAM_USER_ID` puede usar el bot
- `SAFE_MODE=true` bloquea: `rm`, `del`, `Remove-Item`, `git reset --hard`, `git clean`, `dd if=`, etc.
- Las operaciones bloqueadas piden confirmación ("YES") antes de ejecutar
- Añade tus propios patrones con `BLOCKED_PATTERNS=patron1,patron2`
