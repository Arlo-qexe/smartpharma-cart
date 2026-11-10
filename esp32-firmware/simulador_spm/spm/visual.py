"""Gráficas y animación (matplotlib). Dibuja el mecanismo como 'esqueleto' sobre una esfera unidad centrada en O."""
import math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .rot import rotz, a_inclinacion

COL = ["#d9822b", "#2b7bd9", "#3aa655"]


def _arco(u, v, n=24):
    """Arco de círculo máximo entre los vectores unitarios u y v."""
    d = float(np.clip(u @ v, -1, 1)); th = math.acos(d)
    if th < 1e-9:
        return np.array([u, v])
    s = np.linspace(0, 1, n)[:, None]
    return (np.sin((1 - s) * th) * u + np.sin(s * th) * v) / math.sin(th)


def dibujar_pose(spm, R, theta, ax=None, titulo=None):
    ax = ax or plt.figure(figsize=(5.5, 5.5)).add_subplot(111, projection="3d")
    u = np.linspace(0, 2 * np.pi, 60)
    for tl in (0, 0.5, -0.5):                                        # esfera de referencia (ecuador y dos paralelos)
        r = math.sqrt(1 - tl * tl)
        ax.plot(r * np.cos(u), r * np.sin(u), tl * np.ones_like(u), color="#cccccc", lw=0.6)
    ax.plot([0, 0], [0, 0], [-1.15, 1.15], color="#888888", lw=1.2, ls="--")            # eje central (engranes)
    p2s = []
    R, theta = spm.a_cad(R, theta)
    for i in range(3):
        u1 = rotz(theta[i]) @ spm.a1[i]; w2 = R @ spm.a2[i]
        s1, s2 = np.sign(u1[2]) or 1.0, np.sign(w2[2]) or 1.0
        p1, p2 = s1 * u1, s2 * w2                                     # se dibuja el extremo del eje que mira hacia arriba
        p2s.append(p2)
        ax.plot(*_arco(p1, p2).T, color=COL[i], lw=3.5, solid_capstyle="round")   # Brazov
        ax.plot([0, p1[0]], [0, p1[1]], [0, p1[2]], color=COL[i], lw=1.0, ls=":")
        ax.scatter(*p1, color=COL[i], s=45, edgecolor="k", zorder=5)                  # unión con el Brasop (gira con el motor)
        ax.scatter(*p2, color=COL[i], s=45, marker="s", edgecolor="k", zorder=5)       # unión con la plataforma
        ax.text(*(1.12 * p1), "NNR%d" % (i + 1), color=COL[i], fontsize=8, ha="center")
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    ax.add_collection3d(Poly3DCollection([np.array(p2s)], facecolor=(0.6, 0.6, 0.65, 0.35), edgecolor="#555555", lw=1))   # plataforma (triángulo de sus 3 uniones)
    n = R[:, 2]
    ax.quiver(0, 0, 0, *n, color="k", lw=2, arrow_length_ratio=0.12)                  # normal de la plataforma
    ax.set_xlim(-1.1, 1.1); ax.set_ylim(-1.1, 1.1); ax.set_zlim(-1.1, 1.1); ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off(); ax.view_init(elev=22, azim=35)
    if titulo: ax.set_title(titulo, fontsize=10)
    return ax


