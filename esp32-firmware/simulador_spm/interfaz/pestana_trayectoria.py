"""Pestaña 3 — Trayectoria: puntos de paso de orientación, tres modos de interpolación (como en el brazo 3DOF), gráficas y animación del CAD."""
import math
import time
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from spm import (desde_inclinacion, FueraDeEspacioDeTrabajo, CaminoSlerp, planificar, planificar_con_paradas, planificar_articular,
                 planificar_jacobiano, a_csv, desde_csv, expandir_esperas, programa_caja, programa_caja_reducida, programa_oscilacion_motores)
from . import tema
from .tema import PANEL, TEXT, MUTED, ACCENT, C_M
from .vista_cad import crear_vista

OSCILACIONES = {            # nombre: (motores 0-indexados, amplitud en ° de motor); modo articular: un solo motor se mueve a la vez, los otros en 0
    "Oscilación motores ±70°: M1 → M2 → M3": ((0, 1, 2), 70.0),
    "Oscilación M1 ±70° (M2 = M3 = 0)": ((0,), 70.0),
    "Oscilación M2 ±70° (M1 = M3 = 0)": ((1,), 70.0),
    "Oscilación M3 ±70° (M1 = M2 = 0)": ((2,), 70.0),
}
CAJA = "Caja: 4 caras (5 s) + volcado a la máxima inclinación"
CAJAS_REDUCIDAS = {          # nombre: (separación entre caras [°], inclinación máx. del volcado [°]); de menor a mayor recorrido, para subir poco a poco hasta el tope del mecanismo real
    "Caja reducida 1: caras cada 5°, volcado 4°": (5.0, 4.0),
    "Caja reducida 2: caras cada 10°, volcado 6°": (10.0, 6.0),
    "Caja reducida 3: caras cada 15°, volcado 8°": (15.0, 8.0),
    "Caja reducida 4: caras cada 30°, volcado 10°": (30.0, 10.0),
}
PRESETS = {
    "Precesión 12° (1 vuelta)": [[0, 0, 0, 1.0]] + [[12, a, 0, 1.2 if a == 0 else 0.9] for a in range(0, 361, 45)] + [[0, 0, 0, 1.2]],
    "Torsión ±20°": [[0, 0, 0, 1.0], [0, 0, 20, 1.5], [0, 0, -20, 3.0], [0, 0, 0, 1.5]],
    "Inclinación en 3 direcciones (12°)": [[0, 0, 0, 1.0], [12, 0, 0, 1.5], [0, 0, 0, 1.5], [12, 120, 0, 1.5], [0, 0, 0, 1.5], [12, 240, 0, 1.5], [0, 0, 0, 1.5]],
    "Estrella (10°)": [[0, 0, 0, 1.0]] + [[10, a, 0, 1.4] for a in (0, 144, 288, 72, 216, 0)] + [[0, 0, 0, 1.2]],
    "Inclinación + torsión": [[0, 0, 0, 1.0], [10, 30, 10, 2.0], [10, 150, -10, 2.0], [6, 270, 15, 2.0], [0, 0, 0, 2.0]],
}


