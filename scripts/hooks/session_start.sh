#!/usr/bin/env bash
# Hook de Claude Code (SessionStart): se corre al iniciar una sesión dentro de
# orange-pi/ o pc/. Hace dos cosas:
#   1. git pull --ff-only desde la raíz del repo, para que la sesión arranque
#      siempre con la versión más reciente de shared/.
#   2. Verifica que exista el venv local (.venv dentro de la carpeta donde se
#      invocó `claude`) y, si no existe, le avisa al agente que lo cree antes
#      de instalar nada con pip fuera de un entorno virtual.
#
# Se referencia desde orange-pi/.claude/settings.json y pc/.claude/settings.json.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
NOTAS=()

if [ -n "$REPO_ROOT" ]; then
  cd "$REPO_ROOT"
  if git pull --ff-only --quiet 2>/tmp/_git_pull_err; then
    :
  else
    NOTAS+=("git pull --ff-only falló (posible conflicto o cambios locales sin commitear). Revisa manualmente: $(cat /tmp/_git_pull_err | tr '\n' ' ')")
  fi
fi

# CLAUDE_PROJECT_DIR es la carpeta donde se invocó `claude` (orange-pi/ o pc/)
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
if [ ! -d "$PROJECT_DIR/.venv" ]; then
  NOTAS+=("No existe .venv en $PROJECT_DIR. Antes de instalar o correr nada con pip, crea el entorno virtual: bash scripts/setup_venv.sh (ver CLAUDE.md, sección Entorno). Nunca instales paquetes con el Python del sistema.")
fi

if [ ${#NOTAS[@]} -gt 0 ]; then
  JOINED=$(printf '%s\n' "${NOTAS[@]}" | sed ':a;N;$!ba;s/\n/ \\n/g')
  printf '{"hookSpecificOutput": {"additionalContext": "%s"}}\n' "$JOINED"
fi

exit 0
