"""Planificación de trayectorias de orientación y conversión a ángulos de motor.

Un 'camino' es una función s in [0,1] -> R (geometría sin tiempo). El perfil temporal (quíntico o trapezoidal) lo recorre en T segundos y la
cinemática inversa da los ángulos de los motores en cada instante. Si se piden límites de motor, el tiempo se alarga lo necesario
(la geometría del camino no cambia: velocidades escalan 1/k y aceleraciones 1/k^2).
"""
import math
from dataclasses import dataclass
import numpy as np

from .cinematica import FueraDeEspacioDeTrabajo
from .rot import (desde_inclinacion, a_inclinacion, quat_de_R, R_de_quat, slerp, angulo_entre, rotz, log_so3)


# ------------------------------------------------------------------ perfiles temporales tau in [0,1] -> s in [0,1]
def quintico(tau):
    tau = np.clip(tau, 0, 1)
    return 10 * tau**3 - 15 * tau**4 + 6 * tau**5


def trapezoidal(tau, frac=0.25):
    """Aceleración constante durante 'frac' de T, velocidad constante, desaceleración constante."""
    tau = np.clip(tau, 0, 1)
    v = 1 / (1 - frac)
    s = np.where(tau < frac, 0.5 * v / frac * tau**2,
        np.where(tau <= 1 - frac, 0.5 * v * frac + v * (tau - frac),
                 1 - 0.5 * v / frac * (1 - tau)**2))
    return s


PERFILES = {"quintico": quintico, "trapezoidal": trapezoidal}


# ------------------------------------------------------------------ caminos
class CaminoSlerp:
    """Pasa por las orientaciones dadas con SLERP por tramos, parametrizado por distancia angular (velocidad angular uniforme)."""
    def __init__(self, orientaciones):
        self.R = [np.asarray(R, float) for R in orientaciones]
        self.q = [quat_de_R(R) for R in self.R]
        d = [angulo_entre(self.R[k], self.R[k + 1]) for k in range(len(self.R) - 1)]
        self.cum = np.concatenate([[0], np.cumsum(d)])
        self.longitud = float(self.cum[-1])

    def __call__(self, s):
        x = float(np.clip(s, 0, 1)) * self.longitud
        k = int(min(np.searchsorted(self.cum, x, side="right") - 1, len(self.q) - 2))
        seg = self.cum[k + 1] - self.cum[k]
        return R_de_quat(slerp(self.q[k], self.q[k + 1], 0.0 if seg < 1e-12 else (x - self.cum[k]) / seg))


class CaminoPrecesion:
    """Inclinación fija que da 'vueltas' alrededor del eje vertical (la normal de la plataforma dibuja un cono)."""
    def __init__(self, tilt_deg, vueltas=1.0, az0_deg=0.0, torsion_deg=0.0):
        self.t, self.v, self.a0, self.tor = math.radians(tilt_deg), vueltas, math.radians(az0_deg), math.radians(torsion_deg)

    def __call__(self, s):
        return desde_inclinacion(self.t, self.a0 + 2 * math.pi * self.v * s, self.tor)


class CaminoInclinacion:
    """Interpola linealmente (tilt, az, torsion) entre dos poses; útil para barridos de torsión o de inclinación en un azimut fijo."""
    def __init__(self, p0_deg, p1_deg):
        self.p0, self.p1 = np.radians(p0_deg), np.radians(p1_deg)

    def __call__(self, s):
        return desde_inclinacion(*(self.p0 + (self.p1 - self.p0) * float(s)))


# ------------------------------------------------------------------ trayectoria muestreada
@dataclass
class Trayectoria:
    t: np.ndarray               # (N,)
    R: np.ndarray               # (N,3,3) orientación de la plataforma
    theta: np.ndarray           # (N,3) ángulos de los engranes NNR (rad)
    motor: np.ndarray           # (N,3) ángulos de los motores (rad, eje de salida)
    motor_vel: np.ndarray       # (N,3) rad/s
    motor_acc: np.ndarray       # (N,3) rad/s^2
    inclinacion: np.ndarray     # (N,) rad
    azimut: np.ndarray          # (N,) rad
    torsion: np.ndarray         # (N,) rad
    duracion: float
    factor_tiempo: float        # >1 si hubo que alargar la duración para respetar los límites
    violaciones: list           # [(indice, motivo)] muestras fuera de límites de espacio de trabajo

    @property
    def dt(self):
        return float(self.t[1] - self.t[0])


