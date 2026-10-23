"""Prepara el dataset YOLO y entrena el detector de regiones (nombre, gramaje, lote, fecha).

Entrada: (a) carpeta exportada de Roboflow en formato "YOLOv8" (tiene data.yaml; se detecta sola), o
(b) carpeta plana con cajaNNN_caraK.jpg + cajaNNN_caraK.txt (etiquetas YOLO, clases en CLASES).

    python entrenamiento/entrenar.py --datos ../dataset_ia/etiquetado --salida ../dataset_ia/yolo
    python entrenamiento/entrenar.py --datos ... --salida ... --epocas 100 --modelo yolov8s.pt

El split train/val se hace POR CAJA (no por foto) para que las 5 caras de una misma caja
no queden repartidas entre entrenamiento y validación. Al final exporta ONNX junto a best.pt.
"""
import argparse
import random
import re
import shutil
from pathlib import Path

CLASES = ["nombre", "gramaje", "lote", "fecha"]  # mismo orden que ETIQUETAS_CAMPO


def aplanar_roboflow(datos: Path, tmp: Path) -> Path:
    """Export 'YOLOv8' de Roboflow (train/valid/test + data.yaml) -> carpeta plana cajaNNN_caraK.jpg/.txt.
    Junta los 3 splits de Roboflow (se re-divide por caja), restaura el nombre original y
    REMAPEA los ids de clase: Roboflow los ordena distinto (p. ej. alfabético) que CLASES."""
    import yaml

    nombres = yaml.safe_load((datos / "data.yaml").read_text())["names"]
    if isinstance(nombres, dict):
        nombres = [nombres[k] for k in sorted(nombres)]
    desconocidas = set(nombres) - set(CLASES)
    if desconocidas:
        raise SystemExit(f"Clases en Roboflow que no están en {CLASES}: {sorted(desconocidas)}")
    mapa = {i: CLASES.index(n) for i, n in enumerate(nombres)}
    tmp.mkdir(parents=True, exist_ok=True)
    n = 0
    for img in sorted(datos.glob("*/images/*.jpg")):
        m = re.match(r"(caja\d+_cara\d+)", img.name)
        if not m:
            print("ignorada (nombre no reconocido):", img.name)
            continue
        base = m.group(1)
        shutil.copy2(img, tmp / f"{base}.jpg")
        txt = img.parent.parent / "labels" / (img.stem + ".txt")
        lineas = []
        if txt.exists():
            for linea in txt.read_text().splitlines():
                c = linea.split()
                if c:
                    lineas.append(" ".join([str(mapa[int(c[0])])] + c[1:]))
        (tmp / f"{base}.txt").write_text("\n".join(lineas))
        n += 1
    print(f"Roboflow: {n} imágenes aplanadas, clases remapeadas {dict(enumerate(nombres))} -> {CLASES}")
    return tmp


def preparar(datos: Path, salida: Path, frac_val: float, semilla: int) -> Path:
    if (datos / "data.yaml").exists():
        datos = aplanar_roboflow(datos, salida.parent / (salida.name + "_plano"))
    fotos = sorted(datos.glob("caja*_cara*.jpg"))
    if not fotos:
        raise SystemExit(f"No hay caja*_cara*.jpg en {datos}")
    cajas = sorted({re.match(r"(caja\d+)_", f.name).group(1) for f in fotos})
    random.Random(semilla).shuffle(cajas)
    n_val = max(1, round(len(cajas) * frac_val))
    val = set(cajas[:n_val])
    for sub in ("train", "val"):
        for tipo in ("images", "labels"):
            (salida / tipo / sub).mkdir(parents=True, exist_ok=True)
    for f in fotos:
        sub = "val" if f.name.split("_")[0] in val else "train"
        shutil.copy2(f, salida / "images" / sub / f.name)
        txt = f.with_suffix(".txt")
        if txt.exists():  # sin .txt = foto sin regiones (válido como negativo)
            shutil.copy2(txt, salida / "labels" / sub / txt.name)
    yaml = salida / "data.yaml"
    yaml.write_text(f"path: {salida.resolve()}\ntrain: images/train\nval: images/val\n"
                    f"names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASES)))
    print(f"{len(cajas)} cajas: {len(cajas) - n_val} train / {n_val} val")
    return yaml


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True, type=Path)
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--modelo", default="yolov8n.pt")
    ap.add_argument("--epocas", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=960, help="texto pequeño (lote): no bajar de 640")
    ap.add_argument("--val", type=float, default=0.2)
    ap.add_argument("--semilla", type=int, default=0)
    ap.add_argument("--device", default=None, help="'cpu', '0' (GPU)... por defecto auto")
    a = ap.parse_args()

    yaml = preparar(a.datos, a.salida, a.val, a.semilla)
    from ultralytics import YOLO

    modelo = YOLO(a.modelo)
    modelo.train(data=str(yaml), epochs=a.epocas, imgsz=a.imgsz, device=a.device,
                 project=str((a.salida / "runs").resolve()), name="detector", exist_ok=True,
                 fliplr=0.0, flipud=0.0)  # NO espejar: invertiría el texto
    print("métricas val:", modelo.val(project=str((a.salida / "runs").resolve()), name="val", exist_ok=True).box.maps)
    print("ONNX:", modelo.export(format="onnx", imgsz=a.imgsz))


if __name__ == "__main__":
    main()
