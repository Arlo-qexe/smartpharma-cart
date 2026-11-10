"""Mapa de colisiones calculado con el modelo 3D del CAD.

Para una malla de poses (inclinación, azimut, torsión) se calcula, con nubes de puntos de la superficie de cada cuerpo, la distancia mínima
entre todos los pares de cuerpos que NO están en contacto de diseño (pares con distancia < 3 mm en la pose de ensamblaje se ignoran: son
uniones, rodamientos y engranes concéntricos). La precisión es la del muestreo (~0.3-0.5 mm), por eso se usa un margen.
El resultado se guarda en datos/mapa_colision.npz y Limites lo usa como zona prohibida.
"""
import itertools
import math
import time
from pathlib import Path
import numpy as np

from .geometria import RUTA_DATOS
from .rot import desde_inclinacion, a_inclinacion
from .cinematica import FueraDeEspacioDeTrabajo

RUTA_MAPA = RUTA_DATOS / "mapa_colision.npz"
CUERPOS = ["fijo", "plataforma", "nnr1", "nnr2", "nnr3", "brazov1", "brazov2", "brazov3"]


def _mind(a, b):
    """Distancia mínima entre dos nubes de puntos (descomposición |a|²+|b|²-2ab, por bloques)."""
    best = np.inf
    bb = (b * b).sum(1)
    for s in range(0, len(a), 1000):
        blk = a[s:s + 1000]
        d2 = (blk * blk).sum(1)[:, None] + bb[None, :] - 2 * blk @ b.T
        best = min(best, d2.min())
    return math.sqrt(max(best, 0.0))


def _submuestra(P, n, rng):
    return P if len(P) <= n else P[rng.choice(len(P), n, replace=False)]


class MapaColision:
    def __init__(self, tilts, azs, tors, holgura, par, pares):
        self.tilts, self.azs, self.tors = np.asarray(tilts), np.asarray(azs), np.asarray(tors)
        self.holgura, self.par, self.pares = holgura, par, pares          # holgura[t, i, j] en mm (NaN = sin solución IK)

    def guardar(self, ruta=RUTA_MAPA):
        np.savez_compressed(ruta, tilts=self.tilts, azs=self.azs, tors=self.tors, holgura=self.holgura, par=self.par,
                            pares=np.array(["%s|%s" % p for p in self.pares]))

    @classmethod
    def cargar(cls, ruta=RUTA_MAPA):
        ruta = Path(ruta)
        if not ruta.exists():
            return None
        z = np.load(ruta)
        return cls(z["tilts"], z["azs"], z["tors"], z["holgura"], z["par"], [tuple(s.split("|")) for s in z["pares"]])

    @staticmethod
    def _vecinos(rejilla, x):
        """Índices de las dos celdas que rodean a x (rejilla uniforme). Con x exactamente sobre un nodo se incluye también el siguiente: evaluación
        conservativa y determinista (sin depender del ruido de punto flotante)."""
        paso = rejilla[1] - rejilla[0]
        i = int(math.floor((x - rejilla[0]) / paso + 1e-6))
        return sorted({min(max(i, 0), len(rejilla) - 1), min(max(i + 1, 0), len(rejilla) - 1)})

    def consultar(self, tilt_deg, az_deg, tors_deg):
        """(holgura_mm, par) CONSERVADOR: mínimo de las celdas de la malla que rodean la pose. NaN fuera del rango calculado."""
        if tilt_deg > self.tilts[-1] + 1e-6:
            return float("nan"), None
        extra = 0.0
        if tors_deg < self.tors[0] - 1e-6 or tors_deg > self.tors[-1] + 1e-6:
            # fuera del rango calculado: el mecanismo tiene simetría de 120° ((incl, az, tors) ~ (incl, az+120, tors+120)); se pliega la torsión al rango y se
            # resta un margen que crece con la inclinación (la simetría no es exacta: ~1 mm a 10°, ~2 mm a 20°, ~4 mm a 26°). Por encima de 26° no se extrapola.
            if tilt_deg > 26.0:
                return float("nan"), None
            k = math.ceil((tors_deg - self.tors[-1]) / 120.0) if tors_deg > self.tors[-1] else -math.ceil((self.tors[0] - tors_deg) / 120.0)
            tors_deg -= 120.0 * k; az_deg -= 120.0 * k; extra = 1.0 + 0.2 * tilt_deg
        its = self._vecinos(self.tilts, tilt_deg)
        paso_az = self.azs[1] - self.azs[0]; ja = (az_deg % 360.0) / paso_az + 1e-6
        jas = {int(math.floor(ja)) % len(self.azs), (int(math.floor(ja)) + 1) % len(self.azs)}
        iks = self._vecinos(self.tors, float(np.clip(tors_deg, self.tors[0], self.tors[-1])))
        celdas = [(k, i, j) for k in iks for i in its for j in jas]
        vals = [self.holgura[c] for c in celdas]
        if any(np.isnan(v) for v in vals):
            return float("nan"), None
        k = int(np.argmin(vals))
        return float(vals[k]) - extra, self.pares[int(self.par[celdas[k]])]


