# Claude Code Companion (local mode)

Bot de Telegram para controlar Claude Code en tu maquina local.

## Tutorial desde cero

### 1) Requisitos

- Windows + PowerShell (este README usa esos comandos)
- Python 3.13
- `uv` instalado
- Claude Code CLI instalado y autenticado
- `cloudflared` instalado (para publicar URL en internet)
- Bot de Telegram creado con @BotFather

### 2) Clonar y entrar al repo

```powershell
git clone https://github.com/YOUR_USER/claude_code_bot
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

Para audio:

- `WHISPER_MODEL` (opcional, por defecto `small`)
- opcional: `WHISPER_DEVICE=auto`
- opcional: `WHISPER_COMPUTE_TYPE=int8`
- opcional: `WHISPER_PRIMARY_LANGUAGE=es` (por defecto espanol)
- opcional: `WHISPER_ALLOW_AUTO_FALLBACK=true` (permite audios en ingles)

### 5) Login de Claude Code

```powershell
claude auth login
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

## Variables de entorno

Relevantes para este flujo:

- `TELEGRAM_TOKEN` (requerida)
- `TELEGRAM_USER_ID` (requerida)
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
- `INACTIVITY_TIMEOUT_SECS` (opcional, por defecto `900`; cierra Claude + server tras inactividad)
- `INACTIVITY_CHECK_SECS` (opcional, por defecto `30`; frecuencia del watchdog de inactividad)
- `MAX_IMAGE_HISTORY` (opcional, por defecto `50`; maximo de imagenes recordadas por chat)
- `MAX_PENDING_IMAGES` (opcional, por defecto `10`; maximo de imagenes pendientes por usar)
- `TRAY_ICON_ENABLED` (opcional, por defecto `true`; icono en bandeja de Windows con boton Stop)
- `FULLSTACK_FRONT_CMD` (opcional; comando para levantar frontend en `/server fullstack`)
- `FULLSTACK_FRONT_DIR` (opcional; carpeta del frontend)
- `FULLSTACK_BACK_CMD` (opcional; comando para levantar backend en `/server fullstack`)
- `FULLSTACK_BACK_DIR` (opcional; carpeta del backend)
- `BACKEND_RUNBOOK_FILE` (opcional; archivo JSON con comando de backend)
- `ENFORCE_BACKEND_RUNBOOK` (opcional; por defecto `true`, instruye a Claude a mantener ese archivo)
- `BLOCKED_PATTERNS` (opcional)
- `SERVE_PORT` (opcional, por defecto 8080 para modo estatico)