def _muestrear(spm, trans, camino, T, dt, perfil, theta0=None):
    n = max(2, int(round(T / dt)) + 1)
    t = np.linspace(0, T, n)
    s = PERFILES[perfil](t / T)
    R = np.array([camino(x) for x in s])
    theta = np.zeros((n, 3)); prev = np.zeros(3) if theta0 is None else np.asarray(theta0, float)
    for k in range(n):
        try:
            theta[k] = spm.ik(R[k], prev)
        except FueraDeEspacioDeTrabajo as e:
            tilt, az, _ = a_inclinacion(R[k])
            raise FueraDeEspacioDeTrabajo("t=%.2f s (inclinación %.1f°, azimut %.0f°): %s" % (t[k], math.degrees(tilt), math.degrees(az), e))
        prev = theta[k]
    return t, R, theta                                        # ik() ya sigue a la muestra anterior: sin saltos de 2*pi, también en varias vueltas


def planificar(spm, trans, lim, camino, T=3.0, dt=0.01, perfil="quintico", ajustar_tiempo=True, estricto=False, theta0=None):
    """Trayectoria de orientación -> ángulos de motor, respetando límites de velocidad/aceleración del motor (alargando T si hace falta).

    estricto=True lanza excepción si alguna muestra cae fuera del espacio de trabajo permitido (inclinación máxima, singularidades);
    si no, esas muestras se devuelven en 'violaciones'.
    """
    k = 1.0
    for _ in range(4):
        t, R, theta = _muestrear(spm, trans, camino, T * k, dt, perfil, theta0)
        motor = trans.a_motor(theta)
        vel = np.gradient(motor, t, axis=0)
        acc = np.gradient(vel, t, axis=0)
        if not (ajustar_tiempo and lim):
            break
        kv = np.abs(vel).max() / lim.vel_motor_max
        ka = math.sqrt(np.abs(acc).max() / lim.acc_motor_max)
        extra = max(kv, ka)
        if extra <= 1.0:
            break
        k *= extra * 1.02
    viol = []
    if lim:
        for i in range(len(t)):
            ok, why = lim.valida(spm, R[i], theta[i])
            if ok:
                ok, why = lim.valida_motor(motor[i])
            if not ok:
                viol.append((i, why))
        if viol and estricto:
            raise FueraDeEspacioDeTrabajo("t=%.2f s: %s (+%d muestras más)" % (t[viol[0][0]], viol[0][1], len(viol) - 1))
    ang = np.array([a_inclinacion(r) for r in R])
    return Trayectoria(t, R, theta, motor, vel, acc, ang[:, 0], ang[:, 1], np.unwrap(ang[:, 2]), float(t[-1]), k, viol)


def planificar_con_paradas(spm, trans, lim, orientaciones, T_tramo=2.0, dt=0.01, perfil="quintico", **kw):
    """Va de orientación en orientación deteniéndose en cada una (velocidad cero en los puntos de paso)."""
    partes, th0 = [], None
    for i, (a, b) in enumerate(zip(orientaciones[:-1], orientaciones[1:])):
        T = T_tramo[i] if np.ndim(T_tramo) else T_tramo                  # T_tramo puede ser un número o una lista (un tiempo por tramo)
        partes.append(planificar(spm, trans, lim, CaminoSlerp([a, b]), T, dt, perfil, theta0=th0, **kw))
        th0 = partes[-1].theta[-1]                                       # el siguiente tramo continúa desde donde terminó este (sin saltos de vuelta)
    return unir(partes)


