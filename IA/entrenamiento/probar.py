"""Prueba de punta a punta: detector (best.onnx) + OCR sobre fotos cajaNNN_caraK.jpg.
Agrupa las 5 caras de cada caja, corre Reconocedor y escribe un CSV para comparar con las cajas reales.

    python entrenamiento/probar.py --modelo ruta/best.onnx --fotos dataset_ia/crudo --csv resultados.csv
Requiere la carpeta shared/ en la raíz del repo (ver IA/CLAUDE.md) y: pip install -r requirements.txt -r requirements-ocr.txt ultralytics onnxruntime
"""
import argparse
import csv
import re
import sys
import time
from datetime import date
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smartpharma_ia import parseo  # noqa: E402
from smartpharma_ia.config import cargar_config  # noqa: E402
from smartpharma_ia.detector import DetectorCNN  # noqa: E402
from smartpharma_ia.ocr import crear_ocr  # noqa: E402
from smartpharma_ia.pipeline import Reconocedor  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--modelo", required=True)
ap.add_argument("--fotos", required=True, type=Path)
ap.add_argument("--csv", default="resultados.csv", type=Path)
ap.add_argument("--debug", type=Path, help="carpeta donde guardar los recortes detectados y ver qué lee el OCR en cada uno")
ap.add_argument("--referencia", type=Path,
                help="CSV caja,lote,vencimiento con los valores REALES (vencimiento 2028-10-31 o 10/2028) para medir exactitud")
a = ap.parse_args()

cajas = defaultdict(list)
for f in sorted(a.fotos.glob("caja*_cara*.jpg")):
    cajas[re.match(r"(caja\d+)_", f.name).group(1)].append(f)

def cargar_referencia(ruta):
    ref = {}
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        for fila in csv.DictReader(f):
            try:
                venc = date.fromisoformat(fila["vencimiento"].strip())
            except ValueError:
                venc = parseo.parsear_fecha(fila["vencimiento"])
            ref[f"caja{int(re.sub(r'[^0-9]', '', fila['caja'])):03d}"] = (fila["lote"].replace(" ", "").upper(), venc)
    return ref


def depurar(rec, fotos, carpeta):
    """Por cada cara: regiones detectadas, texto crudo del OCR (y rotado 90 grados si el recorte es alto)."""
    import io

    from PIL import Image

    carpeta.mkdir(parents=True, exist_ok=True)
    for f in fotos:
        img = Image.open(io.BytesIO(f.read_bytes())).convert("RGB")
        regs = rec.detector.detectar(img)
        print(f"  {f.name}: {len(regs)} regiones -> {[(r.etiqueta, round(r.confianza, 2)) for r in regs]}")
        for r in regs:
            rec_img = img.crop(r.caja)
            rec_img.save(carpeta / f"{f.stem}_{r.etiqueta}.jpg")
            w, h = rec_img.size
            lecturas = {"normal": rec.ocr.leer(rec_img)[0]}
            if h > 1.5 * w:  # texto vertical
                lecturas["rot90"] = rec.ocr.leer(rec_img.rotate(90, expand=True))[0]
                lecturas["rot-90"] = rec.ocr.leer(rec_img.rotate(-90, expand=True))[0]
            for k, t in lecturas.items():
                print(f"     {r.etiqueta:8s} [{k}] {t!r}")


rec = Reconocedor(ocr=crear_ocr("rapidocr"), detector=DetectorCNN(a.modelo, conf_minima=float(__import__("os").environ.get("IA_CONF", "0.25"))), config=cargar_config())
with open(a.csv, "w", newline="", encoding="utf-8-sig") as out:
    w = csv.writer(out)
    w.writerow(["caja", "clasificacion", "motivo", "lote", "vencimiento", "confianza", "segundos", "lote_ok", "venc_ok", "lecturas_por_cara"])
    tiempos, aciertos = [], []
    ref = cargar_referencia(a.referencia) if a.referencia else {}
    for caja, fotos in cajas.items():
        if a.debug:
            print(caja)
            depurar(rec, fotos, a.debug)
        datos = [f.read_bytes() for f in fotos]
        t0 = time.perf_counter()
        r = rec.procesar(datos)
        tiempos.append(time.perf_counter() - t0)
        caras = " | ".join(f"c{l.indice + 1}: lote={l.lote} venc={l.vencimiento}" for l in r.lecturas)
        lote_ok = venc_ok = ""
        if caja in ref:
            lote_ok = int((r.lote or "").replace(" ", "").upper() == ref[caja][0])
            venc_ok = int(r.vencimiento == ref[caja][1])
            aciertos.append((lote_ok, venc_ok))
        w.writerow([caja, r.clasificacion, r.motivo, r.lote, r.vencimiento, f"{r.confianza:.2f}", f"{tiempos[-1]:.2f}", lote_ok, venc_ok, caras])
        print(f"{caja}: {r.clasificacion} lote={r.lote} venc={r.vencimiento} motivo={r.motivo} ({tiempos[-1]:.1f} s)")
if tiempos:
    print(f"Tiempo por caja ({len(tiempos)} cajas): media {sum(tiempos) / len(tiempos):.1f} s, máximo {max(tiempos):.1f} s")
if aciertos:
    n = len(aciertos)
    ambos = sum(1 for l, v in aciertos if l and v)
    print(f"Exactitud vs referencia ({n} cajas): lote {sum(l for l, _ in aciertos)}/{n}, vencimiento {sum(v for _, v in aciertos)}/{n}, "
          f"ambos {ambos}/{n} ({100 * ambos / n:.0f} %)")
elif a.referencia:
    print("La referencia no coincide con ninguna caja de --fotos (revisa los números de caja)")
print("CSV:", a.csv)
