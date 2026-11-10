"""Modelo 3D del CAD movido por la cinemática del simulador.

Las mallas (datos/malla_cuerpos.npz, generadas con herramientas/exportar_malla_cad.py) están agrupadas por cuerpo cinemático. Cada cuerpo se
gira alrededor de O con la rotación que da SPM.orientaciones_cuerpos y se dibuja con un rasterizador sencillo (orden por profundidad +
sombreado de Lambert) sobre matplotlib; no necesita OpenGL ni FreeCAD.
"""
import math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

from .geometria import RUTA_DATOS
from .rot import a_inclinacion


def _color(nombre):
    if nombre.startswith(("Brasop", "platosupv")): return (0.95, 0.52, 0.10), 1.0
    if nombre.startswith("Brazov"): return (0.25, 0.55, 0.85), 1.0
    if nombre.startswith("NNR"): return (0.55, 0.60, 0.66), 1.0
    if nombre.startswith("rodamiento"): return (0.08, 0.15, 0.55), 1.0
    if nombre == "stopper": return (0.85, 0.25, 0.25), 1.0
    if nombre == "platov3": return (0.80, 0.82, 0.88), 0.55
    if nombre.startswith("anillo"): return (0.30, 0.65, 0.40), 1.0
    return (0.74, 0.76, 0.79), 1.0


def decimar(V, F, celda):
    """Decimación por agrupación de vértices en una rejilla de 'celda' mm."""
    key = np.floor(V / celda).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    nv = np.zeros((len(uniq), 3)); np.add.at(nv, inv, V); nv /= np.bincount(inv)[:, None]
    F2 = inv[F]
    ok = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    return nv, F2[ok]


GRUPOS = {"fijo": "Base, anillo y tope", "plataforma": "Plataforma", "brazov": "Brazov + rodamientos", "nnr": "NNR + Brasop"}


class ModeloCAD:
    """lod: número aproximado de triángulos total para el modo interactivo (None = malla completa exportada)."""
    def __init__(self, spm, ruta=None, lod=None):
        self.spm = spm
        ruta = Path(ruta) if ruta else RUTA_DATOS / "malla_cuerpos.npz"
        z = np.load(ruta)
        self.partes = []                                   # (cuerpo, nombre, V, F, color, alfa)
        for k in z.files:
            cuerpo, nombre, campo = k.split("|")
            if campo != "V": continue
            col, al = _color(nombre)
            self.partes.append([cuerpo, nombre, z[k].astype(float), z["%s|%s|F" % (cuerpo, nombre)].astype(int), col, al])
        if lod:
            total = sum(len(p[3]) for p in self.partes)
            for p in self.partes:
                objetivo = max(250, int(len(p[3]) * lod / total))
                if len(p[3]) > objetivo:
                    cel = 0.6
                    while True:
                        v2, f2 = decimar(p[2], p[3], cel)
                        if len(f2) <= objetivo or cel > 30:
                            break
                        cel *= 1.25
                    p[2], p[3] = v2, f2
        self.partes = [tuple(p) for p in self.partes]
        self._empaquetar()
        self.puntos = {}                                   # cuerpo -> nube de puntos (para colisiones)
        for k in z.files:
            cuerpo, nombre, campo = k.split("|")
            if campo == "P":
                self.puntos.setdefault(cuerpo, []).append(z[k].astype(float))
        self.puntos = {c: np.concatenate(v) for c, v in self.puntos.items()}
        self.O = spm.g.O

    def _empaquetar(self):
        """Un bloque de arrays por cuerpo: vértices, caras, normales locales, color y índice de parte (para ocultar rápido)."""
        self.nombres = [p[1] for p in self.partes]
        self.empaque = {}
        for idx, (c, n, V, F, col, al) in enumerate(self.partes):
            tri = V[F]; nor = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            ln = np.linalg.norm(nor, axis=1); ln[ln == 0] = 1; nor /= ln[:, None]
            b = self.empaque.setdefault(c, {"V": [], "F": [], "N": [], "col": [], "al": [], "pid": [], "off": 0})
            b["V"].append(V); b["F"].append(F + b["off"]); b["off"] += len(V); b["N"].append(nor)
            b["col"].append(np.tile(col, (len(F), 1))); b["al"].append(np.full(len(F), al)); b["pid"].append(np.full(len(F), idx))
        for c, b in self.empaque.items():
            for k in ("V", "F", "N", "col", "al", "pid"):
                b[k] = np.concatenate(b[k])

    def grupo_de(self, cuerpo):
        return "fijo" if cuerpo == "fijo" else "plataforma" if cuerpo == "plataforma" else "nnr" if cuerpo.startswith("nnr") else "brazov"

    # ------------------------------------------------------------------ poses
    def rotaciones(self, R, theta):
        """cuerpo -> rotación alrededor de O (3x3)."""
        d = self.spm.orientaciones_cuerpos(R, theta)
        out = {"fijo": np.eye(3), "plataforma": d["plataforma"]}
        for i in (1, 2, 3):
            out["nnr%d" % i] = d["nnr_brasop_%d" % i]
            out["brazov%d" % i] = d["brazov_%d" % i]
        return out

    def vertices(self, R, theta):
        rot = self.rotaciones(R, theta)
        return [(c, n, (V - self.O) @ rot[c].T + self.O, F, col, al) for c, n, V, F, col, al in self.partes]

    def nubes(self, R, theta):
        rot = self.rotaciones(R, theta)
        return {c: (P - self.O) @ rot[c].T + self.O for c, P in self.puntos.items()}

    # ------------------------------------------------------------------ dibujo
    def _camara(self, azim, elev):
        a, e = math.radians(azim), math.radians(elev)
        mirando = -np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])      # del observador hacia la escena
        der = np.cross(mirando, [0, 0, 1]); der /= np.linalg.norm(der)
        arriba = np.cross(der, mirando)
        return der, arriba, mirando

    def dibujar(self, R, theta, ax, azim=-55, elev=22, luz=(0.4, -0.6, 0.7), ocultar=(), grupos_ocultos=()):
        """Dibuja el mecanismo en 'ax' (proyección ortográfica). 'ocultar': nombres de pieza; 'grupos_ocultos': claves de GRUPOS.
        Reutiliza la PolyCollection del eje entre llamadas (rápido para interactivo)."""
        der, arriba, mirando = self._camara(azim, elev)
        luz = np.asarray(luz, float); luz /= np.linalg.norm(luz)
        rot = self.rotaciones(R, theta)
        ocultos = np.array([i for i, n in enumerate(self.nombres) if n in ocultar], int)
        polys, cols, prof = [], [], []
        for c, b in self.empaque.items():
            if self.grupo_de(c) in grupos_ocultos:
                continue
            Rb = rot[c]
            Vt = (b["V"] - self.O) @ Rb.T + self.O
            xy = np.stack([Vt @ der, Vt @ arriba], axis=-1); z = Vt @ mirando
            F = b["F"]
            sh = 0.35 + 0.65 * np.abs((b["N"] @ Rb.T) @ luz)                       # sombreado a dos caras
            rgba = np.empty((len(F), 4)); rgba[:, :3] = np.clip(b["col"] * sh[:, None], 0, 1); rgba[:, 3] = b["al"]
            m = ~np.isin(b["pid"], ocultos) if len(ocultos) else slice(None)
            polys.append(xy[F][m]); cols.append(rgba[m]); prof.append(z[F].mean(axis=1)[m])
        polys = np.concatenate(polys); cols = np.concatenate(cols); prof = np.concatenate(prof)
        orden = np.argsort(-prof)                                                   # lo lejano primero
        pc = getattr(ax, "_spm_pc", None)
        if pc is None or pc not in ax.collections:
            pc = PolyCollection([], linewidths=0.25); ax.add_collection(pc); ax._spm_pc = pc
        pc.set_verts(polys[orden]); pc.set_facecolor(cols[orden]); pc.set_edgecolor(cols[orden])
        return pc

    def limites_vista(self, azim=-55, elev=22, margen=1.08):
        """Encuadre fijo para toda la animación: caja que contiene la pose de ensamblaje y poses inclinadas."""
        der, arriba, _ = self._camara(azim, elev)
        pts = np.concatenate([V for _, _, V, _, _, _ in self.vertices(np.eye(3), np.zeros(3))])
        x, y = pts @ der, pts @ arriba
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        r = max(x.max() - x.min(), y.max() - y.min()) / 2 * margen
        return cx - r, cx + r, cy - r, cy + r