def unir(partes):
    """Concatena trayectorias (la primera muestra de cada una se descarta salvo la de la primera)."""
    cat = lambda name: np.concatenate([getattr(partes[0], name)] + [getattr(p, name)[1:] for p in partes[1:]])
    t = partes[0].t.copy(); off = t[-1]
    for p in partes[1:]:
        t = np.concatenate([t, p.t[1:] + off]); off = t[-1]
    viol = []
    base = 0
    for p in partes:
        viol += [(i + base, m) for i, m in p.violaciones]
        base += len(p.t) - 1
    return Trayectoria(t, cat("R"), cat("theta"), cat("motor"), cat("motor_vel"), cat("motor_acc"), cat("inclinacion"), cat("azimut"),
                       cat("torsion"), float(t[-1]), max(p.factor_tiempo for p in partes), viol)


# ------------------------------------------------------------------ planificadores adicionales
def _construir(spm, trans, lim, t, theta, R=None, R0=None):
    """Trayectoria completa a partir de theta(t) (rad, NNR). Si no se da R, se obtiene con FK continua desde R0."""
    t = np.asarray(t, float); theta = np.asarray(theta, float)
    if R is None:
        R = np.zeros((len(t), 3, 3)); prev = np.eye(3) if R0 is None else R0
        for k in range(len(t)):
            R[k] = prev = spm.fk(theta[k], prev)
    motor = trans.a_motor(theta)
    vel = np.gradient(motor, t, axis=0); acc = np.gradient(vel, t, axis=0)
    viol = []
    if lim:
        for i in range(len(t)):
            ok, why = lim.valida(spm, R[i], theta[i])
            if ok:
                ok, why = lim.valida_motor(motor[i])
            if not ok:
                viol.append((i, why))
    ang = np.array([a_inclinacion(r) for r in R])
    return Trayectoria(t, np.asarray(R), theta, motor, vel, acc, ang[:, 0], ang[:, 1], np.unwrap(ang[:, 2]), float(t[-1]), 1.0, viol)


def planificar_articular(spm, trans, lim, orientaciones, T_tramo=2.0, dt=0.01, perfil="quintico", ajustar_tiempo=True):
    """Interpolación en el espacio de los motores: IK en cada punto de paso y perfil temporal entre ellos (con paradas).
    La plataforma NO sigue una geodésica: el camino de orientación sale curvo, como la 'interpolación articular' del brazo 3DOF."""
    th_w, prev = [], np.zeros(3)
    for k, R in enumerate(orientaciones):
        try:
            th = spm.ik(R, prev)
        except FueraDeEspacioDeTrabajo as e:
            raise FueraDeEspacioDeTrabajo("punto de paso %d: %s" % (k + 1, e))
        th_w.append(th); prev = th
    partes, R_prev = [], spm.fk(th_w[0])
    for i, (a, b) in enumerate(zip(th_w[:-1], th_w[1:])):
        Tn = T_tramo[i] if np.ndim(T_tramo) else T_tramo
        T = Tn
        for _ in range(4):
            n = max(2, int(round(T / dt)) + 1); t = np.linspace(0, T, n)
            th = a + (b - a) * PERFILES[perfil](t / T)[:, None]
            if not (ajustar_tiempo and lim):
                break
            mv = trans.a_motor(th); v = np.gradient(mv, t, axis=0); ac = np.gradient(v, t, axis=0)
            extra = max(np.abs(v).max() / lim.vel_motor_max, math.sqrt(np.abs(ac).max() / lim.acc_motor_max))
            if extra <= 1.0:
                break
            T *= extra * 1.02
        tr = _construir(spm, trans, lim, t, th, R0=R_prev)
        tr.factor_tiempo = T / Tn; R_prev = tr.R[-1]
        partes.append(tr)
    return unir(partes)


