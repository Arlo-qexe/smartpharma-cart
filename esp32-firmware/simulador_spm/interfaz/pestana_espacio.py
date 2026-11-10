"""Pestaña 5 — Espacio de trabajo: mapa polar (inclinación × azimut) de lo permitido según el CAD. Clic en el mapa = ir a esa pose."""
import math
import numpy as np
import tkinter as tk
import matplotlib
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from spm import desde_inclinacion, FueraDeEspacioDeTrabajo
from . import tema
from .tema import PANEL, TEXT, MUTED


class PestanaEspacio:
    nombre = "Espacio de trabajo"

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=tema.BG)
        izq = tk.Frame(self.frame, bg=PANEL, width=330, highlightthickness=1, highlightbackground=tema.BORDER); izq.pack(side="left", fill="y", padx=(8, 4), pady=8); izq.pack_propagate(False)
        tema.etiqueta(izq, "ESPACIO DE TRABAJO PERMITIDO", MUTED).pack(anchor="w", padx=10, pady=(10, 4))
        tema.etiqueta(izq, "Verde: dentro de límites (colisiones del CAD,\nsingularidades).  Naranja: alcanzable pero\nfuera de límites.  Rojo: sin solución.\n\nClic en el mapa: lleva el CAD a esa pose.", MUTED, justify="left").pack(anchor="w", padx=10)
        f, self.v_tor, s, lv = tema.deslizador(izq, "torsión (°)", -30, 30, 0.0, lambda x: None, 1.0); f.pack(fill="x", padx=10, pady=(10, 2))
        s.bind("<ButtonRelease-1>", lambda e: self.calcular())
        f, self.v_max = tema.campo(izq, "inclinación máx (°)", 30, 5); f.pack(anchor="w", padx=10, pady=4)
        tema.boton(izq, "Recalcular mapa", self.calcular).pack(fill="x", padx=10, pady=4)
        self.info = tk.Label(izq, text="", bg=PANEL, fg=MUTED, font=tema.FUENTE, justify="left", anchor="w", wraplength=300); self.info.pack(anchor="w", padx=10, pady=8, fill="x")
        self.fig = Figure(figsize=(7, 7), facecolor=tema.BG)
        self.ax = self.fig.add_subplot(111, projection="polar"); self.ax.set_facecolor(PANEL)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame); self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)
        self.canvas.mpl_connect("button_press_event", self._clic)
        self._punto = None; self._hecho = False

    def calcular(self):
        app = self.app
        try:
            tmax = float(self.v_max.get().replace(",", "."))
        except ValueError:
            tmax = 30.0
        tor = float(self.v_tor.get())
        tilts = np.linspace(0, tmax, 36); azs = np.linspace(0, 2 * np.pi, 120)
        est = np.zeros((len(tilts), len(azs)))
        for i, tl in enumerate(tilts):
            prev = np.zeros(3)
            for j, a in enumerate(azs):
                R = desde_inclinacion(*np.radians([tl, math.degrees(a), tor]))
                try:
                    th = app.spm.ik(R, prev); prev = th
                except FueraDeEspacioDeTrabajo:
                    continue
                est[i, j] = 2 if app.lim.valida(app.spm, R, th)[0] else 1
        self.ax.clear(); self.ax.set_facecolor(PANEL)
        cm = matplotlib.colors.ListedColormap(["#da3633", "#bb8009", "#238636"])
        self.ax.pcolormesh(azs, tilts, est, cmap=cm, vmin=0, vmax=2, shading="auto")
        self.ax.set_title("torsión %.0f° · radio = inclinación (°), ángulo = azimut" % tor, fontsize=8)
        self._punto = None; self.canvas.draw(); self._hecho = True
        self.info.config(text="permitido: %.0f %% de las orientaciones del mapa\n%s" % (100 * (est == 2).mean(), "usa el mapa de colisiones del CAD" if app.lim.mapa is not None else "sin mapa de colisiones: cono de %.0f°" % app.lim.inclinacion_max_deg))

    def _clic(self, ev):
        if ev.inaxes is not self.ax or ev.xdata is None:
            return
        az, tl = math.degrees(ev.xdata) % 360, ev.ydata
        R = desde_inclinacion(*np.radians([tl, az, float(self.v_tor.get())]))
        try:
            th = self.app.spm.ik(R, self.app.theta)
        except FueraDeEspacioDeTrabajo:
            self.info.config(text="esa orientación no tiene solución", fg=tema.ERR); return
        if self._punto is not None: self._punto.remove()
        self._punto = self.ax.plot([ev.xdata], [ev.ydata], "o", color="white", ms=5)[0]; self.canvas.draw_idle()
        self.app.fijar_pose(R, th, origen="mapa"); self.app.ir_a("pose")

    def activar(self):
        if not self._hecho:
            self.calcular()