def _figura(modelo, azim, elev, tam=6.0):
    fig = plt.figure(figsize=(tam, tam)); ax = fig.add_axes([0, 0, 1, 1])
    x0, x1, y0, y1 = modelo.limites_vista(azim, elev)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal"); ax.axis("off")
    return fig, ax


def imagen_pose(modelo, R, theta, archivo, azim=-55, elev=22, ocultar=(), dpi=110, texto=None):
    fig, ax = _figura(modelo, azim, elev)
    modelo.dibujar(R, theta, ax, azim, elev, ocultar=ocultar)
    if texto: ax.text(0.02, 0.98, texto, transform=ax.transAxes, va="top", fontsize=10, family="monospace")
    fig.savefig(archivo, dpi=dpi, facecolor="white"); plt.close(fig)


def animar_modelo(modelo, tray, trans, archivo, paso=2, fps=20, azim=-55, elev=22, dpi=80, ocultar=()):
    """Vídeo (mp4 si hay ffmpeg, GIF si no) del mecanismo CAD siguiendo la trayectoria."""
    import shutil
    from matplotlib.animation import FFMpegWriter, PillowWriter
    archivo = str(archivo)
    if archivo.endswith(".mp4") and not shutil.which("ffmpeg"):
        archivo = archivo[:-4] + ".gif"
    w = FFMpegWriter(fps=fps, codec="libx264", extra_args=["-pix_fmt", "yuv420p"]) if archivo.endswith(".mp4") else PillowWriter(fps=fps)
    fig, ax = _figura(modelo, azim, elev)
    with w.saving(fig, archivo, dpi=dpi):
        for k in range(0, len(tray.t), paso):
            for a in list(ax.collections): a.remove()
            for a in list(ax.texts): a.remove()
            modelo.dibujar(tray.R[k], tray.theta[k], ax, azim, elev, ocultar=ocultar)
            tilt, az, tor = a_inclinacion(tray.R[k]); m = np.degrees(tray.motor[k])
            ax.text(0.02, 0.98, "t = %5.2f s\ninclinación %5.1f°  azimut %4.0f°  torsión %5.1f°\nmotores  %+6.1f°  %+6.1f°  %+6.1f°" %
                    (tray.t[k], math.degrees(tilt), math.degrees(az), math.degrees(tor), *m), transform=ax.transAxes, va="top", fontsize=9, family="monospace")
            w.grab_frame()
    plt.close(fig)
    return archivo
