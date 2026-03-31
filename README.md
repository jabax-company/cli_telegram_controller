# Claude Code Companion (local mode)

Bot de **Telegram** y **Discord** para controlar Claude Code y Codex en tu maquina local, con soporte de **Remote Control** via canal MCP webhook.

## Tutorial desde cero

### 1) Requisitos

- Windows + PowerShell (este README usa esos comandos)
- Python 3.13
- `uv` instalado
- Claude Code CLI instalado y autenticado
- `cloudflared` instalado (para publicar URL en internet)
- Bot de Telegram creado con @BotFather
- _(opcional)_ Bot de Discord para activar control desde Discord

### 2) Clonar y entrar al repo

```powershell
git clone https://github.com/Jairoj88/claude_code_bot
cd claude_code_bot
```

### 3) Fijar Python y sincronizar dependencias

```powershell
uv python install 3.13
uv python pin 3.13
uv sync
```

### 4) Configurar variables de entorno

```powershell
Copy-Item .env.example .env
```

Edita `.env` y pon como minimo:

- `TELEGRAM_TOKEN`
- `TELEGRAM_USER_ID`

Discord (opcional, ver sección más abajo):

- `DISCORD_TOKEN`
- `DISCORD_USER_ID`

Para audio:

- `WHISPER_MODEL` (opcional, por defecto `small`)
- opcional: `WHISPER_DEVICE=auto`
- opcional: `WHISPER_COMPUTE_TYPE=int8`
- opcional: `WHISPER_PRIMARY_LANGUAGE=es` (por defecto espanol)
- opcional: `WHISPER_ALLOW_AUTO_FALLBACK=true` (permite audios en ingles)

### 5) Login de Claude Code

```powershell
# Claude Code:
claude auth login

# Codex:
codex   # sigue las instrucciones en pantalla
```

### 6) Ejecutar el bot

```powershell
uv run python bot.py
```

Si arranca bien, veras algo como:

`Claude Code Companion (local-mode) started. Authorized user: ...`

---

## Uso rapido en Telegram

### Flujo base

1. Envia `/start`
2. Selecciona carpeta:
   - `/paths` (navegador por botones)
   - o `/cd C:\ruta\proyecto`
3. Envia `/claude` para activar modo Claude
4. Envia tu prompt por texto, audio o imagen con caption
5. Si envias una imagen sin caption, queda en cola para tu siguiente prompt
6. Usa `/exit` para salir de modo Claude

### Imagenes de referencia

- Las imagenes se guardan en `images/` en la raiz del repo del bot.
- Si una imagen llega con caption y estas en modo Claude, se ejecuta al instante con referencia al archivo guardado.
- Si llega sin caption, el bot espera tu siguiente prompt y adjunta la imagen automaticamente.
- Puedes usar `<image>` o `<images>` en el prompt para referenciar imagen(es) guardada(s).

### Comandos importantes

- `/cd <ruta>` -> cambiar directorio actual
- `/3d <ruta>` -> alias de `/cd` (util para dictado por voz)
- `/branch <nombre>` -> crea/cambia rama y bloquea los cambios de `/claude` en esa rama
- `/claude` -> ejecuta el prompt pendiente
- `/claude <texto>` -> ejecuta texto directo
- `/bot stop` -> apaga este proceso del bot de forma remota
- `/status` -> estado de sesion, carpeta actual y prompt pendiente
- `/stop` -> interrumpe la ejecucion actual
- `/reset` -> limpia contexto/sesion

---

## Publicar web local en Cloudflare

El codigo sigue corriendo en tu maquina local.
Cloudflare solo expone una URL publica hacia tu localhost.

### Caso A: sitio estatico (HTML/CSS/JS)

```text
/server
```

Publica la carpeta actual como web estatica.

### Caso B: backend ya corriendo en local

Si tu app ya esta escuchando en un puerto local, por ejemplo `5000`:

```text
/server proxy 5000
```

### Caso C: arrancar backend desde el bot y publicarlo

Ejemplo Python:

```text
/server run 5000 python app.py
```

Ejemplo Node:

```text
/server run 3000 npm run dev
```

### Caso D: frontend + backend bajo una sola URL

Si tu frontend y backend corren en puertos distintos localmente:

```text
/server fullstack 3000 5000
```

Eso publica una sola URL donde:

- rutas ` /api/* ` -> backend (`5000`)
- resto de rutas -> frontend (`3000`)

Si tu API usa otro prefijo:

```text
/server fullstack 3000 5000 /backend
```

Recomendado para evitar CORS y que todo funcione desde el mismo dominio Cloudflare.

