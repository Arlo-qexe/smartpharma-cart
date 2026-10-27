"""Mide cuánto tarda cada etapa por cara (decodificar, detector, OCR por región) en 3 cajas de val_341_360.
Ojo: con la GPU entrenando la CPU está ocupada y los tiempos salen inflados. Rutas fijas (yolo_v3): edítalas. Ejecutar desde la raíz de ia/.
"""
import sys, time, io
from pathlib import Path
sys.path.insert(0, ".")
from PIL import Image
from smartpharma_ia.config import cargar_config
from smartpharma_ia.detector import DetectorCNN
from smartpharma_ia.ocr import crear_ocr
cfg = cargar_config(); det = DetectorCNN("dataset_ia/yolo_v3/runs/detector/weights/best.onnx", conf_minima=0.25); ocr = crear_ocr("rapidocr")
t=[0,0,0]; n=0
for caja in ("caja343","caja347","caja349"):
    for k in range(1,6):
        p = Path(f"dataset_ia/arducam/val_341_360/{caja}_cara{k}.jpg")
        t0=time.perf_counter(); datos=p.read_bytes(); im=Image.open(io.BytesIO(datos)).convert("RGB"); t1=time.perf_counter()
        regs=det.detectar(im); t2=time.perf_counter()
        for r in regs:
            a=time.perf_counter(); rec=im.crop(r.caja); ocr.leer(rec, exhaustivo=True); b=time.perf_counter()
            print(f"  {caja} c{k} {r.etiqueta:8s} crop {rec.size} ocr {b-a:.2f}s")
            t[2]+=b-a
        t[0]+=t1-t0; t[1]+=t2-t1; n+=1
print(f"por cara: decodificar {t[0]/n:.2f}s, detector {t[1]/n:.2f}s, OCR {t[2]/n:.2f}s")
