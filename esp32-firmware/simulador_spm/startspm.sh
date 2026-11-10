#!/usr/bin/env bash
# Inicia el simulador del SPM: comprueba dependencias y datos, muestra una cinemática de muestra y abre la interfaz gráfica con el modelo CAD.
# Uso: ./startspm.sh [--sin-gui] [--completo] [--3d] [--abrir] [--lock]
#   --sin-gui   no abre la interfaz (solo comprobaciones y cinemática de muestra)
#   --completo  además corre las pruebas, la demo (gráficas, GIF) y el mapa del espacio de trabajo
#   --3d        además genera el vídeo del modelo CAD en movimiento (~90 s)
#   --abrir     con --completo/--3d, abre las imágenes/vídeo generados con el visor del sistema
#   --lock      al crear .venv, instala las versiones exactas de requirements-lock.txt
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

GUI=1; COMPLETO=0; V3D=0; ABRIR=0; LOCK=0
for a in "$@"; do
  case "$a" in
    --sin-gui)  GUI=0 ;;
    --completo) COMPLETO=1 ;;
    --3d)       V3D=1 ;;
    --abrir)    ABRIR=1 ;;
    --lock)     LOCK=1 ;;
    -h|--help)  sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $a" >&2; exit 2 ;;
  esac
done

echo "== Simulador SPM =="
command -v python3 >/dev/null || { echo "ERROR: falta python3" >&2; exit 1; }

# entorno virtual reproducible (.venv): se crea e instala requirements.txt la primera vez (o requirements-lock.txt con --lock)
if [ ! -x .venv/bin/python ]; then
  echo "-- creando .venv --"
  python3 -m venv .venv || { echo "ERROR: no se pudo crear .venv (en Manjaro/Arch: sudo pacman -S python tk; en Debian/Ubuntu: sudo apt install python3-venv python3-tk)" >&2; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r "$([ "$LOCK" -eq 1 ] && echo requirements-lock.txt || echo requirements.txt)" || { rm -rf .venv; echo "ERROR: falló pip install" >&2; exit 1; }
fi
PY=.venv/bin/python
echo "python: $($PY --version) (.venv)"

$PY - <<'PY' || { echo "ERROR: faltan dependencias. Falta tkinter: sudo pacman -S tk (o python3-tk en Debian/Ubuntu)" >&2; exit 1; }
import importlib
faltan = []
for m, opcional in (("numpy", 0), ("matplotlib", 0), ("PIL", 0), ("tkinter", 0), ("serial", 1), ("OpenGL", 1)):
    try:
        mod = importlib.import_module(m)
        print("  %-11s %s" % (m, getattr(mod, "__version__", "ok")))
    except ImportError:
        print("  %-11s %s" % (m, "NO (opcional: sin él no hay pestaña Serial / se usa el render por software)" if opcional else "NO"))
        if not opcional: faltan.append(m)
raise SystemExit(1 if faltan else 0)
PY

for f in datos/geometria.json datos/malla_cuerpos.npz; do
  [ -f "$f" ] || { echo "ERROR: falta $f (ver herramientas/ y README)" >&2; exit 1; }
done
[ -f datos/malla_gpu.npz ] && echo "  malla fina para GPU: sí" || echo "  malla fina para GPU: NO (render por software; genérala con FreeCADCmd herramientas/exportar_malla_gpu.py)"
[ -f datos/mapa_colision.npz ] && echo "  mapa de colisiones del CAD: sí" || echo "  mapa de colisiones del CAD: NO (se usará un cono de 28°; genera uno con: $PY -m spm colisiones)"
mkdir -p salida

if [ "$COMPLETO" -eq 1 ]; then
  echo "-- pruebas --"
  $PY -m unittest discover -s tests 2>&1 | tail -4
fi

echo "-- cinemática de muestra (inclinación 12°, azimut 40°, torsión 5°) --"
$PY -m spm ik --inclinacion 12 --azimut 40 --torsion 5

if [ "$COMPLETO" -eq 1 ]; then
  echo "-- modelo 3D del CAD en esa pose --"
  $PY -m spm modelo --inclinacion 12 --azimut 40 --torsion 5
  echo "-- demo (trayectorias, seguimiento, animación) --"
  $PY -m spm demo
  echo "-- mapa del espacio de trabajo --"
  $PY -m spm mapa
fi
if [ "$V3D" -eq 1 ]; then
  echo "-- vídeo del modelo CAD (precesión) --"
  $PY -m spm demo3d
fi
if [ "$ABRIR" -eq 1 ] && command -v xdg-open >/dev/null; then
  for f in salida/modelo_pose.png salida/modelo3d_precesion.mp4 salida/precesion.gif salida/mapa_espacio_trabajo.png salida/seguimiento.png; do
    [ -f "$f" ] && xdg-open "$f" >/dev/null 2>&1 &
  done
fi

echo
echo "Resultados (si se generaron) en $(pwd)/salida"
if [ "$GUI" -eq 1 ]; then
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    echo "No hay pantalla (DISPLAY/WAYLAND_DISPLAY vacíos): no se abre la interfaz." >&2
  else
    echo "Abriendo la interfaz gráfica…"
    exec $PY interfaz_spm.py
  fi
fi
echo "Interfaz:  .venv/bin/python interfaz_spm.py   ·   CLI: .venv/bin/python -m spm ik|fk|modelo|demo|demo3d|mapa|colisiones"
