# Mobile Coding Agent - Fase 1

Bot de Telegram que refina ideas de desarrollo con Claude (Haiku) y las ejecuta
mediante Claude Code CLI en un workflow de GitHub Actions.

Modo por defecto:
- iterativo sobre la misma rama
- PR solo cuando tu lo pides con `/pr`

## Flujo

```text
Tu (Telegram) -> /repo + idea
      |
  Bot pregunta 2-3 clarificaciones (Haiku)
      |
  Muestra prompt final + boton Ejecutar
      |
  GitHub Actions: checkout -> claude --print -> commit/push en rama activa
      |
  Bot envia estado + resumen de cambios por Telegram
      |
  Cuando quieras cerrar: /pr + siguiente ejecucion -> abre/reutiliza PR
```

---

## Requisitos previos

- Python 3.11+
- Cuenta de GitHub con un repo donde alojar este bot
- Bot de Telegram creado con [@BotFather](https://t.me/BotFather)
- Tu Telegram user ID (usa [@getmyid_bot](https://t.me/userinfobot))

---

## Instalacion local

```bash
# 1. Clonar y entrar al directorio
git clone https://github.com/TU_USUARIO/claude_code_bot
cd claude_code_bot

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Copiar y rellenar variables de entorno
cp .env.example .env
# Edita .env con tus valores reales
```

### Variables en `.env`

| Variable | Descripcion |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (de @BotFather) |
| `TELEGRAM_USER_ID` | Tu ID numerico de Telegram |
| `ANTHROPIC_API_KEY` | API key de Anthropic |
| `ANTHROPIC_MODEL` | Modelo para refinamiento (default: `claude-haiku-4-5-20251001`) |
| `GH_PAT` | GitHub Personal Access Token (permisos: `repo` + `workflow`) |
| `GITHUB_WORKFLOW_REPO_OWNER` | Tu usuario de GitHub |
| `GITHUB_WORKFLOW_REPO_NAME` | Nombre de este repo (ej. `claude_code_bot`) |

---

## Secretos en GitHub

Ve a **Settings -> Secrets and variables -> Actions** de este repo y anade:

| Secret | Valor |
|---|---|
| `TELEGRAM_TOKEN` | Igual que en `.env` |
| `ANTHROPIC_API_KEY` | Igual que en `.env` |
| `GH_PAT` | Igual que en `.env` |

> `GH_PAT` necesita acceso de escritura al repo objetivo (el repo donde Claude hara cambios), no solo a este repo.

---

## Ejecutar el bot en local

```bash
python bot.py
```

---

## Uso

```text
/start              -> Instrucciones
/repo owner/repo    -> Establece el repositorio objetivo
<escribe tu idea>   -> Inicia el refinamiento con Haiku
Ejecutar            -> Dispara el workflow de GitHub Actions
/rama               -> Ver rama activa
/rama nueva         -> Reiniciar rama activa
/pr                 -> La proxima ejecucion abre/reutiliza PR
/cancelar           -> Resetea la conversacion (mantiene repo configurado)
```

### Ejemplo de sesion iterativa

```text
Tu:   /repo miusuario/mi-api-python
Bot:  Repositorio configurado

Tu:   quiero anadir autenticacion JWT
Bot:  (preguntas de clarificacion)
Bot:  Prompt generado + botones Ejecutar/Cancelar

Tu:   [click Ejecutar]
Bot:  Workflow en marcha (rama agent/...)

Bot:  Workflow completado. Cambios aplicados en rama agent/...
      (puedes seguir enviando mas tareas y seguira en la misma rama)

Tu:   /pr
Bot:  Cierre con PR activado para la proxima ejecucion

Tu:   [click Ejecutar en la siguiente tarea]
Bot:  PR listo: https://github.com/miusuario/mi-api-python/pull/7
```

---

## Probar el workflow manualmente

Desde la pestana **Actions** de este repo -> *Agente Claude Code* -> **Run workflow**:

```text
prompt:         Anade un endpoint GET /health que devuelva {"status":"ok"}
repo_objetivo:  owner/nombre-repo
rama_base:      main
chat_id:        TU_CHAT_ID_NUMERICO
rama_existente: (opcional) agent/20260223-123000
abrir_pr:       false   # true para cerrar y abrir/reutilizar PR
```

---

## Notas de diseno

- Sin base de datos: el historial vive en memoria y se pierde al reiniciar el bot.
- Whitelist: solo responde a `TELEGRAM_USER_ID`.
- Rama base: `main` por defecto (`RAMA_BASE` en `bot.py`).
- Modelo: Haiku por defecto (`ANTHROPIC_MODEL` en `.env`).
