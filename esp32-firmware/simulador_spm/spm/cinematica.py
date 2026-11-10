"""Cinemática del manipulador esférico paralelo 3-RRR (ejes concurrentes en O).

Modelo: la pata i gira con su engrane NNR un ángulo theta_i alrededor del eje central z. El eje a1_i (unión Brasop-Brazov) queda en
u_i = Rz(theta_i) a1_i. La plataforma tiene orientación R; su eje a2_i (unión Brazov-platosupv) queda en w_i = R a2_i. El Brazov es
rígido, así que el ángulo entre u_i y w_i es constante:

    f_i = (R a2_i) . (Rz(theta_i) a1_i) - a1_i . a2_i = 0     (i = 1,2,3)

Referencia (NucleoCAD): R = I y theta = 0 es la pose de ensamblaje del CAD (el disco de la plataforma queda inclinado 16°).
La clase pública SPM re-expresa todo respecto a la POSE INICIAL con el disco HORIZONTAL (R = I, theta = 0, motores en 0); ver más abajo.
"""
import math
import numpy as np
from .geometria import Geometria
from .rot import rotz, rot_axis, exp_so3, wrap, angulo_entre


class FueraDeEspacioDeTrabajo(ValueError):
    """La orientación pedida no tiene solución cinemática."""


class NucleoCAD:
    """Cinemática en el marco del CAD (pose de ensamblaje = referencia). Usar SPM, que añade la pose inicial horizontal."""
    def __init__(self, geom=None):
        self.g = geom or Geometria.cargar()
        self.a1, self.a2 = self.g.a1, self.g.a2
        self.c = np.einsum("ij,ij->i", self.a1, self.a2)

    # ------------------------------------------------------------------ cinemática inversa
    def _rhs(self, i, R):
        a, w = self.a1[i], R @ self.a2[i]
        ra, rw = math.hypot(a[0], a[1]), math.hypot(w[0], w[1])
        if ra < 1e-12 or rw < 1e-12:
            raise FueraDeEspacioDeTrabajo("eje de la pata %d alineado con el eje central (singularidad)" % (i + 1))
        rhs = (self.c[i] - a[2] * w[2]) / (ra * rw)
        return rhs, math.atan2(w[1], w[0]) - math.atan2(a[1], a[0])

    def ik_soluciones(self, R):
        """(3,2): las dos soluciones de theta (rad) por pata, o None en la pata sin solución."""
        out = np.zeros((3, 2))
        for i in range(3):
            rhs, base = self._rhs(i, R)
            if abs(rhs) > 1 + 1e-9:
                raise FueraDeEspacioDeTrabajo("pata %d: la orientación no es alcanzable (cos = %.3f)" % (i + 1, rhs))
            s = math.acos(max(-1.0, min(1.0, rhs)))
            out[i] = wrap([base + s, base - s])
        return out

    def ik(self, R, prev=None):
        """theta (3,) en rad para la orientación R. Elige en cada pata la rama más cercana a 'prev' (por defecto 0 = pose de ensamblaje)."""
        prev = np.zeros(3) if prev is None else np.asarray(prev, float)
        sol = self.ik_soluciones(R)
        out = np.zeros(3)
        for i in range(3):
            d = wrap(sol[i] - prev[i])                                     # diferencia mínima módulo 2*pi con cada rama
            out[i] = prev[i] + d[np.argmin(np.abs(d))]                     # así el resultado sigue a 'prev' aunque ya haya dado varias vueltas
        return out

    def angulos_pasivos(self, R, theta):
        """Giro (rad) de cada Brazov respecto a su Brasop, alrededor de u_i."""
        phi = np.zeros(3)
        for i in range(3):
            Rt = rotz(theta[i])
            w1, u, tgt = Rt @ self.a1[i], Rt @ self.a2[i], R @ self.a2[i]
            up, tp = u - (u @ w1) * w1, tgt - (tgt @ w1) * w1
            phi[i] = math.atan2(w1 @ np.cross(up, tp), up @ tp)
        return phi

    def orientaciones_cuerpos(self, R, theta):
        """Rotaciones (alrededor de O) de cada cuerpo móvil respecto a la pose de ensamblaje."""
        phi = self.angulos_pasivos(R, theta)
        d = {"plataforma": R}
        for i in range(3):
            Rt = rotz(theta[i])
            d["nnr_brasop_%d" % (i + 1)] = Rt
            d["brazov_%d" % (i + 1)] = rot_axis(Rt @ self.a1[i], phi[i]) @ Rt
        return d

    # ------------------------------------------------------------------ cinemática directa
    def residuo(self, R, theta):
        return np.array([(R @ self.a2[i]) @ (rotz(theta[i]) @ self.a1[i]) - self.c[i] for i in range(3)])

    def jacobianos(self, R, theta):
        """A, B con  A w + diag(B) theta_dot = 0  (w = velocidad angular de la plataforma en el marco fijo)."""
        A, B = np.zeros((3, 3)), np.zeros(3)
        for i in range(3):
            u, w = rotz(theta[i]) @ self.a1[i], R @ self.a2[i]
            A[i] = np.cross(w, u)
            B[i] = np.cross(u, w)[2]
        return A, B

    def fk(self, theta, R0=None, tol=1e-12, max_iter=60):
        """Orientación de la plataforma para theta (rad). Newton partiendo de R0 (por defecto la pose de ensamblaje); converge a la solución
        de montaje más cercana a R0."""
        theta = np.asarray(theta, float)
        R = np.eye(3) if R0 is None else np.array(R0, float)
        for _ in range(max_iter):
            f = self.residuo(R, theta)
            if np.max(np.abs(f)) < tol:
                return R
            A, _ = self.jacobianos(R, theta)
            lam = 1e-9 if np.linalg.cond(A) < 1e8 else 1e-3                 # amortiguamiento cerca de singularidades
            w = np.linalg.solve(A.T @ A + lam * np.eye(3), -A.T @ f)
            n = np.linalg.norm(w)
            if n > 0.3:
                w *= 0.3 / n                                                # paso máximo 0.3 rad
            R = exp_so3(w) @ R
        raise FueraDeEspacioDeTrabajo("la cinemática directa no converge para theta = %s deg" % np.degrees(theta).round(2))

    def fk_todas(self, theta, n_inicios=40, seed=0):
        """Soluciones de montaje distintas para theta (rotaciones), probando varios inicios."""
        rng = np.random.default_rng(seed)
        sols = []
        for k in range(n_inicios):
            R0 = np.eye(3) if k == 0 else exp_so3(rng.normal(size=3) * rng.uniform(0.2, 3.0))
            try:
                R = self.fk(theta, R0)
            except FueraDeEspacioDeTrabajo:
                continue
            if all(angulo_entre(R, S) > 1e-6 for S in sols):
                sols.append(R)
        return sols

    # ------------------------------------------------------------------ velocidades y singularidades
    def thetadot_desde_omega(self, R, theta, omega):
        A, B = self.jacobianos(R, theta)
        if np.min(np.abs(B)) < 1e-9:
            raise FueraDeEspacioDeTrabajo("singularidad serie (una pata no puede mover la plataforma en esa dirección)")
        return -(A @ np.asarray(omega, float)) / B

    def omega_desde_thetadot(self, R, theta, thetadot):
        A, B = self.jacobianos(R, theta)
        return np.linalg.solve(A, -B * np.asarray(thetadot, float))

    def indice_singularidad(self, R, theta):
        """(sigma_min(A), min|B|): ambos tienden a 0 al acercarse a una singularidad (paralela o serie)."""
        A, B = self.jacobianos(R, theta)
        return float(np.linalg.svd(A, compute_uv=False)[-1]), float(np.min(np.abs(B)))


