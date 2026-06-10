# CLI Telegram Controller

> Controla **Claude Code** y **Codex CLI** desde Telegram. Envía prompts de texto, voz o imagen desde tu móvil y ejecuta el CLI directamente en tu máquina local, con soporte de ramas Git, publicación web via Cloudflare y watchdog de inactividad automático.

---

## ¿Qué es?

**CLI Telegram Controller** es un **bot de Telegram** que actúa como interfaz remota para [Claude Code](https://claude.ai/code) y Codex CLI. Te permite:

- Ejecutar prompts de Claude desde cualquier dispositivo con Telegram
- Navegar y operar tu sistema de archivos remotamente
- Publicar proyectos locales en internet vía Cloudflare Tunnel
- Enviar notas de voz transcritas localmente (sin API externa)
- Adjuntar imágenes como contexto para Claude
- Gestionar ramas Git con bloqueo automático
- Desplegar stacks fullstack (frontend + backend) con una sola URL

Todo corre **localmente en tu máquina**. Cloudflare solo expone una URL pública hacia tu localhost; ningún dato de código pasa por servidores externos salvo la propia API de Anthropic.

---

## Características

| Categoría | Funcionalidad |
|-----------|--------------|
| **Core** | Ejecución de Claude Code CLI con streaming de output en tiempo real |
| **Sesiones** | Continuidad de sesión con `--resume` automático entre prompts |
| **Optimización** | Flujo interactivo de Q&A para refinar prompts antes de ejecutar |
| **Multimodal** | Texto, notas de voz (transcritas localmente) e imágenes |
| **Git** | Bloqueo de rama (`/branch`): Claude solo puede modificar esa rama |
| **Navegación** | Navegador de carpetas con botones inline + proyectos guardados |
| **Servidor** | Publicación de sitios estáticos, proxies y fullstack via Cloudflare |
| **Seguridad** | Blocklist de patrones destructivos, autorización por user ID, Safe Mode |
| **Watchdog** | Auto-shutdown de Claude + túneles tras inactividad configurable |
| **Windows** | Icono en bandeja del sistema con botón Stop |
| **Auditoría** | Log de todas las acciones con timestamp |

---

## Requisitos

### Sistema
- **OS**: Windows (probado), Linux/macOS (compatible)
- **Python**: 3.13 exactamente (`requires-python = ">=3.13,<3.14"`)
- **uv**: gestor de paquetes recomendado → [instalación](https://docs.astral.sh/uv/)

### Herramientas externas (deben estar en PATH)
- **Claude Code CLI**: instalado y autenticado (`claude auth login`)
- **cloudflared**: para publicar URLs públicas con `/server` → [descarga](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)

### Telegram
- Bot creado con [@BotFather](https://t.me/BotFather) → obtendrás un `TELEGRAM_TOKEN`
- Tu `TELEGRAM_USER_ID` (usa [@userinfobot](https://t.me/userinfobot) para obtenerlo)

---

## Instalación

### 1. Clonar el repositorio

```powershell
git clone https://github.com/YOUR_USER/cli_telegram_controller
cd cli_telegram_controller
```

### 2. Instalar Python 3.13 y sincronizar dependencias

```powershell
uv python install 3.13
uv python pin 3.13
uv sync
```

O con pip estándar:

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```powershell
Copy-Item .env.example .env
```

Edita `.env` con tus valores mínimos obligatorios:

```env
TELEGRAM_TOKEN=123456:ABC-DEF...
TELEGRAM_USER_ID=987654321
```

### 4. Autenticar Claude Code CLI

```powershell
claude auth login
```

### 5. Lanzar el bot

```powershell
uv run python bot.py
```

Si todo está bien, verás en consola:

```
CLI Telegram Controller started. Authorized user: 987654321
```

El bot está listo. Abre Telegram y envía `/start`.

---

## Uso rápido

### Flujo básico

```
1. /start          → ver estado actual
2. /paths          → navegar carpetas con botones (o /cd C:\ruta\proyecto)
3. /claude         → activar modo Claude + iniciar Q&A de optimización
4. Responde las preguntas del bot para refinar el prompt
5. /claude         → ejecutar prompt optimizado
6. /exit           → salir de modo Claude
```

### Modo directo (sin optimización)

```
/claude <tu prompt aquí>   → ejecuta inmediatamente sin Q&A
```

Una vez activado el modo Claude, cada mensaje de texto se envía directamente a Claude sin necesidad de `/claude`.

---

## Referencia de comandos

### Navegación

| Comando | Descripción |
|---------|-------------|
| `/cd <ruta>` | Cambiar directorio actual |
| `/3d <ruta>` | Alias de `/cd` (útil para dictado por voz) |
| `/base` | Ver directorio base actual |
| `/base <ruta>` | Cambiar directorio base |
| `/base reset` | Resetear base al directorio inicial del bot |
| `/mkdir <nombre>` | Crear carpeta y entrar en ella |
| `/paths [ruta]` | Navegador interactivo de carpetas con botones inline |
| `/projects` | Listar proyectos guardados |
| `/save <nombre>` | Guardar directorio actual como proyecto |

### Ejecución de Claude

| Comando | Descripción |
|---------|-------------|
| `/claude` | Activar modo Claude o ejecutar prompt pendiente |
| `/claude <texto>` | Ejecutar prompt directamente |
| `/exit` | Salir de modo Claude |
| `/plan <tarea>` | Pedir plan sin ejecutar código |
| `/stop` | Interrumpir ejecución actual |
| `/reset` | Limpiar contexto y sesión de Claude |

### Git

| Comando | Descripción |
|---------|-------------|
| `/branch <nombre>` | Crear/cambiar a rama y bloquear cambios en ella |
| `/branch off` | Limpiar bloqueo de rama |

### Sistema

| Comando | Descripción |
|---------|-------------|
| `/bash <cmd>` | Ejecutar comando shell directo (con timeout, desactivable) |
| `/status` | Ver estado de sesión, carpeta y prompt pendiente |
| `/help` | Lista completa de comandos |
| `/bot stop` | Apagar el proceso del bot remotamente |

### Control del ordenador

| Comando | Descripción |
|---------|-------------|
| `/sysinfo` | CPU, RAM, disco, batería y uptime |
| `/screenshot` | Captura la pantalla y la envía al chat |
| `/ps [nombre]` | Procesos principales (filtro opcional por nombre) |
| `/kill <pid>` | Terminar proceso (pide confirmación: `/kill <pid> yes`) |
| `/lock` | Bloquear la pantalla del ordenador |
| `/download <ruta>` | Enviar un archivo del ordenador al chat (máx. 50 MB) |
| *(enviar documento)* | Cualquier documento no-imagen se guarda en `<cwd>/incoming/` |

### Publicación web (`/server` / `/serve`)

| Comando | Descripción |
|---------|-------------|
| `/server` | Publicar carpeta actual como sitio estático |
| `/server proxy <puerto>` | Publicar servicio local ya corriendo |
| `/server run <puerto> <cmd>` | Ejecutar comando y publicar resultado |
| `/server fullstack <fport> <bport>` | Frontend + backend bajo una URL |
| `/server fullstack <fport> <bport> <prefix>` | Con prefijo de API personalizado |
| `/server status` | Ver estado del túnel activo |
| `/server stop` | Detener túnel y procesos asociados |
| `/server help` | Prompt para auto-configuración |

---

## Entrada multimodal

### Voz (Audio)

Envía una nota de voz o archivo de audio. El bot:
1. Descarga el audio
2. Transcribe **localmente** con [faster-whisper](https://github.com/guillaumekln/faster-whisper) (sin API externa)
3. Muestra la transcripción
4. Si estás en modo Claude → ejecuta directamente
5. Si no → guarda como prompt pendiente para ejecutar con `/claude`

**Configuración de Whisper en `.env`:**

```env
WHISPER_MODEL=small              # tiny / base / small / medium / large-v3
WHISPER_DEVICE=auto              # auto / cpu / cuda
WHISPER_COMPUTE_TYPE=int8        # int8 / int8_float16 / float16 / float32
WHISPER_PRIMARY_LANGUAGE=es      # es / en / fr / ...
WHISPER_ALLOW_AUTO_FALLBACK=true # Si falla el idioma, intenta detección automática
```

### Imágenes

Envía una foto o imagen:
- **Con caption en modo Claude** → se ejecuta inmediatamente con la imagen como contexto
- **Sin caption** → se añade a la cola de imágenes pendientes para el siguiente prompt
- Puedes usar `<image>` o `<images>` en tu prompt para referenciar imágenes guardadas

Las imágenes se guardan en `images/` en la raíz del repositorio del bot.

---

## Publicación web con Cloudflare

Todos los comandos `/server` usan **Cloudflare Tunnel** para exponer tu localhost en una URL pública `*.trycloudflare.com`. No requiere cuenta de Cloudflare.

### Sitio estático

```
/server
```

Publica la carpeta actual (`cwd`) como web estática.

### Proxy de servicio existente

```
/server proxy 5000
```

Expone `localhost:5000` directamente.

### Arrancar + publicar

```
/server run 5000 python app.py
/server run 3000 npm run dev
```

Ejecuta el comando y expone el puerto resultante.

### Fullstack (una sola URL)

```
/server fullstack 3000 5000
/server fullstack 3000 5000 /backend
```

Crea un reverse proxy local que enruta:
- `GET /api/*` → backend (`5000`)
- Todo lo demás → frontend (`3000`)

Si algún puerto no está levantado, el bot intenta arrancar el proceso automáticamente detectando:
- `FULLSTACK_FRONT_CMD` / `FULLSTACK_BACK_CMD` en `.env`
- `.claude/server.json` con comandos explícitos
- `package.json` con scripts `dev`/`start` en carpetas candidatas
- `.claude/backend_run.json` con comando de backend
- Entrypoints Python (`manage.py`, `app.py`, `main.py`)

**`.claude/server.json` (configuración explícita):**

```json
{
  "frontend": { "dir": "frontend", "cmd": "npm run dev", "port": 3000 },
  "backend":  { "dir": "backend",  "cmd": "npm run dev", "port": 5000, "api_prefix": "/api" }
}
```

**`.claude/backend_run.json` (solo backend):**

```json
{
  "command": "uv run python main.py",
  "workdir": "backend",
  "port": 8000,
  "api_prefix": "/api"
}
```

---

## Seguridad

### Autorización
Solo el usuario cuyo ID coincide con `TELEGRAM_USER_ID` puede interactuar con el bot. Todos los handlers verifican la identidad antes de ejecutar. Los intentos no autorizados se registran en el audit log con ID y username del atacante.

### Solo chats privados
Por defecto (`PRIVATE_CHAT_ONLY=true`) el bot ignora cualquier mensaje fuera de un chat privado, para que rutas de archivos y output nunca se filtren en grupos.

### Blocklist de patrones
Los mensajes enviados a Claude son inspeccionados antes de ejecutarse. Se bloquean:
- `rm -rf /`, `rm -rf *` y variantes
- `dd if=/dev/`, `mkfs`, fork bombs (`:(){ :|:& };:`)
- `git reset --hard`, `git clean -f`
- `curl | bash`, `wget | bash`, `python | bash`
- Destructivos de Windows: `del /f /s /q`, `rd /s`, `format c:`, `diskpart`, `cipher /w`, `vssadmin delete`, `reg delete`
- Lectura de credenciales: `cat ~/.ssh/id_rsa`, `cat ... credentials`
- Patrones personalizados vía `BLOCKED_PATTERNS` en `.env`

Por defecto un patrón bloqueado se **rechaza sin posibilidad de bypass**. El antiguo override "responde YES para ejecutar igualmente" solo existe si activas `ALLOW_BLOCKED_OVERRIDE=true`.

### /bash endurecido
- `ENABLE_BASH=false` desactiva el comando por completo.
- Todo comando `/bash` se mata automáticamente tras `BASH_TIMEOUT_SECS` (300s por defecto).

### Safe Mode (`SAFE_MODE=true`, activo por defecto)
Cuando está activado:
- Agrega `--disallowedTools` a Claude con comandos destructivos bloqueados
- Inyecta un prompt de sistema con reglas de seguridad
- Nunca ejecuta comandos de eliminación de archivos/directorios

### Branch Lock
`/branch <nombre>` bloquea el contexto de Claude a esa rama específica. Si el directorio de trabajo cambia de repo, Claude lanza un error antes de ejecutar.

---

## Variables de entorno completas

### Obligatorias

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_TOKEN` | Token del bot (de @BotFather) |
| `TELEGRAM_USER_ID` | Tu ID de Telegram (solo este usuario puede usar el bot) |

### Directorio

| Variable | Default | Descripción |
|----------|---------|-------------|
| `INITIAL_DIR` | `~` (home) | Directorio inicial al arrancar el bot |

### Seguridad

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SAFE_MODE` | `true` | Bloquea comandos destructivos en Claude |
| `RESTRICT_PATHS` | `false` | Claude solo puede tocar el `cwd` actual |
| `BLOCKED_PATTERNS` | vacío | Patrones CSV extra para bloquear |
| `ALLOW_BLOCKED_OVERRIDE` | `false` | Permite el override YES sobre patrones bloqueados |
| `PRIVATE_CHAT_ONLY` | `true` | Ignorar mensajes fuera de chats privados |
| `ENABLE_BASH` | `true` | Permite el comando `/bash` |
| `BASH_TIMEOUT_SECS` | `300` | Timeout de comandos `/bash` |
| `CLAUDE_SKIP_PERMISSIONS` | `true` | `false` usa `--permission-mode acceptEdits` en vez de skip |
| `MAX_DOWNLOAD_MB` | `50` | Tamaño máximo de `/download` |
| `AUDIT_MAX_BYTES` | `5242880` | Rotación del audit log |

### Audio (Whisper)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `WHISPER_MODEL` | `small` | Modelo: `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_DEVICE` | `auto` | `auto` / `cpu` / `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` / `float16` / `float32` |
| `WHISPER_BEAM_SIZE` | `5` | Beam size para decodificación |
| `WHISPER_VAD_FILTER` | `true` | Filtro de actividad de voz |
| `WHISPER_PRIMARY_LANGUAGE` | `es` | Idioma principal |
| `WHISPER_ALLOW_AUTO_FALLBACK` | `true` | Auto-detect si falla el idioma |

### Imágenes

| Variable | Default | Descripción |
|----------|---------|-------------|
| `MAX_IMAGE_HISTORY` | `50` | Máximo de imágenes recordadas por chat |
| `MAX_PENDING_IMAGES` | `10` | Máximo de imágenes en cola para adjuntar |

### Inactividad

| Variable | Default | Descripción |
|----------|---------|-------------|
| `INACTIVITY_TIMEOUT_SECS` | `900` | Segundos sin interacción → cierra Claude + túneles |
| `INACTIVITY_CHECK_SECS` | `30` | Frecuencia del watchdog de inactividad |

### Publicación web

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SERVE_PORT` | `8080` | Puerto para modo estático |
| `FULLSTACK_FRONT_CMD` | auto-detect | Comando para levantar frontend |
| `FULLSTACK_FRONT_DIR` | auto-detect | Carpeta del frontend |
| `FULLSTACK_BACK_CMD` | auto-detect | Comando para levantar backend |
| `FULLSTACK_BACK_DIR` | auto-detect | Carpeta del backend |
| `BACKEND_RUNBOOK_FILE` | `.claude/backend_run.json` | JSON con comando de backend |
| `ENFORCE_BACKEND_RUNBOOK` | `true` | Instruye a Claude a mantener ese archivo actualizado |

### UI

| Variable | Default | Descripción |
|----------|---------|-------------|
| `TRAY_ICON_ENABLED` | `true` | Icono en bandeja Windows (requiere pystray + Pillow) |

---

## Estructura del proyecto

```
cli_telegram_controller/
│
├── bot.py                      # Entrypoint principal
├── main.py                     # Entrypoint alternativo
├── fullstack_proxy.py          # Reverse proxy HTTP (frontend/backend)
├── test_serve.py               # Publicador estático standalone
├── setup.sh                    # Script de configuración interactivo
├── requirements.txt            # Dependencias pip
├── pyproject.toml              # Configuración uv / metadatos
├── .env.example                # Plantilla de variables de entorno
│
├── companion/
│   ├── app.py                  # Bootstrap: Application Telegram + watchdog
│   │
│   ├── core/
│   │   ├── activity.py         # Watchdog de inactividad
│   │   ├── audio.py            # Transcripción con faster-whisper
│   │   ├── auth.py             # Autorización por TELEGRAM_USER_ID
│   │   ├── browser.py          # Navegador de carpetas con botones inline
│   │   ├── claude_runtime.py   # Orquestación de claude CLI (spawn, stream, resume)
│   │   ├── config.py           # Carga de .env y constantes globales
│   │   ├── paths.py            # Resolución de rutas (relativas, ~, /base, proyectos)
│   │   ├── prompt_optimizer.py # Flujo Q&A para refinar prompts
│   │   ├── runtime_control.py  # Stop remoto thread-safe (tray + comandos)
│   │   ├── security.py         # Blocklist de patrones peligrosos
│   │   ├── server_runtime.py   # Lógica de /server (cloudflared, auto-detect)
│   │   ├── state.py            # Estado en-memoria por chat
│   │   ├── storage.py          # Persistencia (proyectos JSON + audit log)
│   │   └── tray_icon.py        # Icono bandeja Windows (pystray)
│   │
│   └── handlers/
│       ├── callbacks.py        # Botones inline (/paths, /projects)
│       ├── commands.py         # Todos los command handlers
│       └── messages.py         # Texto, audio, imágenes y passthrough
│
└── images/                     # Imágenes recibidas (tempdir de Claude)
```

---

## Dependencias

```
python-telegram-bot~=21.0   # SDK oficial Telegram async
python-dotenv~=1.0          # Carga de .env
anthropic~=0.40             # SDK Anthropic (uso futuro)
faster-whisper>=1.1.0,<2    # Transcripción local de audio
pystray~=0.19               # Icono bandeja sistema (Windows)
Pillow~=10.0                # Generación del icono + /screenshot
psutil~=6.0                 # /sysinfo, /ps, /kill
```

### Tests

```bash
pip install pytest
python -m pytest tests/
```

---

## Cómo funciona internamente

### Ciclo de vida de un prompt

```
Usuario envía mensaje
   ↓
handle_message() / handle_audio() / handle_image()
   ↓
¿claude_mode? → run_task() → spawn_claude()
   ↓
claude CLI  --dangerously-skip-permissions
            -p "<prompt>"
            --output-format stream-json
            --include-partial-messages
            [--resume <session_id>]
            [--append-system-prompt "..."]
   ↓
output_reader() → parsea JSON stream → chunks de texto → Telegram
   ↓
Extrae session_id para próximo --resume
```

### Continuidad de sesión

CLI Telegram Controller usa `--resume <session_id>` automáticamente para mantener el contexto entre prompts consecutivos dentro del mismo chat. El `session_id` se extrae del stream JSON de Claude y se guarda en `state["session_id"]`.

### Optimización de prompts

Al escribir `/claude` sin argumentos:
1. El bot activa modo intake y pregunta: *"¿Cuál es el resultado final exacto que quieres?"*
2. Luego pregunta sobre rutas/archivos, restricciones técnicas, y opcionalmente formato de salida y validación
3. Construye un prompt estructurado con toda esa información
4. El prompt optimizado queda pendiente y se ejecuta con el siguiente `/claude`

### Publicación fullstack

```
/server fullstack 3000 5000
         ↓
Verifica puertos → arranca procesos si faltan
         ↓
fullstack_proxy.py (puerto libre ~8080)
   /api/* → :5000 (backend)
   /*     → :3000 (frontend)
         ↓
cloudflared tunnel → https://xyz.trycloudflare.com
         ↓
Bot envía URL a Telegram
```

---

## Troubleshooting

**El bot no responde:**
- Verifica que `TELEGRAM_TOKEN` y `TELEGRAM_USER_ID` son correctos
- Comprueba que el proceso `bot.py` está corriendo

**Claude no ejecuta:**
- Verifica que `claude` está en PATH: `claude --version`
- Verifica autenticación: `claude auth login`

**Audio no transcribe:**
- Instala faster-whisper: `pip install faster-whisper`
- Prueba con `WHISPER_MODEL=tiny` para menor uso de RAM

**`/server` no abre túnel:**
- Verifica que `cloudflared` está en PATH: `cloudflared --version`
- Descarga desde: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

**Icono de bandeja no aparece (Windows):**
- Instala dependencias GUI: `pip install pystray Pillow`
- O desactiva con `TRAY_ICON_ENABLED=false` en `.env`

---

## Licencia

MIT
