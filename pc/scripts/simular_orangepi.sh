#!/usr/bin/env bash
# Orange Pi SIMULADA con fotos ya tomadas (sin cámara, UART ni mDNS). Con el servidor corriendo
# (start_pc.sh), recorre las cajas del conjunto de entrenamiento como lo haría la Orange Pi.
# Opciones: ver `bash pc/scripts/simular_orangepi.sh --help`.
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PC_VENV:-$RAIZ/.venv}"
[ -x "$VENV/bin/python" ] || { echo "Falta el venv ($VENV). Crea uno con: bash $RAIZ/scripts/setup_venv.sh" >&2; exit 1; }
exec env -u PYTHONPATH "$VENV/bin/python" "$RAIZ/tests/simular_orangepi.py" "$@"