Si alguno de los dos puertos no esta levantado, el bot intenta arrancar frontend/backend automaticamente.
Por defecto, intenta frontend con `npm run dev` en la raiz del repo.
Para backend, primero intenta leer el archivo `.claude/backend_run.json` (si existe) y usar ese comando.
Solo despues usa deteccion en scripts/entrypoints de la raiz del repo.
Si no puede detectar el comando correcto, define comandos explicitos en `.env`:

```text
FULLSTACK_FRONT_CMD=npm run dev
FULLSTACK_FRONT_DIR=frontend
FULLSTACK_BACK_CMD=uv run python main.py
FULLSTACK_BACK_DIR=backend
```

Formato recomendado para `.claude/backend_run.json`:

```json
{
  "command": "uv run python main.py",
  "workdir": "backend",
  "port": 8000,
  "api_prefix": "/api"
}
```

### Estado y parada

```text
/server status
/server stop
```

> `/serve` tambien funciona como alias de `/server`.

---

## Cambiar base directory desde Telegram

Cada chat tiene su base path:

- ver base actual:
  - `/base`
- cambiar base:
  - `/base C:\Users\balta\proyectos`
- resetear base al valor inicial del bot:
  - `/base reset`

Al cambiar base, el bot tambien cambia `Current dir` a esa carpeta.
Luego `/cd`, `/3d` y `/paths` usan esa base.

---

## Audio a texto (local faster-whisper)

Si mandas una nota de voz o audio:

1. El bot la transcribe localmente con `faster-whisper`
   (primero intenta en espanol por defecto y puede hacer fallback automatico).
2. Guarda esa transcripcion como prompt pendiente
3. Ejecutas con `/claude`

Si falla, revisa que `faster-whisper` este instalado y tu configuracion `WHISPER_*` en `.env`.

---

## Control desde Discord

El mismo bot corre en paralelo con el de Telegram.
Los comandos son idénticos pero como slash-commands de Discord (`/claude`, `/codex`, `/cd`, etc.).

### Pasos para activarlo

#### 1. Crear el bot en Discord

