"""Utilidades de rotaciones (solo numpy): matrices, cuaterniones, SLERP y parametrización inclinación/azimut/torsión."""
import math
import numpy as np


def rotx(t):
    c, s = math.cos(t), math.sin(t)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def roty(t):
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rotz(t):
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def hat(v):
    return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])


def vee(M):
    return np.array([M[2, 1], M[0, 2], M[1, 0]])


def rot_axis(axis, ang):
    """Rotación de 'ang' rad alrededor de 'axis' (no necesita ser unitario)."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    K = hat(a)
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def exp_so3(w):
    w = np.asarray(w, float)
    th = np.linalg.norm(w)
    if th < 1e-12:
        return np.eye(3) + hat(w)
    return rot_axis(w, th)


def log_so3(R):
    """Vector de rotación (eje*ángulo) de R."""
    c = (np.trace(R) - 1) / 2
    ang = math.acos(max(-1.0, min(1.0, c)))
    if ang < 1e-9:
        return vee(R - R.T) / 2
    if math.pi - ang < 1e-6:                              # cerca de 180°: el eje sale de la diagonal
        d = np.sqrt(np.maximum((np.diag(R) + 1) / 2, 0))
        k = int(np.argmax(d))
        ax = d.copy()
        for j in range(3):
            if j != k and (R[k, j] + R[j, k]) < 0:
                ax[j] = -ax[j]
        return ax / np.linalg.norm(ax) * ang
    return vee(R - R.T) / (2 * math.sin(ang)) * ang


def angulo_entre(R1, R2):
    """Ángulo (rad) de la rotación relativa entre dos orientaciones."""
    return float(np.linalg.norm(log_so3(R1.T @ R2)))


# ------------------------------------------------------------------ cuaterniones (w, x, y, z)
def quat_de_R(R):
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1) * 2
        q = [s / 4, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, s / 4, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, s / 4, (R[1, 2] + R[2, 1]) / s]
    else:
        s = math.sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, s / 4]
    q = np.array(q)
    return q / np.linalg.norm(q)


def R_de_quat(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def slerp(q0, q1, s):
    d = float(q0 @ q1)
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + s * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - s) * th) * q0 + math.sin(s * th) * q1) / math.sin(th)


# ------------------------------------------------------------------ inclinación / azimut / torsión
def desde_inclinacion(tilt, az=0.0, torsion=0.0):
    """Orientación: la normal de la plataforma se inclina 'tilt' hacia el azimut 'az' (desde +X hacia +Y) y luego gira 'torsion' sobre su propio eje. Radianes."""
    Rt = rotz(az) @ roty(tilt) @ rotz(-az)
    return Rt @ rotz(torsion)


def a_inclinacion(R):
    """Inverso de desde_inclinacion: (tilt, az, torsion) en rad. Con tilt=0 el azimut no está definido y se devuelve 0."""
    n = R[:, 2]
    tilt = math.acos(max(-1.0, min(1.0, n[2])))
    az = math.atan2(n[1], n[0]) if tilt > 1e-9 else 0.0
    Rt = rotz(az) @ roty(tilt) @ rotz(-az)
    M = Rt.T @ R
    return tilt, az, math.atan2(M[1, 0], M[0, 0])


def wrap(x):
    """Lleva un ángulo (o array) a (-pi, pi]."""
    return (np.asarray(x) + math.pi) % (2 * math.pi) - math.pi
