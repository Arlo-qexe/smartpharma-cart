#!/usr/bin/env bash
# Crea/actualiza el entorno virtual de orange-pi/. Correr desde orange-pi/:
#   bash scripts/setup_venv.sh
#
# IMPORTANTE: este venv es específico de la Orange Pi (ARM64, Armbian). NO es
# intercambiable con el venv de pc/ (x86_64) — cada máquina crea el suyo con
# este mismo script, nunca se copia ni se sube al repo (.venv/ está en
# .gitignore).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "Usando: $($PYTHON_BIN --version)"

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Listo. Para activar en esta terminal:  source .venv/bin/activate"
echo "Para reproducir exactamente estas versiones después (lockfile):"
echo "  pip freeze > requirements-lock.txt"