1. Ve a [discord.com/developers/applications](https://discord.com/developers/applications) y crea una nueva aplicación.
2. En **Bot**, crea el bot y copia el **Token** (lo necesitarás en `.env` como `DISCORD_TOKEN`).
3. En **Bot → Privileged Gateway Intents**, activa:
   - **Message Content Intent** ✅
   - **Server Members Intent** ✅ (recomendado)
4. En **OAuth2 → URL Generator**, selecciona scopes:
   - `bot`
   - `applications.commands`
5. En permisos de bot, marca:
   - `Send Messages`
   - `Read Message History`
   - `Attach Files`
   - `Use Slash Commands`
6. Copia la URL generada y úsala para invitar el bot a tu servidor.

#### 2. Obtener tu ID de usuario de Discord

1. Activa el **Modo Desarrollador** en Discord:
   _Ajustes de usuario → Avanzado → Modo de desarrollador_.
2. Haz clic derecho en tu nombre de usuario → **Copiar ID**.
3. Ponlo en `.env` como `DISCORD_USER_ID=TU_ID`.

#### 3. Configurar `.env`

```env
DISCORD_TOKEN=tu_token_aqui
DISCORD_USER_ID=123456789012345678
```

#### 4. Instalar dependencias y ejecutar

```powershell
uv sync
uv run python bot.py
```

Al iniciar, verás algo como:

```
Discord bot connected as NombreDelBot#1234 (authorised user ID: 123456789012345678)
```

Los slash commands se registran automáticamente en todos los servidores donde está el bot.

### Comandos disponibles en Discord

Todos los comandos Telegram tienen su equivalente en Discord como slash command:

| Slash Command | Equivalente Telegram | Descripción |
|---|---|---|
| `/start` | `/start` | Inicializar |
| `/help` | `/help` | Referencia de comandos |
| `/status` | `/status` | Estado de sesión |
| `/cd [path]` | `/cd` | Cambiar directorio |
| `/claude [prompt]` | `/claude` | Ejecutar con Claude Code |
| `/codex [prompt]` | `/codex` | Ejecutar con Codex |
| `/bash <command>` | `/bash` | Comando shell |
| `/branch [name]` | `/branch` | Gestión de rama Git |
| `/exit` | `/exit` | Salir del modo Claude |
| `/stop` | `/stop` | Interrumpir ejecución |
| `/reset` | `/reset` | Limpiar contexto |
| `/save <name>` | `/save` | Guardar directorio como proyecto |
| `/projects` | `/projects` | Listar proyectos guardados |
| `/engine <motor>` | `/engine` | Cambiar motor de IA |

> **Nota:** Audio y fotos no están disponibles en Discord (Telegram sí los tiene por la integración con Whisper).
> Para Discord, pega el texto directamente en el canal cuando el modo Claude esté activo.

---

## Remote Control (modo `claude-channel`)

El modo **Remote Control** usa el sistema de **Canales MCP** de Claude Code para mantener una **sesión persistente** de Claude Code en lugar de lanzar un proceso nuevo por cada mensaje.

### Diferencias clave

| | Modo `claude` (por defecto) | Modo `claude-channel` (Remote Control) |
|---|---|---|
| Proceso | Nuevo proceso por mensaje | Un proceso persistente |
| Contexto | Mantenido vía `--resume` | Nativo en la sesión |
| Herramientas MCP | Recargadas cada vez | Siempre cargadas |
| Streaming | Sí (tiempo real) | No (respuesta completa al final) |
| Complejidad | Baja | Media (requiere `.mcp.json`) |

### Activar el modo Remote Control

#### 1. Instalar `aiohttp` (ya incluido en `uv sync`)

```powershell
uv sync
```

#### 2. Verificar el archivo `.mcp.json`

El archivo `.mcp.json` en la raíz del bot ya está configurado. Claude Code lo leerá automáticamente al arrancar en modo canal:

```json
{
  "mcpServers": {
    "channel": {
      "command": "uv",
      "args": ["run", "python", "-m", "companion.core.channel_server"],
      "env": {
        "CHANNEL_PORT": "8789",
        "CHANNEL_HOST": "127.0.0.1"
      }
    }
  }
}
```

#### 3. Configurar el motor en `.env`

```env
AI_ENGINE=claude-channel
```

O cambiarlo en tiempo real desde el bot:

- **Telegram:** `/engine claude-channel`
- **Discord:** `/engine claude-channel`

#### 4. Ejecutar el bot

```powershell
uv run python bot.py
```

El bot inicia Claude Code automáticamente con el canal webhook al recibir el primer prompt.

### Arquitectura

```
Telegram / Discord
       │
       ▼
Python Bot (bot.py)
       │ HTTP POST /prompt
       ▼
channel_server.py (servidor MCP + HTTP)
       │ notifications/claude/channel
       ▼
Claude Code (sesión persistente)
  ├── Bash, Edit, Read, Write...
  └── reply(chat_id, text)
       │
       ▼
channel_server.py → HTTP 200 → Python Bot → Telegram / Discord
```

### Codex sigue funcionando con subprocess

El motor `codex` siempre usa subprocess directamente (no tiene Remote Control todavía):

- **Telegram:** `/codex <prompt>` o `/engine codex`
- **Discord:** `/codex <prompt>` o `/engine codex`

---

## Variables de entorno

Relevantes para este flujo:

- `TELEGRAM_TOKEN` (requerida)
- `TELEGRAM_USER_ID` (requerida)
- `DISCORD_TOKEN` (opcional; activa el bot de Discord)
- `DISCORD_USER_ID` (opcional; ID numérico del usuario autorizado en Discord)
- `AI_ENGINE` (opcional; `claude` por defecto, también `codex` o `claude-channel`)
- `CODEX_MODEL` (opcional; modelo de Codex, ej. `o4-mini`)
- `CHANNEL_PORT` (opcional; por defecto `8789`, puerto del servidor MCP de canal)
- `CHANNEL_HOST` (opcional; por defecto `127.0.0.1`)
- `WHISPER_MODEL` (opcional; por defecto `small`)
- `WHISPER_DEVICE` (opcional; `auto`, `cpu` o `cuda`)
- `WHISPER_COMPUTE_TYPE` (opcional; por defecto `int8`)
- `WHISPER_BEAM_SIZE` (opcional; por defecto `5`)
- `WHISPER_VAD_FILTER` (opcional; por defecto `true`)
- `WHISPER_PRIMARY_LANGUAGE` (opcional; por defecto `es`)
- `WHISPER_ALLOW_AUTO_FALLBACK` (opcional; por defecto `true`)
- `INITIAL_DIR` (opcional)
- `RESTRICT_PATHS` (opcional; restringe a `cwd` cuando el CLI lo soporte)
- `SAFE_MODE` (opcional, por defecto `true`; bloquea comandos destructivos)
- `INACTIVITY_TIMEOUT_SECS` (opcional, por defecto `1800`)
- `INACTIVITY_CHECK_SECS` (opcional, por defecto `30`)
- `MAX_IMAGE_HISTORY` (opcional, por defecto `50`)
- `MAX_PENDING_IMAGES` (opcional, por defecto `10`)
- `TRAY_ICON_ENABLED` (opcional, por defecto `true`)
- `FULLSTACK_FRONT_CMD` / `FULLSTACK_FRONT_DIR` (opcional)
- `FULLSTACK_BACK_CMD` / `FULLSTACK_BACK_DIR` (opcional)
- `BACKEND_RUNBOOK_FILE` / `ENFORCE_BACKEND_RUNBOOK` (opcional)
- `BLOCKED_PATTERNS` (opcional)
- `SERVE_PORT` (opcional, por defecto `8080`)
