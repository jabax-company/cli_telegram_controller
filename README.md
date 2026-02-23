# Mobile Coding Agent — Fase 1

Bot de Telegram que refina ideas de desarrollo con Claude (Haiku) y las ejecuta
mediante Claude Code CLI en un workflow de GitHub Actions, abriendo un PR automático.

## Flujo

```
Tú (Telegram) → /repo + idea
      ↓
  Bot pregunta 2-3 clarificaciones (Haiku, barato)
      ↓
  Muestra prompt final + botón ✅ Ejecutar
      ↓
  GitHub Actions: checkout → claude --print → commit → PR
      ↓
  Bot te manda el link al PR por Telegram
```

---

## Requisitos previos

- Python 3.11+
- Cuenta de GitHub con un repo donde alojar este bot
- Bot de Telegram creado con [@BotFather](https://t.me/BotFather)
- Tu Telegram user ID (usa [@userinfobot](https://t.me/userinfobot))

---

## Instalación local

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

| Variable | Descripción |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot (de @BotFather) |
| `TELEGRAM_USER_ID` | Tu ID numérico de Telegram |
| `ANTHROPIC_API_KEY` | API key de Anthropic |
| `ANTHROPIC_MODEL` | Modelo para refinamiento (por defecto: `claude-haiku-4-5-20251001`) |
| `GH_PAT` | GitHub Personal Access Token (permisos: `repo` + `workflow`) |
| `GITHUB_WORKFLOW_REPO_OWNER` | Tu usuario de GitHub |
| `GITHUB_WORKFLOW_REPO_NAME` | Nombre de este repo (ej. `claude_code_bot`) |

---

## Secretos en GitHub

Ve a **Settings → Secrets and variables → Actions** de este repo y añade:

| Secret | Valor |
|---|---|
| `TELEGRAM_TOKEN` | Igual que en `.env` |
| `ANTHROPIC_API_KEY` | Igual que en `.env` |
| `GH_PAT` | Igual que en `.env` |

> El `GH_PAT` necesita acceso de escritura al **repo objetivo** (el repo sobre
> el que Claude va a hacer cambios), no solo a este repo.

---

## Ejecutar el bot en local

```bash
python bot.py
```

---

## Uso

```
/start              → Instrucciones
/repo owner/repo    → Establece el repositorio objetivo (persiste hasta que lo cambies)
<escribe tu idea>   → Inicia el refinamiento con Haiku
✅ Ejecutar         → Dispara el workflow de GitHub Actions
/cancelar           → Resetea la conversación (mantiene el repo configurado)
```

**Ejemplo de sesión:**

```
Tú:   /repo miusuario/mi-api-python
Bot:  ✅ Repositorio configurado: miusuario/mi-api-python

Tú:   quiero añadir autenticación con JWT
Bot:  ¿Los endpoints que hay que proteger son todos, o solo algunos específicos?

Tú:   solo /admin y /dashboard
Bot:  📋 Prompt generado:
      Añadir autenticación JWT al proyecto...
      [botones ✅ Ejecutar / ❌ Cancelar]

Tú:   [click ✅ Ejecutar]
Bot:  🚀 Workflow en marcha. Síguelo aquí: https://github.com/...

      (2-4 minutos después)

Bot:  ✅ PR listo: https://github.com/miusuario/mi-api-python/pull/7
```

---

## Probar el workflow manualmente

Desde la pestaña **Actions** de este repo → *Agente Claude Code* → **Run workflow**:

```
prompt:        Añade un endpoint GET /health que devuelva {"status": "ok"}
repo_objetivo: owner/nombre-repo
rama_base:     main
chat_id:       TU_CHAT_ID_NUMERICO
```

---

## Notas de diseño

- **Sin base de datos**: el historial de conversación vive en memoria; se pierde al reiniciar el bot. Suficiente para la Fase 1.
- **Whitelist**: solo responde a `TELEGRAM_USER_ID`. Añadir más usuarios requiere cambiar la función `is_authorized` en `bot.py`.
- **Rama base**: siempre `main`. Para cambiarla, modifica `RAMA_BASE` en `bot.py`.
- **Modelo**: Haiku por defecto (barato). Cámbialo con `ANTHROPIC_MODEL` en `.env`.
