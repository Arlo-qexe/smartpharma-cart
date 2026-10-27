"""Prepara el dataset YOLO y entrena el detector de regiones (nombre, gramaje, lote, fecha).

Entrada: (a) carpeta exportada de Roboflow en formato "YOLOv8" (tiene data.yaml; se detecta sola), o
(b) carpeta plana con cajaNNN_caraK.jpg + cajaNNN_caraK.txt (etiquetas YOLO, clases en CLASES).

    python entrenamiento/entrenar.py --datos dataset_ia/etiquetado --salida dataset_ia/yolo
    python entrenamiento/entrenar.py --datos ... --salida ... --epocas 150 --paciencia 25 --modelo yolov8s.pt

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


def leer_mapa(ruta: Path) -> dict[str, str]:
    """CSV caja_arducam,caja_celular (p. ej. 101,3): la caja física que se fotografió con las dos cámaras."""
    import csv

    def norm(s: str) -> str:
        return "caja%03d" % int(re.sub(r"\D", "", s))

    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return {norm(r["caja_arducam"]): norm(r["caja_celular"]) for r in csv.DictReader(f)}


def sobremuestrear(salida: Path, veces: int) -> int:
    """Duplica en train las fotos que tienen lote o fecha (clases minoritarias: ~89 etiquetas vs ~470 de nombre).
    Cada copia sale distinta porque el aumento de datos de YOLO es aleatorio. La validación NO se toca."""
    if veces <= 1:
        return 0
    prioritarias = {CLASES.index("lote"), CLASES.index("fecha")}
    n = 0
    for txt in sorted((salida / "labels" / "train").glob("*.txt")):
        ids = {int(l.split()[0]) for l in txt.read_text().splitlines() if l.split()}
        if not ids & prioritarias:
            continue
        img = salida / "images" / "train" / (txt.stem + ".jpg")
        for k in range(1, veces):
            shutil.copy2(img, img.with_name(f"{txt.stem}_dup{k}.jpg"))
            shutil.copy2(txt, txt.with_name(f"{txt.stem}_dup{k}.txt"))
            n += 1
    return n


def fisica(caja: str) -> str:
    """Las recapturas (cajas 401-499) son la caja física N-100 (401 = 301): validan o entrenan JUNTO con
    su original; si no, el modelo vería en entrenamiento el mismo lote que luego se usa para validar."""
    n = int(caja[4:])
    return f"caja{n - 100:03d}" if 400 <= n <= 499 else caja


def preparar(datos: Path, salida: Path, frac_val: float, semilla: int,
             arducam_desde: int | None = None, mapa: Path | None = None, solo_arducam: bool = False,
             sobremuestreo: int = 1) -> Path:
    """arducam_desde=N: las cajas con número >= N son de la Arducam y SOLO de ellas sale la validación
    (el celular siempre entrena). mapa: si una caja de validación Arducam es la misma caja física que una
    de celular, esa caja de celular se saca del entrenamiento (si no, el modelo vería el mismo lote al validar)."""
    if (datos / "data.yaml").exists():
        datos = aplanar_roboflow(datos, salida.parent / (salida.name + "_plano"))
    fotos = sorted(datos.glob("caja*_cara*.jpg"))
    if not fotos:
        raise SystemExit(f"No hay caja*_cara*.jpg en {datos}")
    cajas = sorted({fisica(re.match(r"(caja\d+)_", f.name).group(1)) for f in fotos})
    candidatas = [c for c in cajas if arducam_desde is None or int(c[4:]) >= arducam_desde]
    if not candidatas:
        raise SystemExit(f"No hay cajas con número >= {arducam_desde} (Arducam) en {datos}")
    random.Random(semilla).shuffle(candidatas)
    n_val = max(1, round(len(candidatas) * frac_val))
    val = set(candidatas[:n_val])
    excluir = {leer_mapa(mapa).get(c) for c in val} - {None} if mapa else set()
    if excluir:
        print(f"Fuera del entrenamiento por ser la misma caja física que una de validación: {sorted(excluir)}")
    for sub in ("train", "val"):
        for tipo in ("images", "labels"):
            (salida / tipo / sub).mkdir(parents=True, exist_ok=True)
    for f in fotos:
        if fisica(f.name.split("_")[0]) in excluir:
            continue
        if solo_arducam and arducam_desde is not None and int(fisica(f.name[:7])[4:]) < arducam_desde:
            continue  # experimento: entrenar solo con fotos de la Arducam
        sub = "val" if fisica(f.name.split("_")[0]) in val else "train"
        shutil.copy2(f, salida / "images" / sub / f.name)
        txt = f.with_suffix(".txt")
        if txt.exists():  # sin .txt = foto sin regiones (válido como negativo)
            shutil.copy2(txt, salida / "labels" / sub / txt.name)
    yaml = salida / "data.yaml"
    yaml.write_text(f"path: {salida.resolve()}\ntrain: images/train\nval: images/val\n"
                    f"names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASES)))
    extra = sobremuestrear(salida, sobremuestreo)
    if extra:
        print(f"Sobremuestreo x{sobremuestreo}: {extra} copias de fotos con lote/fecha en train")
    usadas = [c for c in cajas if c not in excluir and not (solo_arducam and arducam_desde is not None and int(c[4:]) < arducam_desde)]
    print(f"{len(usadas)} cajas: {len(usadas) - n_val} train / {n_val} val")
    return yaml


# Aumento de datos pensado para texto (nunca espejar). Los valores por defecto de YOLO quedan como base;
# aquí solo se cambia lo que ayuda con troquelado, brillo desigual y la cámara en movimiento.
AUMENTO = dict(
    fliplr=0.0, flipud=0.0,            # NO espejar: invertiría el texto
    degrees=5.0,                       # cajas ligeramente giradas
    perspective=0.0005,                # inclinación leve
    scale=0.4,                         # distancia distinta a la cámara
    translate=0.1,
    hsv_s=0.3, hsv_v=0.5,              # brillo/reflejos del blíster y la caja
    mosaic=1.0, close_mosaic=15,       # sin mosaico en las últimas épocas: se afina con fotos reales
    erasing=0.0,                       # no borrar trozos: podría tapar el lote
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", required=True, type=Path)
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--modelo", default="yolov8n.pt")
    ap.add_argument("--epocas", type=int, default=150, help="tope; el early stopping suele cortar antes")
    ap.add_argument("--paciencia", type=int, default=25,
                    help="early stopping: se detiene si la métrica de validación no mejora en N épocas (0 = desactivado)")
    ap.add_argument("--imgsz", type=int, default=960, help="texto pequeño (lote): no bajar de 640")
    ap.add_argument("--val", type=float, default=0.2)
    ap.add_argument("--semilla", type=int, default=0)
    ap.add_argument("--device", default=None, help="'cpu', '0' (GPU)... por defecto auto")
    ap.add_argument("--arducam-desde", type=int, default=None,
                    help="cajas con número >= N son de la Arducam y son las únicas que validan (p. ej. 101)")
    ap.add_argument("--mapa", type=Path, help="CSV caja_arducam,caja_celular de las cajas fotografiadas con ambas cámaras")
    ap.add_argument("--solo-arducam", action="store_true", help="con --arducam-desde: NO usa las fotos de celular para entrenar")
    ap.add_argument("--sobremuestreo", type=int, default=3,
                    help="veces que aparece en train cada foto con lote/fecha (1 = sin sobremuestreo)")
    ap.add_argument("--sin-aumento", action="store_true", help="usa los valores por defecto de YOLO (para comparar)")
    a = ap.parse_args()

    yaml = preparar(a.datos, a.salida, a.val, a.semilla, a.arducam_desde, a.mapa, a.solo_arducam, a.sobremuestreo)
    from ultralytics import YOLO

    modelo = YOLO(a.modelo)
    extra = dict(fliplr=0.0, flipud=0.0) if a.sin_aumento else AUMENTO
    modelo.train(data=str(yaml), epochs=a.epocas, imgsz=a.imgsz, device=a.device,
                 patience=a.paciencia or a.epocas + 1,   # early stopping nativo de ultralytics; guarda best.pt
                 seed=a.semilla, cache=True,
                 project=str((a.salida / "runs").resolve()), name="detector", exist_ok=True, **extra)
    # best.pt = la mejor época (no la última), así que el early stopping no deja un modelo peor.
    mejor = YOLO(str((a.salida / "runs" / "detector" / "weights" / "best.pt").resolve()))
    m = mejor.val(data=str(yaml), imgsz=a.imgsz, project=str((a.salida / "runs").resolve()), name="val", exist_ok=True)
    print("\nmAP50 por clase (lote y fecha son las que importan):")
    ap50 = dict(zip(m.box.ap_class_index.tolist(), m.box.ap50.tolist()))  # solo clases presentes en val
    for i, nombre in enumerate(CLASES):
        print(f"  {nombre:8s} mAP50={ap50.get(i, float('nan')):.3f}  mAP50-95={m.box.maps[i]:.3f}")
    print("ONNX:", mejor.export(format="onnx", imgsz=a.imgsz))


if __name__ == "__main__":
    main()
