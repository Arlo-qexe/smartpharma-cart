"""Prueba de punta a punta EN VIVO con la Arducam: captura las caras de una caja y corre el reconocedor.

    python entrenamiento/en_vivo.py --modelo modelos/best.onnx --camara 1 --config ../dataset_ia/arducam/camara.json

ESPACIO captura una cara · P procesa con las caras que haya · N caja nueva (descarta lo capturado) · Q sale.
Cada cara se codifica a JPEG en memoria y se entrega a Reconocedor.procesar(list[bytes]), igual que
haría la Orange Pi. No guarda nada a disco salvo que uses --guardar (para revisar después qué falló).
Requiere la carpeta shared/ en la raíz del repo (ver IA/CLAUDE.md).
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import camara  # noqa: E402
from smartpharma_ia.config import cargar_config  # noqa: E402
from smartpharma_ia.detector import DetectorCNN  # noqa: E402
from smartpharma_ia.ocr import crear_ocr  # noqa: E402
from smartpharma_ia.pipeline import Reconocedor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", required=True, help="ruta a best.onnx")
    ap.add_argument("--camara", type=int, default=0)
    ap.add_argument("--config", type=Path, default=Path("camara.json"))
    ap.add_argument("--caras", type=int, default=5, help="caras por caja; procesa solo al llegar a ese número")
    ap.add_argument("--guardar", type=Path, help="carpeta donde guardar las caras de cada prueba (opcional)")
    a = ap.parse_args()

    rec = Reconocedor(ocr=crear_ocr("rapidocr"),
                      detector=DetectorCNN(a.modelo, conf_minima=float(os.environ.get("IA_CONF", "0.25"))),
                      config=cargar_config())
    cam = camara.abrir(a.camara, config=camara.cargar_config(a.config))
    if cam is None:
        print(f"No se pudo abrir la cámara {a.camara}", file=sys.stderr)
        return 1

    caras: list[bytes] = []
    prueba, resultado = 1, ["ESPACIO captura la cara 1"]
    while True:
        frame = camara.leer(cam, 1)
        if frame is None:
            print("Fallo leyendo frame", file=sys.stderr)
            break
        vista = cv2.resize(frame, (960, 540))
        lineas = [f"prueba {prueba}  caras {len(caras)}/{a.caras}"] + resultado
        for i, t in enumerate(lineas):
            cv2.putText(vista, t, (10, 28 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        cv2.imshow("en vivo", vista)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("n"):
            caras, prueba, resultado = [], prueba + (1 if caras else 0), ["ESPACIO captura la cara 1"]
        elif k == ord(" "):
            foto = camara.leer(cam)
            if foto is None:
                continue
            ok, buf = cv2.imencode(".jpg", foto, [cv2.IMWRITE_JPEG_QUALITY, 90])
            caras.append(buf.tobytes())
            if a.guardar:
                a.guardar.mkdir(parents=True, exist_ok=True)
                (a.guardar / f"prueba{prueba:03d}_cara{len(caras)}.jpg").write_bytes(buf.tobytes())
            resultado = [f"cara {len(caras)} capturada"]
        if len(caras) >= a.caras or (k == ord("p") and caras):
            t0 = time.perf_counter()
            r = rec.procesar(caras)
            seg = time.perf_counter() - t0
            por_cara = " | ".join(f"c{l.indice + 1}: lote={l.lote} venc={l.vencimiento}" for l in r.lecturas)
            print(f"prueba {prueba}: {r.clasificacion} lote={r.lote} venc={r.vencimiento} "
                  f"motivo={r.motivo} conf={r.confianza:.2f} {seg:.1f}s\n   {por_cara}")
            resultado = [f"{r.clasificacion}  lote={r.lote}  venc={r.vencimiento}",
                         f"motivo={r.motivo}  {seg:.1f}s   (N = caja nueva)"]
            caras = []
            prueba += 1
    cam.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
