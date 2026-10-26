#!/usr/bin/env bash
# Consola de usuarios de la PC: crear el primer administrador, listar, restablecer una clave.
#
#   bash pc/scripts/usuarios.sh crear-admin
#   bash pc/scripts/usuarios.sh listar
#   bash pc/scripts/usuarios.sh restablecer-clave "Nombre"
#
# Usa la misma base que el servidor (PC_DB, por defecto pc/data/smartpharma.db); puede correrse
# con el servidor encendido. Ver el docstring de src/usuarios_cli.py.
set -euo pipefail
PC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PC_VENV:-$PC_DIR/.venv}"
cd "$PC_DIR"
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo "ERROR: no existe el entorno virtual en $VENV_DIR (créalo con: bash $PC_DIR/scripts/setup_venv.sh)" >&2
  exit 1
fi
set +u
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
set -u
unset PYTHONPATH
exec python3 src/usuarios_cli.py "$@"
