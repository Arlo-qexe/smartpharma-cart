"""Realza el troquelado SIN apagar la luz general: resta fotos con y sin luz lateral.

Toma N fotos separadas --espera segundos, con la caja quieta y exposición MANUAL (si la exposición cambiara,
la resta no sirve). Durante la espera cambias la luz lateral:
    foto 1: solo la luz general   |  foto 2: + luz lateral (un lado)  |  foto 3 (opcional): + luz del lado contrario
La luz general se cancela al restar y queda el sombreado del relieve (2-1, 3-1 y 2-3). Luego lee cada imagen con OCR.

    python entrenamiento/prueba_diferencial.py --recorte 1200,955,652,669 --lote F02413A --venc 10/28 --fotos 3
    python entrenamiento/prueba_diferencial.py --carpeta dataset_ia/arducam/dif1 --recorte ... --lote ... --venc ...  # reusa f1/f2/f3.png
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import camara  # noqa: E402
from prueba_recorte import norm  # noqa: E402
from smartpharma_ia import parseo  # noqa: E402
from smartpharma_ia.ocr import crear_ocr  # noqa: E402


def realzar(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """b - a en gris, normalizado a 0-255 con 128 = sin cambio (claro = más luz de la lateral)."""
    ga = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype("float32"), (0, 0), 1.0)
    gb = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype("float32"), (0, 0), 1.0)
    d = gb - ga
    # El relieve es una variación fina sobre un cambio grande y suave de iluminación: se quita lo suave
    d -= cv2.GaussianBlur(d, (0, 0), 25)
    s = max(float(np.percentile(np.abs(d), 99)), 1e-3)
    return np.clip(128 + 127 * d / s, 0, 255).astype("uint8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camara", type=int, default=0)
    ap.add_argument("--ancho", type=int, default=3264)
    ap.add_argument("--alto", type=int, default=2448)
    ap.add_argument("--config", type=Path, default=Path("camara.json"))
    ap.add_argument("--exposicion", type=float, default=-6, help="manual y fija (ver prueba_recorte.py)")
    ap.add_argument("--fotos", type=int, default=3, choices=(2, 3))
    ap.add_argument("--espera", type=float, default=10, help="segundos entre fotos para cambiar la luz")
    ap.add_argument("--carpeta", type=Path, default=Path("prueba_diferencial"), help="salida (o entrada si ya hay f1.png...)")
    ap.add_argument("--recorte", required=True, help="x,y,ancho,alto")
    ap.add_argument("--lote", required=True)
    ap.add_argument("--venc", required=True)
    a = ap.parse_args()

    a.carpeta.mkdir(parents=True, exist_ok=True)
    rutas = [a.carpeta / f"f{i}.png" for i in range(1, a.fotos + 1)]
    if not all(r.exists() for r in rutas):
        cam = camara.abrir(a.camara, a.ancho, a.alto, {**camara.cargar_config(a.config), "exposicion": a.exposicion})
        if cam is None:
            print("No abre la cámara", file=sys.stderr)
            return 1
        for _ in range(20):
            cam.read()
        for i, r in enumerate(rutas, 1):
            if i > 1:
                print(f"  ... {a.espera:.0f} s para cambiar la luz (foto {i})", flush=True)
                t0 = time.time()
                while time.time() - t0 < a.espera:  # mantiene la cámara leyendo para que la exposición no se reinicie
                    cam.read()
            cv2.imwrite(str(r), camara.leer(cam))
            print(f"foto {i} guardada", flush=True)
        cam.release()
    x, y, w, h = (int(v) for v in a.recorte.split(","))
    fr = [cv2.imread(str(r))[y:y + h, x:x + w] for r in rutas]
    print("brillo de cada foto:", [round(camara.brillo(f)) for f in fr])

    imgs = {"foto1_general": fr[0], "foto2_lateral": fr[1], "dif_2-1": realzar(fr[0], fr[1])}
    if a.fotos == 3:
        imgs |= {"dif_3-1": realzar(fr[0], fr[2]), "dif_2-3": realzar(fr[2], fr[1])}
    try:
        venc = parseo.parsear_fecha(a.venc)
    except Exception:  # noqa: BLE001
        venc = None
    ocr = crear_ocr("rapidocr")
    lote_ok = venc_ok = False
    for nombre, im in imgs.items():
        cv2.imwrite(str(a.carpeta / f"{nombre}.png"), im)
        pil = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB) if im.ndim == 3 else cv2.cvtColor(im, cv2.COLOR_GRAY2RGB))
        for giro in (0, 90, 180, 270):
            t, c = ocr.leer(pil.rotate(giro, expand=True) if giro else pil, exhaustivo=False)
            lo, ve = norm(a.lote) in norm(t), venc is not None and parseo.buscar_vencimiento(t) == venc
            lote_ok, venc_ok = lote_ok or lo, venc_ok or ve
            if t:
                print(f"  {nombre:14s} giro {giro:3d}: {t.replace(chr(10), ' | ')!r} conf={c:.2f}{' <- LOTE OK' if lo else ''}{' <- FECHA OK' if ve else ''}")
    print(f"\nLote {a.lote}: {'LEÍDO' if lote_ok else 'NO leído'} · Vencimiento {a.venc}: {'LEÍDO' if venc_ok else 'NO leído'}")
    print("Mira las imágenes dif_*.png en", a.carpeta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
