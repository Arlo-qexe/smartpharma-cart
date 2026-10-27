"""Preetiqueta con el detector (best.pt) las fotos de una sesión y las deja listas para subir a Roboflow
(imágenes + .txt YOLO + data.yaml). Solo guarda nombre y gramaje; lote y fecha se dibujan a mano.
Rutas fijas dentro del script (sesion03, yolo_v2): edítalas antes de usarlo con otra sesión. Ejecutar desde la raíz de ia/.
"""
import shutil, sys
from pathlib import Path
from ultralytics import YOLO
raiz = Path("dataset_ia/arducam/sesion03"); out = Path("dataset_ia/arducam/roboflow_sesion03_subir")
out.mkdir(exist_ok=True)
m = YOLO("dataset_ia/yolo_v2/runs/detector/weights/best.pt")
print(m.names)
NOMBRES = ["fecha", "gramaje", "lote", "nombre"]   # mismo orden alfabético que el export de Roboflow
inv = {v: i for i, v in enumerate(NOMBRES)}
cont = {"nombre": 0, "gramaje": 0}; vacias = []
for foto in sorted(raiz.glob("caja3??_cara?.jpg")):
    r = m.predict(str(foto), imgsz=960, conf=0.25, device=0, verbose=False)[0]
    lineas = []
    for b in r.boxes:
        nom = m.names[int(b.cls)]
        if nom not in cont: continue
        x, y, w, h = b.xywhn[0].tolist()
        lineas.append(f"{inv[nom]} {x:.6f} {y:.6f} {w:.6f} {h:.6f}"); cont[nom] += 1
    shutil.copy(foto, out / foto.name)
    (out / foto.with_suffix(".txt").name).write_text("\n".join(lineas) + ("\n" if lineas else ""))
    if not lineas: vacias.append(foto.name)
(out / "data.yaml").write_text("names: " + str(NOMBRES) + "\nnc: 4\n")
print(cont, "fotos sin ninguna etiqueta:", len(vacias))
