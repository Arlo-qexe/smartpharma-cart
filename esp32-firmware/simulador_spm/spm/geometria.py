"""Carga de la geometría cinemática extraída del CAD."""
import json
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

RUTA_DATOS = Path(__file__).resolve().parent.parent / "datos"


@dataclass
class Geometria:
    O: np.ndarray                      # punto de concurrencia de los ejes (mm, marco del ensamblaje)
    a1: np.ndarray                     # (3,3) eje Brasop-Brazov de cada pata, fila i = pata del engrane NNR(i+1)
    a2: np.ndarray                     # (3,3) eje Brazov-platosupv de cada pata (fijo en la plataforma)
    nnr: list = field(default_factory=lambda: [1, 2, 3])
    piezas: list = field(default_factory=list)   # nombres de las piezas CAD de cada pata (informativo)
    disco_normal: np.ndarray = None              # normal del disco de la plataforma en la pose de ensamblaje del CAD

    @classmethod
    def cargar(cls, ruta=None):
        ruta = Path(ruta) if ruta else RUTA_DATOS / "geometria.json"
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        patas = sorted(d["patas"], key=lambda p: p["nnr"])
        a1 = np.array([p["a1"] for p in patas], float)
        a2 = np.array([p["a2"] for p in patas], float)
        a1 /= np.linalg.norm(a1, axis=1, keepdims=True)
        a2 /= np.linalg.norm(a2, axis=1, keepdims=True)
        return cls(np.array(d["origen_O"], float), a1, a2, [p["nnr"] for p in patas],
                   [{k: p[k] for k in ("brasop", "brazov", "plato")} for p in patas], np.array(d["disco_normal_ensamblaje"], float) if "disco_normal_ensamblaje" in d else None)

    def R_horizontal(self):
        """Rotación mínima (alrededor de O) que lleva el disco de la plataforma de la pose de ensamblaje a la horizontal."""
        from .rot import rot_axis
        n, z = self.disco_normal / np.linalg.norm(self.disco_normal), np.array([0.0, 0.0, 1.0])
        eje = np.cross(n, z); s = np.linalg.norm(eje)
        return np.eye(3) if s < 1e-12 else rot_axis(eje, float(np.arctan2(s, n @ z)))
