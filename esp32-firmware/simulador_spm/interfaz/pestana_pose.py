"""Pestaña 1 — Vista 3D del CAD: orientación ⇄ motores (IK / FK) con el modelo CAD movido en vivo."""
import math
import numpy as np
import tkinter as tk

from spm import desde_inclinacion, a_inclinacion, FueraDeEspacioDeTrabajo
from spm.modelo3d import GRUPOS
from . import tema
from .tema import PANEL, TEXT, MUTED, ACCENT, C_M
from .vista_cad import crear_vista, VISTAS


class PestanaPose:
    nombre = "Vista 3D CAD"

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=tema.BG)
        izq = tk.Frame(self.frame, bg=PANEL, width=380, highlightthickness=1, highlightbackground=tema.BORDER)
        izq.pack(side="left", fill="y", padx=(8, 4), pady=8); izq.pack_propagate(False)
        self.vista = crear_vista(self.frame, app, tam=(8, 7))
        self.vista.widget.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)
        self._act = False

        tema.etiqueta(izq, "MODO", tema.MUTED).pack(anchor="w", padx=10, pady=(8, 0))
        self.modo = tk.StringVar(value="ik")
        fm = tk.Frame(izq, bg=PANEL); fm.pack(fill="x", padx=10)
        for txt, val in (("Orientación → motores (IK)", "ik"), ("Motores → orientación (FK)", "fk")):
            tk.Radiobutton(fm, text=txt, variable=self.modo, value=val, command=self._cambio_modo, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG,
                           activebackground=PANEL, activeforeground=ACCENT, font=tema.FUENTE).pack(anchor="w")

        tema.etiqueta(izq, "ORIENTACIÓN DE LA PLATAFORMA", tema.MUTED).pack(anchor="w", padx=10, pady=(10, 0))
        self.sl = {}
        for clave, txt, lo, hi, col in (("tilt", "inclinación (°)", -30, 30, tema.BTN_HOV), ("az", "azimut (°)", -360, 360, tema.BTN_HOV), ("tor", "torsión (°)", -90, 90, tema.BTN_HOV)):
            f, v, s, lv = tema.deslizador(izq, txt, lo, hi, 0.0, lambda x, c=clave: self._on_orient(), 0.1, col)
            f.pack(fill="x", padx=10, pady=1); self.sl[clave] = (v, s, lv)

        tema.etiqueta(izq, "MOTORES (eje de salida)", tema.MUTED).pack(anchor="w", padx=10, pady=(10, 0))
        self.sm = []
        for j in range(3):
            f, v, s, lv = tema.deslizador(izq, "motor %d (°)" % (j + 1), -250, 250, 0.0, lambda x, j=j: self._on_motor(), 0.1, C_M[j])
            f.pack(fill="x", padx=10, pady=1); self.sm.append((v, s, lv))

        self.lbl = {}
        caja = tk.Frame(izq, bg=tema.BG, highlightthickness=1, highlightbackground=tema.BORDER); caja.pack(fill="x", padx=10, pady=(10, 4))
        for k, t in (("nnr", "NNR   (°)"), ("normal", "normal"), ("sing", "singularidad"), ("estado", "estado")):
            fila = tk.Frame(caja, bg=tema.BG); fila.pack(fill="x", padx=6, pady=1)
            tk.Label(fila, text=t, width=13, anchor="w", bg=tema.BG, fg=MUTED, font=tema.FUENTE).pack(side="left")
            self.lbl[k] = tk.Label(fila, text="—", anchor="w", justify="left", bg=tema.BG, fg=TEXT, font=tema.FUENTE, wraplength=250)
            self.lbl[k].pack(side="left", fill="x", expand=True)

        tema.etiqueta(izq, "MOSTRAR", tema.MUTED).pack(anchor="w", padx=10, pady=(6, 0))
        fv = tk.Frame(izq, bg=PANEL); fv.pack(fill="x", padx=10)
        self._vis = {}
        for i, (g, txt) in enumerate(GRUPOS.items()):
            var = tk.BooleanVar(value=True); self._vis[g] = var
            tk.Checkbutton(fv, text=txt, variable=var, command=lambda g=g: self.vista.ocultar_grupo(g, not self._vis[g].get()), bg=PANEL, fg=TEXT,
                           selectcolor=tema.BTN_BG, activebackground=PANEL, activeforeground=ACCENT, font=tema.FUENTE).grid(row=i // 2, column=i % 2, sticky="w")
        fb = tk.Frame(izq, bg=PANEL); fb.pack(fill="x", padx=10, pady=(8, 2))
        self.cb = __import__("tkinter.ttk", fromlist=["Combobox"]).Combobox(fb, values=list(VISTAS), state="readonly", width=12, font=tema.FUENTE)
        self.cb.set("Isométrica"); self.cb.pack(side="left"); self.cb.bind("<<ComboboxSelected>>", lambda e: self.vista.poner_vista(self.cb.get()))
        tema.boton(fb, "Pose inicial (plato horizontal)", self.reset, tema.BTN_RSET).pack(side="right")
        tema.boton(izq, "Enviar al robot →", self.enviar, tema.BTN_PLAY).pack(fill="x", padx=10, pady=(6, 8))
        self._cambio_modo()
        self.refrescar()

    # ------------------------------------------------------------------ estado
    def _cambio_modo(self):
        ik = self.modo.get() == "ik"
        for v, s, lv in self.sl.values(): s.config(state="normal" if ik else "disabled")
        for v, s, lv in self.sm: s.config(state="disabled" if ik else "normal")

    def _on_orient(self):
        if self._act or self.modo.get() != "ik":
            return
        t, a, s = (self.sl[k][0].get() for k in ("tilt", "az", "tor"))
        R = desde_inclinacion(*np.radians([t, a, s]))
        try:
            th = self.app.spm.ik(R, self.app.theta)
            if np.abs(th - self.app.theta).max() > math.radians(60):          # salto brusco del deslizador: ¿otra rama de la IK? se prefiere la más cercana a la pose inicial
                th0 = self.app.spm.ik(R)
                if np.abs(th0).max() < np.abs(th).max(): th = th0
        except FueraDeEspacioDeTrabajo as e:
            self.lbl["estado"].config(text="sin solución: %s" % e, fg=tema.ERR); return
        self._mantener = True                                  # no reescribir los deslizadores que el usuario está moviendo (inclinación o azimut negativos)
        try:
            self.app.fijar_pose(R, th, origen="pose")
        finally:
            self._mantener = False

    def _on_motor(self):
        if self._act or self.modo.get() != "fk":
            return
        m = np.radians([self.sm[j][0].get() for j in range(3)])
        try:
            th = self.app.trans.a_nnr(m); R = self.app.spm.fk(th, self.app.R)
        except FueraDeEspacioDeTrabajo as e:
            self.lbl["estado"].config(text="FK sin solución: %s" % e, fg=tema.ERR); return
        self.app.fijar_pose(R, th, origen="pose")

    def reset(self):
        self.app.fijar_pose(np.eye(3), np.zeros(3), origen="pose")

    def enviar(self):
        self.app.pestanas["serial"].enviar_pose(self.app.trans.a_motor(self.app.theta))

    # ------------------------------------------------------------------ refresco
    def refrescar(self, rapido=True):
        """Actualiza deslizadores, etiquetas y la vista a partir de app.R / app.theta."""
        app = self.app
        self._act = True
        try:
            t, a, s = (math.degrees(x) for x in a_inclinacion(app.R))
            if not getattr(self, "_mantener", False):
                for k, val in (("tilt", t), ("az", a % 360), ("tor", s)):
                    tema.poner_deslizador(*self.sl[k], val)
            m = np.degrees(app.trans.a_motor(app.theta))
            for j in range(3):
                tema.poner_deslizador(*self.sm[j], float(np.clip(m[j], -250, 250)))
        finally:
            self._act = False
        ok, motivo, h, sg = app.estado(app.R, app.theta)
        self.lbl["nnr"].config(text="  ".join("%+7.2f" % x for x in np.degrees(app.theta)))
        self.lbl["normal"].config(text="(%+.3f, %+.3f, %+.3f)" % tuple(app.R[:, 2]))
        self.lbl["sing"].config(text="σ=%.2f  B=%.2f" % sg)
        self.lbl["estado"].config(text=("dentro de límites" + ("  · holgura %.1f mm" % h if h is not None and not math.isnan(h) else "")) if ok else "FUERA: " + motivo,
                                  fg=tema.OK if ok else tema.ERR)
        self.vista.mostrar(app.R, app.theta, "incl %.1f°  az %.0f°  tors %.1f°\nmotores %s°" % (t, a % 360, s, "  ".join("%+.1f" % x for x in m)), rapido=rapido)

    def activar(self):
        self.refrescar()