def planificar_jacobiano(spm, trans, lim, camino, T=3.0, dt=0.01, perfil="quintico", K=10.0, lam=0.05):
    """Control cinemático por velocidades (como el 'control cinemático (Jacobiano)' del brazo 3DOF):
    en cada paso omega = omega_ref + K * e_R,  theta_dot = -B^-1 A omega  (con amortiguamiento cerca de singularidades), integrado en el tiempo.
    La trayectoria devuelta es la REAL (con el error de seguimiento), no la de referencia."""
    n = max(2, int(round(T / dt)) + 1); t = np.linspace(0, T, n)
    Rref = [camino(x) for x in PERFILES[perfil](t / T)]
    theta = np.zeros((n, 3)); Rr = np.zeros((n, 3, 3))
    th = spm.ik(Rref[0]); R = Rref[0].copy()
    for k in range(n):
        theta[k], Rr[k] = th, R
        if k == n - 1:
            break
        w_ref = log_so3(Rref[k + 1] @ Rref[k].T) / dt
        e = log_so3(Rref[k] @ R.T)
        A, B = spm.jacobianos(R, th)
        Bs = np.where(np.abs(B) < lam, np.where(B >= 0, lam, -lam), B)        # evita dividir por ~0 en una singularidad serie
        thd = -(A @ (w_ref + K * e)) / Bs
        th = th + thd * dt
        try:
            R = spm.fk(th, R)
        except FueraDeEspacioDeTrabajo as ex:
            raise FueraDeEspacioDeTrabajo("t=%.2f s: el control cinemático llegó a una configuración sin solución (%s)" % (t[k], ex))
    return _construir(spm, trans, lim, t, theta, R=Rr)


def a_csv(tray, archivo):
    """Exporta t, ángulos de motor (deg), velocidades (deg/s), orientación y validez; lo que lee la pestaña de importación."""
    import csv
    with open(archivo, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "m1_deg", "m2_deg", "m3_deg", "v1_degs", "v2_degs", "v3_degs", "inclinacion_deg", "azimut_deg", "torsion_deg", "valido"])
        malos = {i for i, _ in tray.violaciones}
        for k in range(len(tray.t)):
            w.writerow(["%.4f" % tray.t[k]] + ["%.3f" % x for x in np.degrees(tray.motor[k])] + ["%.3f" % x for x in np.degrees(tray.motor_vel[k])] +
                       ["%.3f" % math.degrees(tray.inclinacion[k]), "%.2f" % math.degrees(tray.azimut[k]), "%.3f" % math.degrees(tray.torsion[k]), 0 if k in malos else 1])


