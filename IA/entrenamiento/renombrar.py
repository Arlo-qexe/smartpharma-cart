"""Renombra fotos del celular a cajaNNN_caraK.jpg y reduce el lado largo a 1920 px.

Toma las fotos ordenadas por nombre (fecha/hora) en grupos de 5 = una caja.
Requisito: tomar SIEMPRE 5 fotos por caja, en orden de caras; borrar las repetidas/falladas antes.

    python entrenamiento/renombrar.py --entrada dataset_ia/celular --salida dataset_ia/crudo [--inicio 1]
"""
import argparse
from pathlib import Path

from PIL import Image, ImageOps

ap = argparse.ArgumentParser()
ap.add_argument("--entrada", required=True, type=Path)
ap.add_argument("--salida", required=True, type=Path)
ap.add_argument("--inicio", type=int, default=1, help="número de la primera caja")
a = ap.parse_args()

fotos = sorted(p for p in a.entrada.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
if len(fotos) % 5:
    raise SystemExit(f"{len(fotos)} fotos: no es múltiplo de 5. Revisa cuál sobra o falta.")
a.salida.mkdir(parents=True, exist_ok=True)
for i, f in enumerate(fotos):
    caja, cara = a.inicio + i // 5, i % 5 + 1
    im = ImageOps.exif_transpose(Image.open(f)).convert("RGB")  # respeta la rotación del celular
    im.thumbnail((1920, 1920))  # conserva proporción, no amplía
    im.save(a.salida / f"caja{caja:03d}_cara{cara}.jpg", quality=90)
print(f"{len(fotos) // 5} cajas escritas en {a.salida}")