class PestanaTrayectoria:
    nombre = "Trayectoria"

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=tema.BG)
        self.wps = [self._pad(w) for w in PRESETS["Precesión 12° (1 vuelta)"]]
        self.tray = None; self._anim = False; self._t_anim = 0.0; self._t0 = 0.0; self._cursores = []

        arriba = tk.Frame(self.frame, bg=tema.BG); arriba.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.vista = crear_vista(arriba, app, tam=(5.2, 5.2))
        self.vista.widget.pack(side="left", fill="both", expand=False, padx=(0, 6)); self.vista.widget.config(width=520, height=520)
        self.fig = Figure(figsize=(8.6, 5.4), facecolor=tema.BG)
        gs = self.fig.add_gridspec(3, 2, hspace=0.62, wspace=0.28, left=0.07, right=0.985, top=0.95, bottom=0.08)
        self.ax = {k: self.fig.add_subplot(gs[i // 2, i % 2]) for i, k in enumerate(("pos", "plat", "vel", "hol", "acc", "rec"))}
        self.canvas = FigureCanvasTkAgg(self.fig, master=arriba); self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True)
        self.canvas.get_tk_widget().config(bg=tema.BG)
        self._bg = None; self.canvas.mpl_connect("draw_event", self._al_dibujar)

        # ---------------- barra inferior de controles ----------------
        bajo = tk.Frame(self.frame, bg=PANEL, highlightthickness=1, highlightbackground=tema.BORDER); bajo.pack(fill="x", padx=8, pady=(0, 8))
        # tabla de puntos de paso
        tabla = tk.Frame(bajo, bg=PANEL); tabla.pack(side="left", padx=8, pady=6)
        self.tv = ttk.Treeview(tabla, columns=("n", "tilt", "az", "tor", "T", "E"), show="headings", height=6, style="SPM.Treeview")
        for c, t, w in (("n", "#", 28), ("tilt", "incl °", 56), ("az", "azim °", 56), ("tor", "tors °", 56), ("T", "T tramo s", 70), ("E", "espera s", 64)):
            self.tv.heading(c, text=t); self.tv.column(c, width=w, anchor="center")
        self.tv.pack(side="left"); sb = ttk.Scrollbar(tabla, command=self.tv.yview); sb.pack(side="left", fill="y"); self.tv.config(yscrollcommand=sb.set)
        self.tv.bind("<<TreeviewSelect>>", self._seleccion); self.tv.tag_configure("mal", foreground=tema.ERR)
        ed = tk.Frame(bajo, bg=PANEL); ed.pack(side="left", padx=4, pady=6)
        fila = tk.Frame(ed, bg=PANEL); fila.pack(anchor="w")
        self.e = {}
        for k, t, v in (("tilt", "incl", 0), ("az", "azim", 0), ("tor", "tors", 0), ("T", "T", 1.5), ("E", "espera", 0)):
            f, var = tema.campo(fila, t, v, 5); f.pack(side="left"); self.e[k] = var
        fb = tk.Frame(ed, bg=PANEL); fb.pack(anchor="w", pady=(4, 0))
        for t, c in (("+ Añadir", self._añadir), ("Actualizar", self._actualizar), ("Quitar", self._quitar), ("↑", lambda: self._mover(-1)), ("↓", lambda: self._mover(1)), ("Limpiar", self._limpiar)):
            tema.boton(fb, t, c).pack(side="left", padx=2)
        fp = tk.Frame(ed, bg=PANEL); fp.pack(anchor="w", pady=(6, 0))
        tema.etiqueta(fp, "Ejemplo:", MUTED).pack(side="left")
        self.cb_preset = ttk.Combobox(fp, values=list(PRESETS) + [CAJA] + list(CAJAS_REDUCIDAS) + list(OSCILACIONES), state="readonly", width=48, font=tema.FUENTE); self.cb_preset.set(list(PRESETS)[0]); self.cb_preset.pack(side="left", padx=4)
        tema.boton(fp, "Cargar", self._cargar_preset).pack(side="left")

        op = tk.Frame(bajo, bg=PANEL); op.pack(side="left", padx=14, pady=6)
        tema.etiqueta(op, "Interpolación", MUTED).pack(anchor="w")
        self.modo = tk.StringVar(value="slerp")
        for t, v in (("Orientación (SLERP)", "slerp"), ("Articular (motores)", "art"), ("Control cinemático (Jacobiano)", "jac")):
            tk.Radiobutton(op, text=t, variable=self.modo, value=v, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG, activebackground=PANEL, activeforeground=ACCENT, font=tema.FUENTE).pack(anchor="w")
        fo = tk.Frame(op, bg=PANEL); fo.pack(anchor="w", pady=(4, 0))
        self.paradas = tk.BooleanVar(value=True); self.ajustar = tk.BooleanVar(value=True)
        tk.Checkbutton(fo, text="parar en cada punto", variable=self.paradas, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG, activebackground=PANEL, font=tema.FUENTE).pack(anchor="w")
        tk.Checkbutton(fo, text="respetar límites de motor", variable=self.ajustar, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG, activebackground=PANEL, font=tema.FUENTE).pack(anchor="w")
        fk = tk.Frame(op, bg=PANEL); fk.pack(anchor="w", pady=(4, 0))
        f, self.k_gain = tema.campo(fk, "K", 10.0, 5); f.pack(side="left")
        tema.etiqueta(fk, "perfil", MUTED).pack(side="left")
        self.perfil = ttk.Combobox(fk, values=["quintico", "trapezoidal"], state="readonly", width=11, font=tema.FUENTE); self.perfil.set("quintico"); self.perfil.pack(side="left", padx=4)

        ac = tk.Frame(bajo, bg=PANEL); ac.pack(side="left", padx=10, pady=6, fill="y")
        r1 = tk.Frame(ac, bg=PANEL); r1.pack(anchor="w")
        tema.boton(r1, "Planificar", self.planificar, tema.BTN_HOV).pack(side="left", padx=2)
        tema.boton(r1, "▶ Animar", self.animar, tema.BTN_PLAY).pack(side="left", padx=2)
        tema.boton(r1, "⏸ Pausar", self.pausar, tema.BTN_PAUS).pack(side="left", padx=2)
        tema.boton(r1, "↺ Reset", self.reset, tema.BTN_RSET).pack(side="left", padx=2)
        r2 = tk.Frame(ac, bg=PANEL); r2.pack(anchor="w", pady=4)
        tema.boton(r2, "Importar CSV", self.importar).pack(side="left", padx=2)
        tema.boton(r2, "Exportar CSV", self.exportar).pack(side="left", padx=2)
        tema.boton(r2, "Transmitir al robot →", self.transmitir, tema.BTN_PLAY).pack(side="left", padx=2)
        r3 = tk.Frame(ac, bg=PANEL); r3.pack(anchor="w")
        f, self.vel_anim = tema.campo(r3, "velocidad anim ×", 1.0, 4); f.pack(side="left")
        self.info = tk.Label(ac, text="Sin planificar. Elige puntos de paso y pulsa Planificar.", bg=PANEL, fg=MUTED, font=tema.FUENTE, justify="left", anchor="w", wraplength=430)
        self.info.pack(anchor="w", pady=(4, 0), fill="x")
        self._llenar_tabla(); self._dibujar_vacio()

    # ------------------------------------------------------------------ tabla
    @staticmethod
    def _pad(w):
        return [float(x) for x in list(w)[:5]] + [0.0] * (5 - len(w))

    def _llenar_tabla(self):
        self.wps = [self._pad(w) for w in self.wps]                                   # admite filas de 4 valores (sin espera)
        self.tv.delete(*self.tv.get_children())
        for i, w in enumerate(self.wps):
            ok = self._wp_ok(w)
            self.tv.insert("", "end", iid=str(i), values=(i + 1, "%.1f" % w[0], "%.0f" % w[1], "%.1f" % w[2], "—" if i == 0 else "%.2f" % w[3], "%.1f" % w[4] if w[4] > 0 else "—"), tags=() if ok else ("mal",))

    def _wp_ok(self, w):
        R = desde_inclinacion(*np.radians(w[:3]))
        try:
            th = self.app.spm.ik(R)
        except FueraDeEspacioDeTrabajo:
            return False
        return self.app.estado(R, th)[0]

    def _leer(self):
        try:
            return [float(self.e[k].get().replace(",", ".")) for k in ("tilt", "az", "tor", "T", "E")]
        except ValueError:
            messagebox.showwarning("Dato inválido", "Escribe números en inclinación, azimut, torsión y T."); return None

    def _sel(self):
        s = self.tv.selection(); return int(s[0]) if s else None

    def _seleccion(self, _):
        i = self._sel()
        if i is not None:
            for k, x in zip(("tilt", "az", "tor", "T", "E"), self.wps[i]): self.e[k].set(x)
            R = desde_inclinacion(*np.radians(self.wps[i][:3]))
            try:
                th = self.app.spm.ik(R, self.app.theta); self.vista.mostrar(R, th, "punto %d" % (i + 1), rapido=True)
            except FueraDeEspacioDeTrabajo:
                pass

    def _añadir(self):
        w = self._leer()
        if w: self.wps.append(w); self._llenar_tabla()

    def _actualizar(self):
        i = self._sel(); w = self._leer()
        if i is not None and w: self.wps[i] = w; self._llenar_tabla(); self.tv.selection_set(str(i))

    def _quitar(self):
        i = self._sel()
        if i is not None and len(self.wps) > 2: self.wps.pop(i); self._llenar_tabla()

    def _mover(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.wps): return
        self.wps[i], self.wps[i + d] = self.wps[i + d], self.wps[i]; self._llenar_tabla(); self.tv.selection_set(str(i + d))

    def _limpiar(self):
        self.wps = [[0, 0, 0, 1.0, 0.0], [0, 0, 0, 1.0, 0.0]]; self._llenar_tabla()

    def _cargar_preset(self):
        nombre = self.cb_preset.get()
        if nombre == CAJA:
            filas, tmax = programa_caja(self.app.spm, self.app.lim)
            self.wps = [self._pad(w) for w in filas]
            self.modo.set("slerp"); self.paradas.set(True)
            self.info.config(text="Programa de la caja: 4 caras de 5 s y volcado a %.1f° (máximo permitido por el CAD con la torsión de la 4.ª cara, hacia azimut 0°; edita la fila 5 para otra dirección)." % tmax, fg=tema.OK)
        elif nombre in OSCILACIONES:
            motores, amp = OSCILACIONES[nombre]
            filas = programa_oscilacion_motores(self.app.spm, self.app.trans, amp, 3.0, motores)
            self.wps = [self._pad(w) for w in filas]
            self.modo.set("art"); self.paradas.set(True)
            self.info.config(text="Oscilación de motores: %s, cada uno 0 → +%.0f° → 0 → −%.0f° → 0 (° de motor) con los otros en 0, 3 s por tramo. Modo articular: planifica y mira la gráfica «Motores (°)»; los topes medidos (±84°, margen 5°) se comprueban al planificar." % (" → ".join("M%d" % (j + 1) for j in motores), amp, amp), fg=tema.OK)
        elif nombre in CAJAS_REDUCIDAS:
            paso, incl = CAJAS_REDUCIDAS[nombre]
            filas, tilt = programa_caja_reducida(self.app.spm, self.app.lim, paso, incl)
            self.wps = [self._pad(w) for w in filas]
            self.modo.set("slerp"); self.paradas.set(True)
            self.info.config(text="Caja reducida: caras cada %.0f° (torsión 0 a %.0f°), 5 s en cada una, y volcado a %.1f° hacia azimut 0°; luego vuelve a la pose inicial." % (paso, 3 * paso, tilt), fg=tema.OK)
        else:
            self.wps = [self._pad(w) for w in PRESETS[nombre]]
        self._llenar_tabla()

    # ------------------------------------------------------------------ planificación
    def planificar(self):
        app = self.app; self.pausar()
        if len(self.wps) < 2:
            messagebox.showwarning("Puntos de paso", "Se necesitan al menos 2 puntos de paso."); return None
        Rs = [desde_inclinacion(*np.radians(w[:3])) for w in self.wps]; Ts = [max(0.2, w[3]) for w in self.wps[1:]]
        perfil = self.perfil.get(); ajustar = self.ajustar.get(); modo = self.modo.get()
        esperas = [max(0.0, w[4]) for w in self.wps]
        if any(e > 0 for e in esperas) and (modo == "art" or (modo == "slerp" and self.paradas.get())):
            Rs, Ts = expandir_esperas(Rs, esperas, Ts)                       # cada espera es un tramo de duración 'espera' entre dos copias del punto
        elif any(e > 0 for e in esperas):
            self.info.config(text="Las esperas solo se aplican con «parar en cada punto» o en modo articular.", fg=tema.WARN)
        try:
            K = float(self.k_gain.get())
        except ValueError:
            K = 10.0
        self.info.config(text="Planificando…", fg=tema.WARN); self.frame.update_idletasks()
        try:
            t0 = time.time()
            if modo == "slerp":
                if self.paradas.get():
                    tr = planificar_con_paradas(app.spm, app.trans, app.lim, Rs, Ts, 0.02, perfil, ajustar_tiempo=ajustar)
                else:
                    tr = planificar(app.spm, app.trans, app.lim, CaminoSlerp(Rs), sum(Ts), 0.02, perfil, ajustar_tiempo=ajustar)
            elif modo == "art":
                tr = planificar_articular(app.spm, app.trans, app.lim, Rs, Ts, 0.02, perfil, ajustar_tiempo=ajustar)
            else:
                tr = planificar_jacobiano(app.spm, app.trans, app.lim, CaminoSlerp(Rs), sum(Ts), 0.02, perfil, K)
        except FueraDeEspacioDeTrabajo as ex:
            self.info.config(text="No se pudo planificar: %s" % ex, fg=tema.ERR); return None
        self.tray = tr; app.tray = tr; self._t_anim = 0.0
        self._dibujar(); self._mostrar_k(0)
        vmax = np.abs(np.degrees(tr.motor_vel)).max() / 6.0
        malos = len(tr.violaciones)
        txt = "%s · %.2f s%s · %d muestras · pico %.1f rpm" % ({"slerp": "SLERP", "art": "articular", "jac": "Jacobiano"}[modo], tr.duracion,
                                                              " (alargada ×%.2f)" % tr.factor_tiempo if tr.factor_tiempo > 1.01 else "", len(tr.t), vmax)
        if malos:
            txt += "\n⚠ %d muestras fuera de límites. Primera: t=%.2f s — %s" % (malos, tr.t[tr.violaciones[0][0]], tr.violaciones[0][1])
        else:
            txt += "\n✔ todo el recorrido dentro de límites (colisión, singularidad)"
        self.info.config(text=txt, fg=tema.WARN if malos else tema.OK)
        return tr

    # ------------------------------------------------------------------ gráficas
    def _dibujar_vacio(self):
        for k, a in self.ax.items():
            a.clear(); a.set_facecolor(PANEL)
        self.ax["pos"].set_title("Motores (°)", fontsize=8); self.canvas.draw_idle()

    def _dibujar(self):
        tr, lim = self.tray, self.app.lim; t = tr.t
        for a in self.ax.values(): a.clear()
        ax = self.ax; self._cursores = []
        for j in range(3):
            ax["pos"].plot(t, np.degrees(tr.motor[:, j]), color=C_M[j], lw=1.2, label="M%d" % (j + 1))
            ax["vel"].plot(t, np.degrees(tr.motor_vel[:, j]), color=C_M[j], lw=1.0)
            ax["acc"].plot(t, np.degrees(tr.motor_acc[:, j]), color=C_M[j], lw=1.0)
        vl, al = math.degrees(lim.vel_motor_max), math.degrees(lim.acc_motor_max)
        for a, v in ((ax["vel"], vl), (ax["acc"], al)):
            a.axhline(v, color=tema.ERR, lw=0.7, ls="--"); a.axhline(-v, color=tema.ERR, lw=0.7, ls="--")
        ax["plat"].plot(t, np.degrees(tr.inclinacion), color=tema.ACCENT, lw=1.2, label="inclinación")
        ax["plat"].plot(t, np.degrees(tr.torsion), color=tema.MUTED, lw=1.2, label="torsión")
        if lim.mapa is not None:
            h = np.array([lim.mapa.consultar(math.degrees(i), math.degrees(a), math.degrees(s))[0] for i, a, s in zip(tr.inclinacion, tr.azimut, tr.torsion)])
            ax["hol"].plot(t, h, color=tema.OK, lw=1.2); ax["hol"].axhline(lim.holgura_min_mm, color=tema.ERR, lw=0.8, ls="--"); ax["hol"].set_ylim(0, max(10, np.nanmax(h) * 1.05))
            ax["hol"].set_title("Holgura de colisión CAD (mm)", fontsize=8)
        else:
            ax["hol"].set_title("(sin mapa de colisiones)", fontsize=8)
        ang = np.array([self._ang(tr.R[max(0, i - 1)], tr.R[i]) for i in range(len(t))]); ax["rec"].plot(t, np.cumsum(ang), color=tema.BTN_HOV, lw=1.2)
        for k, tt in (("pos", "Motores (°)"), ("plat", "Plataforma (°)"), ("vel", "Velocidad motor (°/s)"), ("acc", "Aceleración motor (°/s²)"), ("rec", "Ángulo recorrido (°)")):
            ax[k].set_title(tt, fontsize=8)
        ax["pos"].legend(loc="upper right", fontsize=6, ncol=3); ax["plat"].legend(loc="upper right", fontsize=6, ncol=2)
        for i, _m in tr.violaciones:                                                  # franjas rojas donde hay violaciones
            for k in ("pos", "plat"):
                ax[k].axvspan(t[i] - tr.dt / 2, t[i] + tr.dt / 2, color=tema.ERR, alpha=0.25, lw=0)
        for a in ax.values():
            a.set_xlim(0, t[-1]); self._cursores.append(a.axvline(0, color=tema.WARN, lw=1.0, animated=True))
        ax["rec"].set_xlabel("t (s)", fontsize=7); ax["acc"].set_xlabel("t (s)", fontsize=7)
        self._bg = None; self.canvas.draw()

    @staticmethod
    def _ang(R1, R2):
        from spm.rot import angulo_entre
        return math.degrees(angulo_entre(R1, R2))

    # ------------------------------------------------------------------ animación
    def _al_dibujar(self, _ev):
        """Tras cada redibujado completo: guarda el fondo de las gráficas para mover solo el cursor (blit), sin redibujar todo en cada fotograma."""
        self._bg = self.canvas.copy_from_bbox(self.fig.bbox)
        for c in self._cursores: c.axes.draw_artist(c)

    def _mover_cursores(self, t):
        for c in self._cursores: c.set_xdata([t, t])
        if self._bg is None:
            self.canvas.draw_idle(); return
        self.canvas.restore_region(self._bg)
        for c in self._cursores: c.axes.draw_artist(c)
        self.canvas.blit(self.fig.bbox)

    def _mostrar_k(self, k):
        tr = self.tray
        self._mover_cursores(tr.t[k])
        m = np.degrees(tr.motor[k])
        ok = not any(i == k for i, _ in tr.violaciones)
        self.app.fijar_pose(tr.R[k], tr.theta[k], origen="trayectoria", refrescar_pose=False)
        self.vista.mostrar(tr.R[k], tr.theta[k], "t = %.2f / %.2f s%s\nincl %.1f°  az %.0f°  tors %.1f°\nmotores %+.1f° %+.1f° %+.1f°" % (
            tr.t[k], tr.duracion, "" if ok else "   ⚠ FUERA DE LÍMITES", math.degrees(tr.inclinacion[k]), math.degrees(tr.azimut[k]) % 360, math.degrees(tr.torsion[k]), *m),
            rapido=True, hq_en_reposo=not self._anim)

    def animar(self):
        if self.tray is None and self.planificar() is None:
            return
        if self._anim: return
        if self._t_anim >= self.tray.duracion - 1e-6: self._t_anim = 0.0
        try:
            self._vel = max(0.05, float(self.vel_anim.get().replace(",", ".")))
        except ValueError:
            self._vel = 1.0
        self._anim = True; self._t0 = time.monotonic() - self._t_anim / self._vel; self._tick()

    def _tick(self):
        if not self._anim: return
        tr = self.tray; self._t_anim = min((time.monotonic() - self._t0) * self._vel, tr.duracion)
        k = int(min(np.searchsorted(tr.t, self._t_anim), len(tr.t) - 1)); self._mostrar_k(k)
        if self._t_anim >= tr.duracion:
            self._anim = False; self.vista._programar_hq(); return
        self.frame.after(5, self._tick)

    def pausar(self):
        if self._anim:
            self._anim = False; self.vista._programar_hq()

    def reset(self):
        self.pausar(); self._t_anim = 0.0
        if self.tray is not None: self._mostrar_k(0)

    # ------------------------------------------------------------------ archivos / robot
    def importar(self):
        f = filedialog.askopenfilename(title="Trayectoria CSV (t, m1, m2, m3 en grados de motor)", filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if not f: return
        try:
            tr = desde_csv(self.app.spm, self.app.trans, self.app.lim, f)
        except Exception as ex:
            messagebox.showerror("No se pudo importar", str(ex)); return
        self.tray = tr; self.app.tray = tr; self._t_anim = 0.0; self._dibujar(); self._mostrar_k(0)
        self.info.config(text="CSV importado: %d muestras, %.2f s, %d fuera de límites" % (len(tr.t), tr.duracion, len(tr.violaciones)), fg=tema.OK if not tr.violaciones else tema.WARN)

    def exportar(self):
        if self.tray is None and self.planificar() is None: return
        f = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="trayectoria_spm.csv")
        if f:
            a_csv(self.tray, f); self.info.config(text="Exportado: %s" % f, fg=tema.OK)

    def transmitir(self):
        if self.tray is None and self.planificar() is None: return
        self.app.pestanas["serial"].transmitir(self.tray)

    def activar(self):
        if self.tray is not None:
            self._mostrar_k(min(int(np.searchsorted(self.tray.t, self._t_anim)), len(self.tray.t) - 1))
        else:
            self.vista.mostrar(self.app.R, self.app.theta, "")