def desde_csv(spm, trans, lim, archivo):
    """Importa una trayectoria de motores (columnas t, m1, m2, m3 en grados; con o sin encabezado) y reconstruye la orientación con FK."""
    import csv
    filas = []
    with open(archivo, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            try:
                filas.append([float(x) for x in r[:4]])
            except ValueError:
                continue                                                  # encabezado o línea no numérica
    if len(filas) < 2 or any(len(r) < 4 for r in filas):
        raise ValueError("el CSV debe tener al menos 2 filas con 4 columnas: t, m1, m2, m3 (grados de motor)")
    d = np.array(filas)
    return _construir(spm, trans, lim, d[:, 0] - d[0, 0], trans.a_nnr(np.radians(d[:, 1:4])))


def expandir_esperas(puntos, esperas, tiempos):
    """Inserta una pausa tras cada punto de paso: devuelve (orientaciones, T por tramo). 'tiempos[i]' es el tiempo para llegar al punto i+1;
    'esperas[i]' los segundos que se queda quieto en el punto i (un tramo de duración 'espera' entre dos copias del mismo punto)."""
    R, T = [puntos[0]], []
    if esperas[0] > 0:
        R.append(puntos[0]); T.append(esperas[0])
    for i in range(1, len(puntos)):
        R.append(puntos[i]); T.append(tiempos[i - 1])
        if esperas[i] > 0:
            R.append(puntos[i]); T.append(esperas[i])
    return R, T


def inclinacion_maxima(spm, lim, az_deg, tors_deg=0.0, paso=0.25, tope=40.0):
    """Mayor inclinación (°) hacia el azimut dado, con esa torsión, que sigue dentro de límites (colisión según el mapa del CAD, singularidades)."""
    ult = 0.0
    for tl in np.arange(0.0, tope + 1e-9, paso):
        R = desde_inclinacion(*np.radians([tl, az_deg, tors_deg]))
        try:
            ok, _ = lim.valida(spm, R, spm.ik(R))
        except FueraDeEspacioDeTrabajo:
            break
        if not ok:
            break
        ult = float(tl)
    return ult


def programa_caja(spm, lim, az_volcado=0.0, espera=5.0, t_giro=2.0, t_volcado=1.5, margen=0.25):
    """Puntos de paso [incl, azim, torsión, T tramo, espera] del programa «ver las 4 caras de una caja y volcarla»: pose inicial con 5 s de espera;
    giros de 90° con 5 s de espera en cada cara (0°, 90°, 180°, 270°); inclinación rápida hasta el máximo permitido hacia 'az_volcado' (con la torsión de la
    4.ª cara), pausa; vuelta a nivel y giro de 90° que cierra la vuelta completa hasta la pose inicial."""
    tmax = max(0.0, inclinacion_maxima(spm, lim, az_volcado, 270.0) - margen)
    tmax = math.floor(tmax * 2) / 2                                    # redondeado hacia abajo a 0.5°
    return [[0, 0, 0, 0.0, espera], [0, 0, 90, t_giro, espera], [0, 0, 180, t_giro, espera], [0, 0, 270, t_giro, espera],
            [tmax, az_volcado, 270, t_volcado, 3.0], [0, 0, 270, 2.0, 0.0], [0, 0, 360, t_giro, 0.0]], tmax


def programa_oscilacion_motores(spm, trans, amplitud_deg=70.0, T_tramo=3.0, motores=(0, 1, 2), espera=0.0):
    """Prueba de recorrido de motores uno a uno: para cada motor de `motores` (0, 1, 2 = M1, M2, M3), 0 -> +A -> 0 -> -A -> 0 (A en ° de motor, eje de salida)
    con los otros dos en 0. Devuelve los puntos de paso [incl, azim, torsión, T tramo, espera] de esas poses (hay que planificarlos en modo ARTICULAR, que interpola
    los ángulos de motor linealmente y por tanto mueve un solo motor por tramo). Las poses salen de la cinemática directa, sin pasar por el CAD ni los topes."""
    filas, prev, primera = [], np.eye(3), True
    for j in motores:
        for a in ((0.0, amplitud_deg, 0.0, -amplitud_deg, 0.0) if primera else (amplitud_deg, 0.0, -amplitud_deg, 0.0)):   # el 0 final de un motor es el inicial del siguiente
            m = np.zeros(3); m[j] = math.radians(a)
            R = spm.fk(trans.a_nnr(m), prev); prev = R
            inc, az, to = a_inclinacion(R)
            if abs(inc) < 1e-9:
                az = to = 0.0                                              # a nivel el azimut no existe: se deja la pose inicial limpia
            if primera:
                filas.append([0.0, 0.0, 0.0, 0.0, espera]); primera = False
                continue
            filas.append([round(math.degrees(inc), 6), round(math.degrees(az), 6), round(math.degrees(to), 6), T_tramo, espera])
    return filas


def programa_caja_reducida(spm, lim, paso_deg=30.0, inclinacion_deg=10.0, az_volcado=0.0, espera=5.0, t_giro=2.0, t_volcado=1.5, margen=0.25):
    """Como `programa_caja`, pero con ángulos más pequeños para probar el mecanismo real sin llegar a sus topes: las 4 caras están separadas `paso_deg`
    (torsión 0, paso, 2·paso, 3·paso en vez de 0, 90, 180, 270°) y el volcado inclina `inclinacion_deg` como mucho (se recorta al máximo que permite el CAD con
    la torsión de la 4.ª cara). Vuelve a nivel y deshace el giro hasta la pose inicial. Devuelve (puntos de paso, inclinación de volcado usada)."""
    tors = 3.0 * paso_deg
    tmax = max(0.0, inclinacion_maxima(spm, lim, az_volcado, tors) - margen)
    tilt = math.floor(min(inclinacion_deg, tmax) * 2) / 2                 # redondeado hacia abajo a 0.5°
    return [[0, 0, 0, 0.0, espera], [0, 0, paso_deg, t_giro, espera], [0, 0, 2 * paso_deg, t_giro, espera], [0, 0, tors, t_giro, espera],
            [tilt, az_volcado, tors, t_volcado, 3.0], [0, 0, tors, 2.0, 0.0], [0, 0, 0, 3 * t_giro, 0.0]], tilt
