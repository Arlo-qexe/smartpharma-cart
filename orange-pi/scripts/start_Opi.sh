#!/usr/bin/env bash
# Puesta en marcha del lado Orange Pi: activa el venv, verifica UART, cámara y red, y
# lanza src/main.py (espera la orden "Iniciar recorrido" de la PC y luego corre el ciclo).
#
# Uso (desde cualquier carpeta):
#   bash orange-pi/scripts/start_Opi.sh                 # arranque normal
#   bash orange-pi/scripts/start_Opi.sh --verificar     # solo comprueba UART, cámara y PC y sale
#   bash orange-pi/scripts/start_Opi.sh --sin-orden     # no espera la orden de la PC (pruebas
#                                                       # sueltas; no es parte del contrato)
# Cualquier otro argumento se pasa tal cual a src/main.py.
#
# Qué hace, en orden:
#   1. Activa el venv (por defecto orange-pi/.venv; OPI_VENV=... para otro).
#   2. UART: comprueba que existe el puerto hacia la ESP32-S3 y que se puede abrir.
#   3. Cámara: la busca por NOMBRE (arducam, luego HP 430/435), igual que main.py.
#   4. Red: muestra la IP de esta placa e intenta localizar la PC por mDNS (solo informa:
#      main.py sigue esperando a la PC sin límite).
#   5. Lanza main.py. Con UART y cámara listas espera el botón "Iniciar recorrido" del panel
#      de la PC (sección 4.7 del informe).
#
# Los puertos y nombres NO se escriben aquí: salen de shared/protocol_constants.py y de src/.
# Detener: Ctrl+C (libera la cámara y el puerto serie).
set -euo pipefail

OPI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${OPI_VENV:-$OPI_DIR/.venv}"
cd "$OPI_DIR"

SOLO_VERIFICAR=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--verificar" ]; then SOLO_VERIFICAR=1; else ARGS+=("$a"); fi
done

if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo "ERROR: no existe el entorno virtual en $VENV_DIR" >&2
  echo "       Créalo una vez con:  bash $OPI_DIR/scripts/setup_venv.sh" >&2
  exit 1
fi
set +u                                   # activate puede leer variables sin definir
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
set -u

# Valores que viven en el código: puerto UART (main.py) y cámara (camera.py).
FALLO=0

PUERTO_UART="$(python3 -c "
import sys
sys.path[:0] = ['src', '../shared']
import main
print(main.PUERTO_UART)
")"

echo "=== SmartPharma Cart — lado Orange Pi ==="

# --- 1. UART ---------------------------------------------------------------------------
if [ ! -e "$PUERTO_UART" ]; then
  echo " UART              : FALLO — no existe $PUERTO_UART" >&2
  echo "                     Revisa que UART0 esté liberado de la consola serial (orange-pi/CLAUDE.md)." >&2
  FALLO=1
elif [ ! -r "$PUERTO_UART" ] || [ ! -w "$PUERTO_UART" ]; then
  echo " UART              : FALLO — sin permiso sobre $PUERTO_UART (usuario fuera del grupo dialout)" >&2
  FALLO=1
else
  echo " UART              : $PUERTO_UART  (115200)"
fi

# --- 2. Cámara -------------------------------------------------------------------------
if CAMARA="$(python3 -c "
import sys
sys.path[:0] = ['src', '../shared']
from capture.camera import buscar_camara_por_nombre
print(buscar_camara_por_nombre())
" 2>&1)"; then
  echo " Cámara            : $CAMARA"
else
  echo " Cámara            : FALLO — $(echo "$CAMARA" | tail -1)" >&2
  echo "                     Comprueba que la cámara USB esté conectada:  lsusb | grep -vi hub" >&2
  FALLO=1
fi

# --- 3. Red y PC (solo informa) --------------------------------------------------------
IP_LOCAL="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
if [ -n "$IP_LOCAL" ]; then
  echo " Esta placa        : $IP_LOCAL"
else
  echo " Esta placa        : sin ruta de red (¿WiFi conectado?)" >&2
fi
PC_ENCONTRADA="$(python3 -c "
import sys
sys.path[:0] = ['src', '../shared']
from network.mdns_discovery import DescubridorPC
ip, puerto = DescubridorPC().obtener_destino_sin_bloquear(timeout=5)
print(f'{ip}:{puerto}' if ip else '')
" 2>/dev/null || true)"
if [ -n "$PC_ENCONTRADA" ]; then
  echo " PC (mDNS)         : $PC_ENCONTRADA"
else
  echo " PC (mDNS)         : no encontrada aún (main.py seguirá buscándola; ¿está corriendo start_pc.sh?)"
fi
echo "========================================="

if [ "$FALLO" -ne 0 ]; then
  echo "No se arranca: corrige lo marcado como FALLO y vuelve a lanzar." >&2
  exit 1
fi
if [ "$SOLO_VERIFICAR" -eq 1 ]; then
  echo "Verificación correcta (--verificar: no se lanzó main.py)."
  exit 0
fi

echo " Esperando la orden 'Iniciar recorrido' del panel de la PC. Detener con Ctrl+C."
# exec: Ctrl+C llega directo a main.py, que libera la cámara y el puerto serie.
exec python3 -u src/main.py "${ARGS[@]+"${ARGS[@]}"}"
