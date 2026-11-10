"""Control cinemático: lazo de seguimiento con motores simulados.

Esquema (cinemático, sin dinámica de la plataforma):
    trayectoria de orientación --IK--> consigna de cada motor --(avance de velocidad + P)--> motor --encoder--> FK --> orientación estimada

Para usar hardware real basta implementar InterfazMotores (escribir consigna / leer encoders) y pasarla en lugar de MotoresSimulados.
Nada de esto se ha probado con hardware.
"""
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
import numpy as np

from .rot import angulo_entre


class InterfazMotores(ABC):
    """Contrato mínimo con los motores (reales o simulados). Ángulos en rad en el eje de salida."""
    @abstractmethod
    def comandar_velocidad(self, vel, dt):
        """Aplica velocidades (3,) rad/s durante dt segundos."""

    @abstractmethod
    def leer_encoders(self):
        """Ángulos medidos (3,) rad."""


class MotoresSimulados(InterfazMotores):
    """Servo de velocidad de primer orden con saturación, lectura de encoder cuantizada y desvío constante opcional."""
    def __init__(self, trans, lim, pos0=None, tau=0.05, perturbacion_vel=(0.0, 0.0, 0.0), seed=None):
        self.trans, self.lim, self.tau = trans, lim, tau
        self.pos = np.zeros(3) if pos0 is None else np.array(pos0, float)
        self.vel = np.zeros(3)
        self.pert = np.asarray(perturbacion_vel, float)
        self.rng = np.random.default_rng(seed)

    def comandar_velocidad(self, vel, dt):
        v = np.clip(vel, -self.lim.vel_motor_max, self.lim.vel_motor_max)
        self.vel += (v - self.vel) * (1 - math.exp(-dt / self.tau)) if self.tau > 0 else (v - self.vel)
        self.pos += (self.vel + self.pert) * dt

    def leer_encoders(self):
        return self.trans.cuantizar(self.pos)


@dataclass
class Registro:
    t: np.ndarray
    motor_ref: np.ndarray
    motor_med: np.ndarray
    R_ref: np.ndarray
    R_est: np.ndarray
    error_deg: np.ndarray        # error de orientación de la plataforma (estimada con FK vs. deseada)


def seguir_trayectoria(spm, trans, lim, tray, motores=None, kp=15.0, ff=True):
    """Simula el seguimiento de 'tray'. Devuelve Registro.

    kp: ganancia proporcional (1/s) sobre el error de ángulo del motor; ff: usa la velocidad de la trayectoria como avance.
    """
    motores = motores or MotoresSimulados(trans, lim, pos0=tray.motor[0])
    dt = tray.dt
    med = np.zeros_like(tray.motor); Rest = np.zeros_like(tray.R); err = np.zeros(len(tray.t))
    R_prev = tray.R[0]
    for k in range(len(tray.t)):
        q = motores.leer_encoders()
        med[k] = q
        Rest[k] = R_prev = spm.fk(trans.a_nnr(q), R_prev)           # arranca de la última estimación: sigue la rama de montaje
        err[k] = math.degrees(angulo_entre(tray.R[k], Rest[k]))
        cmd = kp * (tray.motor[k] - q) + (tray.motor_vel[k] if ff else 0.0)
        motores.comandar_velocidad(cmd, dt)
    return Registro(tray.t, tray.motor, med, tray.R, Rest, err)