def calcular(spm, modelo, tilts=None, azs=None, tors=None, n_puntos=2200, ignorar_mm=3.0, verbose=True):
    tilts = np.arange(0, 41, 2.0) if tilts is None else np.asarray(tilts, float)
    azs = np.arange(0, 360, 15.0) if azs is None else np.asarray(azs, float)
    tors = np.array([-15.0, 0.0, 15.0]) if tors is None else np.asarray(tors, float)
    rng = np.random.default_rng(0)
    nube0 = {c: _submuestra(P, n_puntos, rng) for c, P in modelo.puntos.items()}
    # índices en la nube submuestreada: se transforma la nube ya reducida con las mismas rotaciones
    def nubes(R, th):
        rot = modelo.rotaciones(R, th)
        return {c: (P - modelo.O) @ rot[c].T + modelo.O for c, P in nube0.items()}
    base = nubes(np.eye(3), np.zeros(3))
    todos = list(itertools.combinations(CUERPOS, 2))
    pares = [p for p in todos if _mind(base[p[0]], base[p[1]]) >= ignorar_mm]
    if verbose:
        print("pares vigilados (%d de %d): %s" % (len(pares), len(todos), ", ".join("%s-%s" % p for p in pares)), flush=True)
    H = np.full((len(tors), len(tilts), len(azs)), np.nan); PAR = np.zeros(H.shape, dtype=int)
    t0 = time.time(); n = 0
    for k, tr in enumerate(tors):
        for i, tl in enumerate(tilts):
            prev = np.zeros(3)
            for j, az in enumerate(azs):
                R = desde_inclinacion(*np.radians([tl, az, tr]))
                try:
                    th = spm.ik(R, prev); prev = th
                except FueraDeEspacioDeTrabajo:
                    continue
                nb = nubes(R, th)
                d = [_mind(nb[a], nb[b]) if _cajas_cerca(nb[a], nb[b], 8.0) else 99.0 for a, b in pares]
                m = int(np.argmin(d)); H[k, i, j] = d[m]; PAR[k, i, j] = m
                n += 1
        if verbose:
            print("torsión %+.0f°: listo (%d poses, %.0f s)" % (tr, n, time.time() - t0), flush=True)
    return MapaColision(tilts, azs, tors, H, PAR, pares)


def fusionar(m1, m2):
    """Une dos mapas con la misma malla de inclinación/azimut y distintas torsiones (p. ej. para ampliar el rango de torsión sin recalcular)."""
    if not (np.array_equal(m1.tilts, m2.tilts) and np.array_equal(m1.azs, m2.azs) and m1.pares == m2.pares):
        raise ValueError("los mapas no comparten malla de inclinación/azimut/pares")
    tors = np.concatenate([m1.tors, m2.tors]); o = np.argsort(tors); tors = tors[o]
    keep = np.concatenate([[True], np.diff(tors) > 1e-9])
    H = np.concatenate([m1.holgura, m2.holgura])[o][keep]; P = np.concatenate([m1.par, m2.par])[o][keep]
    return MapaColision(m1.tilts, m1.azs, tors[keep], H, P, m1.pares)


def _cajas_cerca(a, b, margen):
    return bool(np.all(a.min(0) - margen <= b.max(0)) and np.all(b.min(0) - margen <= a.max(0)))
