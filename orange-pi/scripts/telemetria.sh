#!/usr/bin/env bash
# Monitor de terminal de la telemetría de la Orange Pi y la última de la ESP32-S3 (local,
# fuera del contrato; no necesita la PC). Correr en OTRA terminal mientras main.py está en marcha:
#   bash orange-pi/scripts/telemetria.sh            # se refresca cada segundo; Ctrl+C sale
#   bash orange-pi/scripts/telemetria.sh --una-vez  # imprime una vez y sale
set -euo pipefail
OPI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${OPI_VENV:-$OPI_DIR/.venv}"
if [ -f "$VENV_DIR/bin/activate" ]; then
  set +u; source "$VENV_DIR/bin/activate"; set -u
fi
exec python3 "$OPI_DIR/src/monitor_telemetria.py" "$@"
