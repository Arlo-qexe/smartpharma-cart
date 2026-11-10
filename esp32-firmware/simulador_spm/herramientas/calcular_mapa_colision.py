"""Calcula datos/mapa_colision.npz con el modelo 3D (varios minutos).  Uso:  python herramientas/calcular_mapa_colision.py"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spm import SPM
from spm.modelo3d import ModeloCAD
from spm import colisiones

spm = SPM(); modelo = ModeloCAD(spm)
t = time.time(); mapa = colisiones.calcular(spm, modelo); mapa.guardar()
print("FIN en %.0f s -> %s" % (time.time() - t, colisiones.RUTA_MAPA))
