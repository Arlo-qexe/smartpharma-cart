#!/usr/bin/env bash
# Crea/actualiza el entorno virtual de ia/ (módulo de reconocimiento).
# Correr desde ia/:
#   bash scripts/setup_venv.sh          # núcleo + OCR (CPU)
#   OCR=0 bash scripts/setup_venv.sh    # solo el núcleo (pruebas sin motor OCR)
#
# El venv es local a esta máquina (.venv/ está en .gitignore); no se copia entre equipos.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "Usando: $($PYTHON_BIN --version)"

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
if [ "${OCR:-1}" = "1" ]; then
  pip install -r requirements-ocr.txt
fi

echo ""
echo "Listo. Para activar en esta terminal:  source .venv/bin/activate"
echo "Pruebas:  python -m unittest discover -s tests -t ."
echo "Cuando todo funcione, congela versiones:  pip freeze > requirements-lock.txt"