class SPM:
    """Cinemática del mecanismo con la pose inicial en HORIZONTAL.

    horizontal=True (por defecto): R = I significa 'disco de la plataforma horizontal' y theta = 0 la posición de los engranes en esa pose (los
    motores se ponen a cero ahí). R_h es la rotación que lleva el disco desde la pose de ensamblaje del CAD hasta la horizontal y th_h los ángulos
    de los NNR en esa pose: R_cad = R @ R_h, theta_cad = theta + th_h.
    horizontal=False: marco original del CAD (pose de ensamblaje = referencia).
    """
    def __init__(self, geom=None, horizontal=True):
        self.n = NucleoCAD(geom)
        self.g, self.a1, self.a2, self.c = self.n.g, self.n.a1, self.n.a2, self.n.c
        self.horizontal = horizontal
        self.R_h = self.g.R_horizontal() if horizontal else np.eye(3)
        self.th_h = self.n.ik(self.R_h) if horizontal else np.zeros(3)

    def a_cad(self, R, theta):
        """(R_cad, theta_cad): la misma pose en el marco del CAD."""
        return R @ self.R_h, np.asarray(theta, float) + self.th_h

    def ik_soluciones(self, R):
        return wrap(self.n.ik_soluciones(R @ self.R_h) - self.th_h[:, None])

    def ik(self, R, prev=None):
        prev_c = self.th_h if prev is None else np.asarray(prev, float) + self.th_h
        return self.n.ik(R @ self.R_h, prev_c) - self.th_h

    def angulos_pasivos(self, R, theta):
        return self.n.angulos_pasivos(*self.a_cad(R, theta))

    def orientaciones_cuerpos(self, R, theta):
        """Rotaciones de los cuerpos respecto a la pose de ensamblaje del CAD (lo que necesita el render)."""
        return self.n.orientaciones_cuerpos(*self.a_cad(R, theta))

    def residuo(self, R, theta):
        return self.n.residuo(*self.a_cad(R, theta))

    def jacobianos(self, R, theta):
        return self.n.jacobianos(*self.a_cad(R, theta))

    def fk(self, theta, R0=None, **kw):
        theta_c = np.asarray(theta, float) + self.th_h
        R0_c = self.R_h if R0 is None else np.asarray(R0, float) @ self.R_h
        return self.n.fk(theta_c, R0_c, **kw) @ self.R_h.T

    def fk_todas(self, theta, n_inicios=40, seed=0):
        sols = self.n.fk_todas(np.asarray(theta, float) + self.th_h, n_inicios, seed)
        return [S @ self.R_h.T for S in sols]

    def thetadot_desde_omega(self, R, theta, omega):
        return self.n.thetadot_desde_omega(*self.a_cad(R, theta), omega)

    def omega_desde_thetadot(self, R, theta, thetadot):
        return self.n.omega_desde_thetadot(*self.a_cad(R, theta), thetadot)

    def indice_singularidad(self, R, theta):
        return self.n.indice_singularidad(*self.a_cad(R, theta))
