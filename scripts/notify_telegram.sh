#!/usr/bin/env bash
# Envía a Telegram un aviso de que shared/ cambió. Pensado para correr dentro del
# job de GitHub Actions .github/workflows/notify-shared-changes.yml (recibe las
# variables de entorno que ese workflow exporta), pero también se puede correr a
# mano para probar localmente.
#
# Variables de entorno requeridas: BOT_TOKEN, CHAT_ID, COMMIT_AUTHOR, COMMIT_MSG,
# COMMIT_URL, GITHUB_REF (o BRANCH directamente).
set -euo pipefail

if [ -z "${BOT_TOKEN:-}" ] || [ -z "${CHAT_ID:-}" ]; then
  echo "::warning::TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no están configurados como secrets. Ver docs/TELEGRAM_SETUP.md."
  exit 0
fi

BRANCH="${BRANCH:-${GITHUB_REF##*/}}"
FILES=$(git diff --name-only HEAD^ HEAD -- shared/ docs/CHANGELOG_protocolo.md || true)

TEXT="🔧 Cambio en shared/ — rama ${BRANCH}
Autor: ${COMMIT_AUTHOR:-desconocido}

${COMMIT_MSG:-}

Archivos modificados:
${FILES}

${COMMIT_URL:-}

⚠️ Ambos agentes (orange-pi y pc) deben hacer git pull antes de seguir trabajando en código que dependa del contrato."

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${TEXT}"
