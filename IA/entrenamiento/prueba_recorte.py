"""Prueba rápida de captura + recorte + OCR contra el lote/fecha REALES de la caja que tienes bajo la cámara.

    python entrenamiento/prueba_recorte.py --camara 0 --ancho 3264 --alto 2448 \
        --recorte 1208,898,636,652 --lote F02413A --venc 10/28 --salida dataset_ia/arducam/prueba_recorte
    python entrenamiento/prueba_recorte.py --foto ruta.jpg --recorte ... --lote ... --venc ...   # sin cámara

Prueba el recorte entero en 4 giros (0/90/180/270) y con cada prefiltro; imprime lo que lee el OCR, si
coincide con lo real y cuánto tarda. Sirve para comparar resolución, luz (rasante lateral) y prefiltro.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import camara  # noqa: E402
from smartpharma_ia import parseo, prefiltro  # noqa: E402
from smartpharma_ia.ocr import crear_ocr  # noqa: E402


def norm(t: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camara", type=int, default=0)
    ap.add_argument("--ancho", type=int, default=3264)
    ap.add_argument("--alto", type=int, default=2448)
    ap.add_argument("--config", type=Path, default=Path("camara.json"))
    ap.add_argument("--exposicion", type=float,
                    help="exposición manual (DirectShow: log2 de segundos, p. ej. -6 = 1/64 s; más negativo = más oscuro)")
    ap.add_argument("--foto", type=Path, help="usa esta foto en vez de la cámara")
    ap.add_argument("--recorte", help="x,y,ancho,alto de la zona de la caja (en píxeles de la foto)")
    ap.add_argument("--lote", required=True)
    ap.add_argument("--venc", required=True, help="vencimiento real, p. ej. 10/28 o 2028-10-31")
    ap.add_argument("--salida", type=Path, default=Path("prueba_recorte"))
    a = ap.parse_args()

    if a.foto:
        frame = cv2.imread(str(a.foto))
    else:
        config = camara.cargar_config(a.config)
        if a.exposicion is not None:
            config = {**config, "exposicion": a.exposicion}
        cam = camara.abrir(a.camara, a.ancho, a.alto, config)
        if cam is None:
            print("No abre la cámara", file=sys.stderr)
            return 1
        for _ in range(15):  # converge autoenfoque y exposición
            cam.read()
        frame = camara.leer(cam)
        cam.release()
    if frame is None:
        print("Sin imagen", file=sys.stderr)
        return 1
    h, w = frame.shape[:2]
    a.salida.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.salida / f"frame_{w}x{h}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if a.recorte:
        x, y, rw, rh = (int(v) for v in a.recorte.split(","))
        frame = frame[y:y + rh, x:x + rw]
    cv2.imwrite(str(a.salida / "recorte.png"), frame)
    print(f"frame {w}x{h} · recorte {frame.shape[1]}x{frame.shape[0]} · nitidez {camara.nitidez(frame):.0f} · "
          f"brillo {camara.brillo(frame):.0f}/255 (evita >200: se quema el relieve)")

    try:
        venc_real = parseo.parsear_fecha(a.venc)
    except Exception:  # noqa: BLE001
        venc_real = None
    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    ocr = crear_ocr("rapidocr")
    lote_real = norm(a.lote)
    ok_lote = ok_venc = False
    t0 = time.perf_counter()
    for nombre, f in (("directa", lambda i: i), *prefiltro.VARIANTES.items()):
        base = f(pil)
        for giro in (0, 90, 180, 270):
            t, c = ocr.leer(base.rotate(giro, expand=True) if giro else base, exhaustivo=False)
            lote_ok = lote_real in norm(t)
            venc_ok = venc_real is not None and parseo.buscar_vencimiento(t) == venc_real
            ok_lote, ok_venc = ok_lote or lote_ok, ok_venc or venc_ok
            if t:
                print(f"  {nombre:9s} giro {giro:3d}: {t.replace(chr(10), ' | ')!r} conf={c:.2f}"
                      f"{'  <- LOTE OK' if lote_ok else ''}{'  <- FECHA OK' if venc_ok else ''}")
    print(f"\nLote real {a.lote}: {'LEÍDO' if ok_lote else 'NO leído'} · Vencimiento real {a.venc}: "
          f"{'LEÍDO' if ok_venc else 'NO leído'} · {time.perf_counter() - t0:.1f} s en total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
