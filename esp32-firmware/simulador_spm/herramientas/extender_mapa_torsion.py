"""Amplía datos/mapa_colision.npz con más torsiones (misma malla de inclinación/azimut).  Uso:  python herramientas/extender_mapa_torsion.py [-90 -75 ... 90]
Por defecto añade ±30, ±45, ±60, ±75, ±90° (~5 min por torsión)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spm import SPM
from spm.modelo3d import ModeloCAD
from spm import colisiones

nuevas = [float(x) for x in sys.argv[1:]] or [-90, -75, -60, -45, -30, 30, 45, 60, 75, 90]
spm = SPM(); modelo = ModeloCAD(spm); base = colisiones.MapaColision.cargar()
nuevas = [t for t in nuevas if all(abs(t - u) > 1e-9 for u in base.tors)]
t0 = time.time(); extra = colisiones.calcular(spm, modelo, tilts=base.tilts, azs=base.azs, tors=nuevas)
colisiones.fusionar(base, extra).guardar()
print("FIN en %.0f s; torsiones del mapa:" % (time.time() - t0), colisiones.MapaColision.cargar().tors)
