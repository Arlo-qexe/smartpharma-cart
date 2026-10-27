"""Busca el enfoque manual que deja más nítida una zona (p. ej. la línea del lote) y lo guarda en camara.json.

    python entrenamiento/barrido_enfoque.py --zona 1420,1670,370,90 --config dataset_ia/arducam/camara.json --guardar
Deja la caja quieta, con el lote hacia la cámara y la cámara cerrada en cualquier otra app.
La zona es x,y,ancho,alto en píxeles del frame (usa un frame guardado para ubicarla).
"""
import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camara  # noqa: E402


def nitidez_zona(frame, zona) -> float:
    x, y, w, h = zona
    gris = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gris, cv2.CV_64F).var())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camara", type=int, default=0)
    ap.add_argument("--ancho", type=int, default=3264)
    ap.add_argument("--alto", type=int, default=2448)
    ap.add_argument("--config", type=Path, default=Path("camara.json"))
    ap.add_argument("--zona", required=True, help="x,y,ancho,alto de la zona a enfocar")
    ap.add_argument("--desde", type=int, default=0)
    ap.add_argument("--hasta", type=int, default=255)
    ap.add_argument("--paso", type=int, default=10)
    ap.add_argument("--guardar", action="store_true", help="escribe autofoco=false y el mejor enfoque en --config")
    a = ap.parse_args()
    zona = tuple(int(v) for v in a.zona.split(","))

    config = camara.cargar_config(a.config)
    cam = camara.abrir(a.camara, a.ancho, a.alto, {**config, "autofoco": False, "enfoque": a.desde})
    if cam is None:
        print("No abre la cámara (¿otra app la está usando?)", file=sys.stderr)
        return 1
    resultados = []
    for f in range(a.desde, a.hasta + 1, a.paso):
        cam.set(cv2.CAP_PROP_FOCUS, f)
        time.sleep(0.8)  # el lente tarda en moverse
        frame = camara.leer(cam, descartar=6)
        if frame is None:
            continue
        n = nitidez_zona(frame, zona)
        resultados.append((n, f, cam.get(cv2.CAP_PROP_FOCUS)))
        print(f"enfoque {f:4d} (reporta {resultados[-1][2]:.0f}): nitidez de la zona = {n:7.1f}", flush=True)
    cam.release()
    if not resultados:
        print("Sin imágenes", file=sys.stderr)
        return 1
    mejor = max(resultados)
    print(f"\nMejor enfoque: {mejor[1]} (nitidez {mejor[0]:.1f}; el peor {min(resultados)[0]:.1f})")
    if a.guardar:
        camara.guardar_config(a.config, {**config, "autofoco": False, "enfoque": mejor[1]})
        print("Guardado en", a.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
