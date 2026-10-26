#!/usr/bin/env bash
# Puesta en marcha del lado PC con la configuración de PRUEBAS actual: activa el venv y
# lanza src/server.py (servidor de lotes, consulta de decisión/orden del ciclo, panel
# web y asistente).
#
# Uso (desde cualquier carpeta):
#   bash pc/scripts/start_pc.sh
#
# Configuración de pruebas por defecto (cada variable se puede sobrescribir al lanzar):
#   ASISTENTE_MODO=prueba   asistente SIN modelo (reglas fijas, NO es un LLM).
#                           Desactivarlo:  ASISTENTE_MODO= bash pc/scripts/start_pc.sh
#   ASISTENTE_MODELO=...    modelo real .gguf (Fase 2, aún sin probar); tiene prioridad.
#   PC_LOG_LEVEL=INFO       nivel de log (DEBUG|INFO|WARNING|ERROR)
#   PANEL_HOST / PANEL_PUERTO   panel web (por defecto 127.0.0.1 y su puerto de panel_web.py)
#   PC_IP=192.168.x.y       fuerza la IP que se anuncia por mDNS (si la PC tiene varias redes
#                           y la elegida no es la de la Orange Pi). Sin ella se usa la de la
#                           ruta por defecto, y sin ruta por defecto la de la interfaz real.
#   PC_DB=...               archivo del registro de eventos (por defecto pc/data/smartpharma.db).
#                           `:memory:` = sin persistencia (demos y pruebas).
#   PC_VENV=...             otro entorno virtual (por defecto pc/.venv)
#
# IMPORTANTE: conecta la PC a la red de la demo ANTES de lanzarlo. La IP se fija al arrancar;
# si cambias de red con el servidor corriendo, detenlo (Ctrl+C) y vuelve a lanzarlo.
#
# Los puertos NO se escriben aquí: se leen de shared/protocol_constants.py y panel_web.py.
# Detener: Ctrl+C. El REGISTRO DE EVENTOS (lotes, alarmas, decisiones, órdenes del ciclo) se guarda
# en disco y sobrevive a los reinicios; el resto del estado (lotes con fotos, alarmas activas,
# decisión en espera) vive en RAM y se pierde al detener.
set -euo pipefail

PC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PC_VENV:-$PC_DIR/.venv}"
cd "$PC_DIR"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo "ERROR: no existe el entorno virtual en $VENV_DIR" >&2
  echo "       Créalo una vez con:  bash $PC_DIR/scripts/setup_venv.sh" >&2
  exit 1
fi
set +u                                   # activate puede leer variables sin definir
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
set -u

# El PYTHONPATH de ROS Jazzy rompe paquetes en esta máquina; esto solo afecta a este proceso.
unset PYTHONPATH

# Por defecto, asistente en modo de prueba; `ASISTENTE_MODO=` (vacío) lo desactiva.
export ASISTENTE_MODO="${ASISTENTE_MODO-prueba}"

# Puertos y host del panel: una sola fuente de verdad (el contrato y panel_web.py).
read -r P_LOTES P_DECISION P_PANEL H_PANEL < <(python3 -c "
import sys
sys.path.insert(0, 'src')
from panel_web import PANEL_HOST, PANEL_PUERTO
from protocol_constants import TCP_PUERTO_DEFECTO, TCP_PUERTO_DECISION_DEFECTO
print(TCP_PUERTO_DEFECTO, TCP_PUERTO_DECISION_DEFECTO, PANEL_PUERTO, PANEL_HOST)
")

# Antes de arrancar: ¿hay otro servidor ocupando los puertos?
if command -v ss >/dev/null 2>&1; then
  ocupados=()
  for p in "$P_LOTES" "$P_DECISION" "$P_PANEL"; do
    if ss -ltnH "( sport = :$p )" 2>/dev/null | grep -q .; then ocupados+=("$p"); fi
  done
  if [ "${#ocupados[@]}" -gt 0 ]; then
    echo "ERROR: ya hay algo escuchando en el/los puerto(s): ${ocupados[*]}" >&2
    echo "       Probablemente otro servidor de la PC sigue corriendo. Búscalo con:" >&2
    echo "         ss -ltnp | grep -E ':($(IFS='|'; echo "${ocupados[*]}"))\\b'" >&2
    echo "       y deténlo por su PID (kill <PID>) antes de volver a lanzar este script." >&2
    exit 1
  fi
fi

if [ -n "${ASISTENTE_MODELO:-}" ]; then
  etiqueta_asistente="modelo real ($ASISTENTE_MODELO)"
elif [ "${ASISTENTE_MODO:-}" = "prueba" ]; then
  etiqueta_asistente="modo de prueba (sin modelo, reglas fijas)"
else
  etiqueta_asistente="desactivado"
fi
# La IP que realmente se anunciará por mDNS (misma lógica que usa el servidor).
IFS='|' read -r IP_ANUNCIADA ORIGEN_IP < <(python3 -c "
import sys
sys.path.insert(0, 'src')
from mdns_service import elegir_ip_anunciada
ip, origen = elegir_ip_anunciada()
print(f'{ip}|{origen}')
")
# Dónde queda el registro de eventos (misma lógica que usa el servidor).
RUTA_REGISTRO="$(python3 -c "
import sys
sys.path.insert(0, 'src')
from auditoria import ruta_db_desde_entorno
print(ruta_db_desde_entorno())
")"

echo "=== SmartPharma Cart — lado PC ==="
echo " Panel del regente : http://$H_PANEL:$P_PANEL/"
echo " Lotes (TCP)       : $P_LOTES    Decisión y orden del ciclo (TCP): $P_DECISION"
echo " IP que se anuncia : $IP_ANUNCIADA  ($ORIGEN_IP)"
case "$IP_ANUNCIADA" in
  127.*)
    echo " ATENCIÓN: esa IP es de loopback y la Orange Pi NO podrá conectarse." >&2
    echo "           Conecta la PC a la red de la demo y vuelve a lanzar, o fuerza PC_IP=<ip>." >&2 ;;
esac
echo " Asistente         : $etiqueta_asistente"
echo " Registro eventos  : $RUTA_REGISTRO"
echo " Detener con Ctrl+C (lotes, alarmas y decisión en curso viven en RAM; el registro persiste)"
echo "=================================="

# exec: Ctrl+C llega directo al servidor, que se apaga ordenadamente (baja el mDNS).
exec python3 -u src/server.py "$@"