def graficar_trayectoria(tray, archivo, titulo="Trayectoria"):
    fig, axs = plt.subplots(4, 1, figsize=(8, 9), sharex=True)
    t = tray.t; nombre = ["Motor 1 (NNR1)", "Motor 2 (NNR2)", "Motor 3 (NNR3)"]
    for i in range(3):
        axs[0].plot(t, np.degrees(tray.motor[:, i]), label=nombre[i], color=COL[i])
        axs[1].plot(t, np.degrees(tray.motor_vel[:, i]) / 6, color=COL[i])          # deg/s -> rpm: /6
        axs[2].plot(t, np.degrees(tray.motor_acc[:, i]), color=COL[i])
    axs[3].plot(t, np.degrees(tray.inclinacion), label="inclinación", color="k")
    axs[3].plot(t, np.degrees(tray.torsion), label="torsión", color="#888888")
    axs[0].set_ylabel("ángulo motor (°)"); axs[1].set_ylabel("velocidad (rpm)"); axs[2].set_ylabel("aceleración (°/s²)")
    axs[3].set_ylabel("plataforma (°)"); axs[3].set_xlabel("t (s)")
    axs[0].legend(ncol=3, fontsize=8); axs[3].legend(ncol=2, fontsize=8)
    axs[0].set_title("%s  (duración %.2f s%s)" % (titulo, tray.duracion, ", tiempo alargado x%.2f" % tray.factor_tiempo if tray.factor_tiempo > 1.001 else ""))
    for a in axs: a.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(archivo, dpi=110); plt.close(fig)


def graficar_seguimiento(reg, archivo):
    fig, axs = plt.subplots(2, 1, figsize=(8, 5.5), sharex=True)
    for i in range(3):
        axs[0].plot(reg.t, np.degrees(reg.motor_ref[:, i] - reg.motor_med[:, i]), color=COL[i], label="Motor %d" % (i + 1))
    axs[0].set_ylabel("error motor (°)"); axs[0].legend(ncol=3, fontsize=8)
    axs[1].plot(reg.t, reg.error_deg, color="k"); axs[1].set_ylabel("error orientación (°)"); axs[1].set_xlabel("t (s)")
    for a in axs: a.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(archivo, dpi=110); plt.close(fig)


def animar(spm, tray, archivo, paso=2, fps=20):
    from matplotlib.animation import PillowWriter
    fig = plt.figure(figsize=(5.5, 5.5)); ax = fig.add_subplot(111, projection="3d")
    w = PillowWriter(fps=fps)
    with w.saving(fig, archivo, dpi=90):
        for k in range(0, len(tray.t), paso):
            ax.clear()
            tilt, az, tor = a_inclinacion(tray.R[k])
            dibujar_pose(spm, tray.R[k], tray.theta[k], ax,
                         "t=%.2f s   inclinación %.1f°  azimut %.0f°  torsión %.1f°" % (tray.t[k], math.degrees(tilt), math.degrees(az), math.degrees(tor)))
            w.grab_frame()
    plt.close(fig)


def mapa_espacio_trabajo(spm, lim, archivo, tilt_max=30, n_tilt=60, n_az=120, torsion_deg=0.0):
    """Mapa polar (radio = inclinación, ángulo = azimut): alcanzable / permitido / singularidad."""
    from .rot import desde_inclinacion
    from .cinematica import FueraDeEspacioDeTrabajo
    tilts = np.linspace(0, tilt_max, n_tilt); azs = np.linspace(0, 2 * np.pi, n_az)
    est = np.zeros((n_tilt, n_az)); sig = np.full((n_tilt, n_az), np.nan)
    for i, tl in enumerate(tilts):
        prev = np.zeros(3)
        for j, a in enumerate(azs):
            R = desde_inclinacion(math.radians(tl), a, math.radians(torsion_deg))
            try:
                th = spm.ik(R, prev); prev = th
            except FueraDeEspacioDeTrabajo:
                est[i, j] = 0; continue
            sig[i, j] = spm.indice_singularidad(R, th)[0]
            est[i, j] = 2 if lim.valida(spm, R, th)[0] else 1
    fig = plt.figure(figsize=(6.5, 6)); ax = fig.add_subplot(111, projection="polar")
    cm = matplotlib.colors.ListedColormap(["#d9534f", "#f0ad4e", "#5cb85c"])
    ax.pcolormesh(azs, tilts, est, cmap=cm, vmin=0, vmax=2, shading="auto")
    ax.set_title("Espacio de trabajo (torsión %.0f°)\nverde: permitido · naranja: alcanzable pero fuera de límites · rojo: sin solución" % torsion_deg, fontsize=9)
    fig.tight_layout(); fig.savefig(archivo, dpi=110); plt.close(fig)
    return est, sig
