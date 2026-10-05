#!/usr/bin/env bash
# Hook de Claude Code (UserPromptSubmit): avisa al agente, dentro de la misma
# sesión, si shared/ cambió en origin/main desde el último pull local. No
# bloquea nada (siempre sale 0) — solo inyecta contexto adicional para que el
# agente decida hacer `git pull` antes de seguir si el cambio es relevante.
#
# Se instala referenciado desde orange-pi/.claude/settings.json y
# pc/.claude/settings.json (ver ese archivo para el registro del hook).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  exit 0  # no es un repo git todavía (ej. antes del primer git init) -> no hacer nada
fi
cd "$REPO_ROOT"

# red/remoto no disponibles -> no bloquear ni ensuciar la salida
git fetch origin --quiet 2>/dev/null || exit 0

LOCAL_HASH=$(git rev-parse "HEAD:shared" 2>/dev/null || echo "none")
REMOTE_HASH=$(git rev-parse "origin/main:shared" 2>/dev/null || echo "none")

if [ "$REMOTE_HASH" != "none" ] && [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
  cat <<JSON
{"hookSpecificOutput": {"additionalContext": "AVISO AUTOMATICO: shared/ cambió en origin/main y tu copia local está desactualizada (hash local ${LOCAL_HASH:0:8}, remoto ${REMOTE_HASH:0:8}). Antes de modificar código que dependa del contrato de comunicación, corre 'git pull' y revisa docs/CHANGELOG_protocolo.md para ver qué cambió."}}
JSON
fi

exit 0
