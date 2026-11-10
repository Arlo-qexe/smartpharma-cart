"""Ventana principal: simulador del manipulador esférico paralelo (SPM) con el modelo CAD, al estilo de la interfaz del brazo 3DOF."""
import argparse
import math
import os
import sys
from pathlib import Path
import numpy as np
import tkinter as tk
from tkinter import ttk

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from spm import SPM, cargar_config, a_inclinacion
from spm.modelo3d import ModeloCAD
from spm.render_gl import RenderGL
from . import tema
from .pestana_pose import PestanaPose
from .pestana_control import PestanaControl
from .pestana_trayectoria import PestanaTrayectoria
from .pestana_serial import PestanaSerial
from .pestana_espacio import PestanaEspacio


class AppSPM(tk.Tk):
    def __init__(self):
        super().__init__()
        tema.aplicar_tema_mpl()
        self.title("Manipulador esférico paralelo — Simulador con modelo CAD")
        self.geometry("1500x930"); self.minsize(1180, 760); self.configure(bg=tema.BG)
        self.spm = SPM(); self.trans, self.lim = cargar_config()
        self.modelo_hq = ModeloCAD(self.spm); self.modelo_lod = ModeloCAD(self.spm, lod=12000)         # render por software (plan B) y puntos de colisión
        self.render_gl = None if os.environ.get("SPM_RENDER") == "mpl" else RenderGL.crear(self.spm)       # render por GPU (OpenGL/EGL)
        self.R, self.theta, self.tray = np.eye(3), np.zeros(3), None
        self._estilo()

        cab = tk.Frame(self, bg=tema.PANEL, height=44, highlightthickness=1, highlightbackground=tema.BORDER); cab.pack(fill="x"); cab.pack_propagate(False)
        tk.Label(cab, text="SPM  ", bg=tema.PANEL, fg=tema.BTN_HOV, font=(tema.FAMILIA, 13, "bold")).pack(side="left", padx=(14, 0))
        tk.Label(cab, text="Manipulador esférico paralelo · simulador CAD", bg=tema.PANEL, fg=tema.MUTED, font=tema.FUENTE).pack(side="left", padx=6)
        self.barra = tk.Frame(cab, bg=tema.PANEL); self.barra.pack(side="right", padx=8)
        self.main = tk.Frame(self, bg=tema.BG); self.main.pack(fill="both", expand=True)
        self.estado_lbl = tk.Label(self, text="", anchor="w", bg=tema.PANEL, fg=tema.MUTED, font=tema.FUENTE); self.estado_lbl.pack(fill="x")

        self.pestanas = {}; self._botones = {}; self.actual = None; self._sucio = set()
        for clave, cls in (("pose", PestanaPose), ("control", PestanaControl), ("trayectoria", PestanaTrayectoria), ("serial", PestanaSerial), ("espacio", PestanaEspacio)):
            p = cls(self, self.main); self.pestanas[clave] = p
            b = tk.Button(self.barra, text=p.nombre, command=lambda c=clave: self.ir_a(c), bg=tema.BTN_BG, fg=tema.TEXT, relief="flat", bd=0, font=tema.FUENTE_B, padx=12, pady=6, cursor="hand2",
                          highlightthickness=0)
            b.pack(side="left", padx=2); self._botones[clave] = b
        self.ir_a("pose")
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.estado_lbl.config(text="  render: %s · mapa de colisiones: %s · relación de engranes %.3f (supuesta) · límites de motor: marcadores" % (
            "GPU %s (%d triángulos)" % (self.render_gl.renderer.split(" (")[0], self.render_gl.n_triangulos) if self.render_gl else "software (sin OpenGL: %s)" % getattr(RenderGL, "ultimo_error", "desactivado"),
            "CAD" if self.lim.mapa is not None else "no disponible (cono 28°)", self.trans.relacion))

    def _estilo(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("SPM.Treeview", background=tema.BG, fieldbackground=tema.BG, foreground=tema.TEXT, rowheight=20, borderwidth=0, font=tema.FUENTE)
        s.configure("SPM.Treeview.Heading", background=tema.BTN_BG, foreground=tema.ACCENT, relief="flat", font=tema.FUENTE_B)
        s.map("SPM.Treeview", background=[("selected", tema.BTN_HOV)])
        s.configure("TCombobox", fieldbackground=tema.BTN_BG, background=tema.BTN_BG, foreground=tema.ACCENT, arrowcolor=tema.ACCENT)
        s.configure("TScrollbar", background=tema.BTN_BG, troughcolor=tema.BG)

    # ------------------------------------------------------------------ navegación y estado compartido
    def ir_a(self, clave):
        if self.actual == clave:
            self.pestanas[clave].activar(); return
        if self.actual is not None:
            self.pestanas[self.actual].frame.pack_forget(); self._botones[self.actual].config(bg=tema.BTN_BG, fg=tema.TEXT)
        self.actual = clave
        self.pestanas[clave].frame.pack(fill="both", expand=True); self._botones[clave].config(bg=tema.BTN_HOV, fg="white")
        self.update_idletasks(); self.pestanas[clave].activar()

    def estado(self, R, theta):
        """(ok, motivo, holgura_mm, (sigma, B)) de una pose según los límites y el mapa de colisiones del CAD."""
        ok, motivo = self.lim.valida(self.spm, R, theta)
        h = float("nan")
        if self.lim.mapa is not None:
            t, a, s = (math.degrees(x) for x in a_inclinacion(R)); h = self.lim.mapa.consultar(t, a, s)[0]
        return ok, motivo, h, self.spm.indice_singularidad(R, theta)

    def fijar_pose(self, R, theta, origen="pose", refrescar_pose=True):
        self.R, self.theta = R, theta
        if refrescar_pose and self.actual == "pose" and origen != "trayectoria":
            self.pestanas["pose"].refrescar()
        elif origen == "robot":
            self._sucio.add("pose")
            if self.actual == "pose": self.pestanas["pose"].refrescar()

    def _cerrar(self):
        try:
            self.pestanas["trayectoria"].pausar(); self.pestanas["serial"]._desconectar()
        except Exception:
            pass
        self.destroy()

    # ------------------------------------------------------------------ capturas para pruebas
    def capturar(self, carpeta):
        carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
        for clave, p in self.pestanas.items():
            self.ir_a(clave); self.update()
            for nombre in ("vista", "fig"):
                obj = getattr(p, nombre, None)
                if obj is None: continue
                (obj.guardar_png if hasattr(obj, "guardar_png") else lambda f, o=obj: o.savefig(f, dpi=90, facecolor=tema.BG))(str(carpeta / ("%s_%s.png" % (clave, nombre))))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Interfaz del simulador del SPM")
    ap.add_argument("--captura", metavar="CARPETA", help="renderiza las pestañas a PNG y sale (para pruebas)")
    a = ap.parse_args(argv)
    app = AppSPM()
    if a.captura:
        app.withdraw(); app.pestanas["trayectoria"].planificar(); app.capturar(a.captura); app.destroy(); return 0
    app.mainloop(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
