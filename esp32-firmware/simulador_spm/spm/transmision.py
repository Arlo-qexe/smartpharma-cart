"""Relación de engranes entre el eje del motor y el engrane NNR de cada pata, y límites de los motores.

Los valores por defecto son SUPUESTOS que hay que confirmar con el hardware real (ver README):
  - piñón de 17 dientes sobre NNR de 37 dientes (exterior-exterior => sentido contrario, signo -1)
  - límites del JGA25-370: marcadores de posición, no datos medidos
"""
import json
import math
from dataclasses import dataclass, asdict, field
from pathlib import Path
import numpy as np

from .geometria import RUTA_DATOS


@dataclass
class Transmision:
    z_central: int = 37
    z_pinon: int = 17
    signo: int = -1                         # engranes exteriores: el motor gira al revés que el NNR (valor común a las 3 patas)
    signos: tuple = None                    # signo POR PATA (motor 1, 2, 3); si es None se usa `signo` en las tres. Ej.: (1, -1, -1) invierte solo el motor 1
    offset_motor_rad: tuple = (0.0, 0.0, 0.0)   # ángulo del motor en la pose de ensamblaje (para el cero del encoder)
    cuentas_por_vuelta_salida: float = 1000.0   # resolución del encoder en el eje de salida del motor (SUPUESTO)

    @property
    def relacion(self):
        return self.z_central / self.z_pinon

    @property
    def signos_pata(self):
        """Signo de cada pata como arreglo (3,): `signos` si está definido, si no `signo` repetido."""
        if self.signos is None:
            return np.full(3, float(self.signo))
        s = np.asarray(self.signos, float)
        if s.shape != (3,) or not np.all(np.abs(s) == 1):
            raise ValueError("signos debe tener 3 valores, cada uno +1 o -1 (recibido %r)" % (self.signos,))
        return s

    def a_motor(self, theta_nnr):
        """Ángulo(s) del motor (rad, eje de salida) para el/los ángulo(s) del NNR."""
        return self.signos_pata * self.relacion * np.asarray(theta_nnr, float) + np.asarray(self.offset_motor_rad)

    def a_nnr(self, motor):
        return (np.asarray(motor, float) - np.asarray(self.offset_motor_rad)) / (self.signos_pata * self.relacion)

    def cuantizar(self, motor):
        """Lectura de encoder: el ángulo del motor redondeado a la resolución del encoder."""
        paso = 2 * math.pi / self.cuentas_por_vuelta_salida
        return np.round(np.asarray(motor, float) / paso) * paso


@dataclass
class Limites:
    inclinacion_max_deg: float = 28.0        # sin mapa de colisiones: cono de respaldo. Medido en el CAD con el plato horizontal de partida: libre hasta ~30°, colisión desde ~32°
    vel_motor_max: float = 2 * math.pi * 60 / 60   # rad/s en el eje de salida (60 rpm, SUPUESTO)
    acc_motor_max: float = 30.0              # rad/s^2 (SUPUESTO)
    sing_min: float = 0.15                   # sigma_min(A) mínimo aceptado (margen a singularidad paralela)
    sing_serie_min: float = 0.10             # min|B| mínimo aceptado (margen a singularidad serie)
    holgura_min_mm: float = 1.5              # holgura mínima entre cuerpos según el mapa de colisiones del CAD (error del muestreo ~0.5 mm)
    motor_min_deg: tuple = (None, None, None)   # topes MEDIDOS de cada motor (°, eje de salida, contacto mecánico); None = sin tope conocido en ese lado
    motor_max_deg: tuple = (None, None, None)
    motor_margen_deg: float = 5.0            # se exige quedar al menos esto antes del tope medido
    mapa: object = field(default=None, repr=False, compare=False)   # MapaColision (datos/mapa_colision.npz); si falta se usa el cono de inclinación_max_deg

    def valida_motor(self, motor_rad):
        """(ok, motivo) para los 3 ángulos de motor (rad) contra los topes medidos (con margen). Sin topes definidos siempre es ok."""
        m = np.degrees(np.asarray(motor_rad, float))
        for i in range(3):
            lo, hi = self.motor_min_deg[i], self.motor_max_deg[i]
            if lo is not None and m[i] < lo + self.motor_margen_deg:
                return False, "motor %d en %.1f° < tope %.1f° (+%.1f° de margen)" % (i + 1, m[i], lo, self.motor_margen_deg)
            if hi is not None and m[i] > hi - self.motor_margen_deg:
                return False, "motor %d en %.1f° > tope %.1f° (-%.1f° de margen)" % (i + 1, m[i], hi, self.motor_margen_deg)
        return True, ""

    def valida(self, spm, R, theta):
        """(ok, motivo)"""
        from .rot import a_inclinacion
        tilt, az, tor = (math.degrees(x) for x in a_inclinacion(R))
        if self.mapa is not None:
            h, par = self.mapa.consultar(tilt, az, tor)
            if math.isnan(h):
                return False, "fuera del rango del mapa de colisiones (inclinación %.1f°, torsión %.1f°)" % (tilt, tor)
            if h < self.holgura_min_mm:
                return False, "colisión %s-%s (holgura %.1f mm < %.1f mm)" % (par[0], par[1], h, self.holgura_min_mm)
        elif tilt > self.inclinacion_max_deg:
            return False, "inclinación %.1f° > %.1f° (colisión Brazov-NNR en el CAD)" % (tilt, self.inclinacion_max_deg)
        smin, bmin = spm.indice_singularidad(R, theta)
        if smin < self.sing_min or bmin < self.sing_serie_min:
            return False, "cerca de una singularidad (sigma=%.3f, B=%.3f)" % (smin, bmin)
        return True, ""


def cargar_config(ruta=None):
    """(Transmision, Limites) desde datos/config.json si existe; si no, valores por defecto."""
    from .colisiones import MapaColision
    ruta = Path(ruta) if ruta else RUTA_DATOS / "config.json"
    t, l = {}, {}
    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        t = d.get("transmision", {}); l = d.get("limites", {})
        if "offset_motor_rad" in t:
            t["offset_motor_rad"] = tuple(t["offset_motor_rad"])
        if t.get("signos") is not None:
            t["signos"] = tuple(int(x) for x in t["signos"])
    for k in ("motor_min_deg", "motor_max_deg"):
        if k in l:
            l[k] = tuple(None if x is None else float(x) for x in l[k])
    lim = Limites(**l)
    lim.mapa = MapaColision.cargar()
    return Transmision(**t), lim


def guardar_config(trans, lim, ruta=None):
    ruta = Path(ruta) if ruta else RUTA_DATOS / "config.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"transmision": asdict(trans), "limites": {k: v for k, v in asdict(lim).items() if k != "mapa"}}, f, indent=1)
