"""Pestaña 2 — Motores + control: simulación del lazo de seguimiento de la trayectoria planificada (motor simulado + encoder + FK)."""
import math
import numpy as np
import tkinter as tk
from tkinter import messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from spm import seguir_trayectoria, MotoresSimulados
from spm.transmision import guardar_config
from . import tema
from .tema import PANEL, TEXT, MUTED, C_M


class PestanaControl:
    nombre = "Motores + control"

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=tema.BG)
        izq = tk.Frame(self.frame, bg=PANEL, width=330, highlightthickness=1, highlightbackground=tema.BORDER); izq.pack(side="left", fill="y", padx=(8, 4), pady=8); izq.pack_propagate(False)
        tema.etiqueta(izq, "LAZO DE SEGUIMIENTO (cinemático)", MUTED).pack(anchor="w", padx=10, pady=(10, 4))
        tema.etiqueta(izq, "Trayectoria de orientación → IK → consigna de motor\n(avance de velocidad + P) → motor simulado → encoder → FK", MUTED, justify="left").pack(anchor="w", padx=10)
        t, l = app.trans, app.lim
        self.v = {}
        for k, txt, val in (("kp", "Kp (1/s)", 15.0), ("tau", "constante de tiempo motor τ (s)", 0.05), ("vmax", "velocidad máx motor (rpm)", round(l.vel_motor_max * 60 / (2 * math.pi), 1)),
                            ("amax", "aceleración máx (rad/s²)", l.acc_motor_max), ("cpr", "cuentas/vuelta del encoder (salida)", t.cuentas_por_vuelta_salida),
                            ("pert", "desvío constante en M1 (°/s)", 0.0)):
            f, var = tema.campo(izq, txt, val, 8); f.pack(anchor="w", padx=10, pady=2); self.v[k] = var
        self.ff = tk.BooleanVar(value=True)
        tk.Checkbutton(izq, text="avance de velocidad (feed-forward)", variable=self.ff, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG, activebackground=PANEL, font=tema.FUENTE).pack(anchor="w", padx=10)
        tema.boton(izq, "Simular seguimiento", self.simular, tema.BTN_PLAY).pack(fill="x", padx=10, pady=(10, 2))
        tema.boton(izq, "Aplicar límites a todo el programa", self.aplicar).pack(fill="x", padx=10, pady=2)
        tema.boton(izq, "Guardar en datos/config.json", self.guardar).pack(fill="x", padx=10, pady=2)
        self.res = tk.Label(izq, text="Planifica una trayectoria en la pestaña «Trayectoria» y pulsa Simular.", bg=PANEL, fg=MUTED, font=tema.FUENTE, justify="left", anchor="w", wraplength=300)
        self.res.pack(anchor="w", padx=10, pady=10, fill="x")
        self.fig = Figure(figsize=(9, 6), facecolor=tema.BG)
        gs = self.fig.add_gridspec(3, 1, hspace=0.5, left=0.08, right=0.985, top=0.95, bottom=0.08)
        self.ax = [self.fig.add_subplot(gs[i]) for i in range(3)]
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame); self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)

    def _num(self, k, d):
        try:
            return float(self.v[k].get().replace(",", "."))
        except ValueError:
            return d

    def aplicar(self):
        self.app.lim.vel_motor_max = self._num("vmax", 60) * 2 * math.pi / 60
        self.app.lim.acc_motor_max = self._num("amax", 30)
        self.app.trans.cuentas_por_vuelta_salida = self._num("cpr", 1000)
        self.res.config(text="Límites aplicados (se usarán en la próxima planificación).", fg=tema.OK)

    def guardar(self):
        self.aplicar(); guardar_config(self.app.trans, self.app.lim); self.res.config(text="Guardado en datos/config.json", fg=tema.OK)

    def simular(self):
        app = self.app; tr = app.tray
        if tr is None:
            messagebox.showinfo("Sin trayectoria", "Primero planifica o importa una trayectoria."); return
        self.aplicar()
        mot = MotoresSimulados(app.trans, app.lim, pos0=tr.motor[0], tau=max(0.0, self._num("tau", 0.05)), perturbacion_vel=(math.radians(self._num("pert", 0.0)), 0, 0))
        try:
            reg = seguir_trayectoria(app.spm, app.trans, app.lim, tr, mot, kp=self._num("kp", 15), ff=self.ff.get())
        except Exception as ex:
            self.res.config(text="Falló la simulación: %s" % ex, fg=tema.ERR); return
        for a in self.ax: a.clear()
        for j in range(3):
            self.ax[0].plot(reg.t, np.degrees(reg.motor_ref[:, j]), color=C_M[j], lw=1.2, label="M%d ref" % (j + 1))
            self.ax[0].plot(reg.t, np.degrees(reg.motor_med[:, j]), color=C_M[j], lw=1.0, ls="--")
            self.ax[1].plot(reg.t, np.degrees(reg.motor_ref[:, j] - reg.motor_med[:, j]), color=C_M[j], lw=1.0)
        self.ax[2].plot(reg.t, reg.error_deg, color=tema.WARN, lw=1.2)
        for a, t in zip(self.ax, ("Ángulo de motor: referencia (continua) vs encoder (guiones) (°)", "Error de motor (°)", "Error de orientación de la plataforma, estimada con FK (°)")):
            a.set_title(t, fontsize=8); a.set_xlim(0, reg.t[-1])
        self.ax[0].legend(ncol=3, fontsize=6, loc="upper right"); self.ax[2].set_xlabel("t (s)", fontsize=7)
        self.canvas.draw()
        self.res.config(text="error de orientación: máx %.2f°, medio %.2f°\nerror de motor máx: %.2f°" % (reg.error_deg.max(), reg.error_deg.mean(), np.degrees(np.abs(reg.motor_ref - reg.motor_med)).max()), fg=tema.OK)

    def activar(self):
        pass
