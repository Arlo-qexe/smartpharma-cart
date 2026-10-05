#!/usr/bin/env bash
# Crea/actualiza el entorno virtual de pc/ (servidor de reconocimiento + asistente).
# Correr desde pc/:
#   bash scripts/setup_venv.sh          # instala todo en CPU
#   CUDA=1 bash scripts/setup_venv.sh   # además compila llama-cpp-python con soporte CUDA
#
# IMPORTANTE: este venv es específico de la PC (x86_64). NO es intercambiable
# con el venv de orange-pi/ (ARM64) — cada máquina crea el suyo con este mismo
# script, nunca se copia ni se sube al repo (.venv/ está en .gitignore).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "Usando: $($PYTHON_BIN --version)"

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

if [ -f assistant/requirements.txt ]; then
  if [ "${CUDA:-0}" = "1" ]; then
    echo "Instalando llama-cpp-python con build CUDA (puede tardar varios minutos)..."
    CMAKE_ARGS="-DGGML_CUDA=on" pip install --no-cache-dir -r assistant/requirements.txt
  else
    echo "Instalando dependencias del asistente (CPU). Usa CUDA=1 para GPU."
    pip install -r assistant/requirements.txt
  fi
fi

echo ""
echo "Listo. Para activar en esta terminal:  source .venv/bin/activate"
echo "Para reproducir exactamente estas versiones después (lockfile):"
echo "  pip freeze > requirements-lock.txt"
