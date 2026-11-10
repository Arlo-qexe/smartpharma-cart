"""Widget que muestra el modelo CAD del SPM dentro de Tkinter (matplotlib TkAgg + rasterizador de spm.modelo3d).

- Arrastrar con el botón izquierdo: gira la cámara. Rueda: zoom. Doble clic: restablece la vista.
- Mientras se interactúa o se anima se usa la malla ligera (LOD); al quedar quieto se redibuja con la malla completa.
"""
import math
import numpy as np
import tkinter as tk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from . import tema

VISTAS = {"Isométrica": (-55, 22), "Frontal": (-90, 0), "Lateral": (0, 0), "Superior": (-90, 89), "Posterior": (90, 12)}


class VistaCAD:
    def __init__(self, parent, app, tam=(6.0, 6.0), texto_inicial=""):
        self.app = app
        self.fig = Figure(figsize=tam, facecolor=tema.BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1]); self.ax.set_facecolor(tema.BG); self.ax.axis("off"); self.ax.set_aspect("equal")
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.widget = self.canvas.get_tk_widget(); self.widget.config(bg=tema.BG, highlightthickness=1, highlightbackground=tema.BORDER)
        self.azim, self.elev, self.zoom = -55.0, 22.0, 1.0
        self.R, self.theta = np.eye(3), np.zeros(3)
        self.grupos_ocultos = set(); self.ocultar_piezas = ()
        self._txt = self.ax.text(0.015, 0.985, texto_inicial, transform=self.ax.transAxes, va="top", ha="left", fontsize=8, color=tema.TEXT, family="monospace")
        self._ayuda = self.ax.text(0.985, 0.015, "arrastrar: girar · rueda: zoom · doble clic: restablecer", transform=self.ax.transAxes, va="bottom", ha="right",
                                   fontsize=7, color=tema.MUTED, family="monospace")
        self._drag = None; self._job_hq = None; self._hq_auto = True
        self.canvas.mpl_connect("button_press_event", self._press)
        self.canvas.mpl_connect("motion_notify_event", self._move)
        self.canvas.mpl_connect("button_release_event", self._release)
        self.canvas.mpl_connect("scroll_event", self._scroll)

    # ------------------------------------------------------------------ API
    def mostrar(self, R, theta, texto=None, rapido=True, hq_en_reposo=True):
        self.R, self.theta = R, theta
        if texto is not None:
            self._txt.set_text(texto)
        self._dibujar(hq=not rapido)
        if rapido and hq_en_reposo and self._hq_auto:
            self._programar_hq()

    def poner_vista(self, nombre):
        self.azim, self.elev = VISTAS[nombre]; self.zoom = 1.0
        self._dibujar(False); self._programar_hq()

    def ocultar_grupo(self, grupo, oculto):
        (self.grupos_ocultos.add if oculto else self.grupos_ocultos.discard)(grupo)
        self._dibujar(False); self._programar_hq()

    def guardar_png(self, archivo, dpi=110):
        self._dibujar(hq=True); self.fig.savefig(archivo, dpi=dpi, facecolor=tema.BG)

    # ------------------------------------------------------------------ dibujo
    def _limites(self, m):
        x0, x1, y0, y1 = m.limites_vista(round(self.azim), round(self.elev))
        cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 / self.zoom
        self.ax.set_xlim(cx - r, cx + r); self.ax.set_ylim(cy - r, cy + r)

    def _dibujar(self, hq):
        m = self.app.modelo_hq if hq else self.app.modelo_lod
        self._limites(m)
        m.dibujar(self.R, self.theta, self.ax, self.azim, self.elev, grupos_ocultos=self.grupos_ocultos, ocultar=self.ocultar_piezas)
        self.canvas.draw_idle() if not hq else self.canvas.draw()

    def _programar_hq(self):
        if self._job_hq is not None:
            self.widget.after_cancel(self._job_hq)
        self._job_hq = self.widget.after(450, lambda: self._dibujar(True))

    # ------------------------------------------------------------------ ratón
    def _press(self, ev):
        if ev.dblclick:
            self.azim, self.elev, self.zoom = -55.0, 22.0, 1.0
            self._dibujar(False); self._programar_hq(); return
        if ev.button == 1 and ev.x is not None:
            self._drag = (ev.x, ev.y, self.azim, self.elev)

    def _move(self, ev):
        if self._drag is None or ev.x is None:
            return
        x0, y0, a0, e0 = self._drag
        self.azim = a0 - (ev.x - x0) * 0.5
        self.elev = float(np.clip(e0 + (ev.y - y0) * 0.5, -89, 89))
        self._dibujar(False)

    def _release(self, ev):
        if self._drag is not None:
            self._drag = None; self._programar_hq()

    def _scroll(self, ev):
        self.zoom = float(np.clip(self.zoom * (1.15 if ev.button == "up" else 1 / 1.15), 0.4, 4.0))
        self._dibujar(False); self._programar_hq()


# ======================================================================================================================
#  Vista por GPU (OpenGL sin ventana → imagen en un Canvas de Tk). Malla fina del CAD, z-buffer real, ~8 ms por fotograma.
# ======================================================================================================================
class VistaCADGL:
    """Misma interfaz que VistaCAD. Izquierdo: girar · derecho/central: desplazar · rueda: zoom · doble clic: restablecer."""

    def __init__(self, parent, app, tam=(6.0, 6.0), texto_inicial=""):
        from PIL import ImageTk
        self._ImageTk = ImageTk
        self.app = app
        self.canvas = tk.Canvas(parent, bg=tema.BG, highlightthickness=1, highlightbackground=tema.BORDER, width=int(tam[0] * 90), height=int(tam[1] * 90))
        self.widget = self.canvas
        self.azim, self.elev, self.zoom, self.pan = -55.0, 22.0, 1.0, [0.0, 0.0]
        self.R, self.theta = np.eye(3), np.zeros(3)
        self.grupos_ocultos = set(); self.ocultar_piezas = ()
        self._img = self.canvas.create_image(0, 0, anchor="nw")
        self._txt = self.canvas.create_text(10, 8, anchor="nw", text=texto_inicial, fill=tema.TEXT, font=(tema.FAMILIA, 9))
        self.canvas.create_text(10, 10, anchor="sw", text="", tags="ayuda")
        self._ayuda = self.canvas.create_text(0, 0, anchor="se", text="izq: girar · der: desplazar · rueda: zoom · doble clic: restablecer", fill=tema.MUTED, font=(tema.FAMILIA, 8))
        self._foto = None; self._drag = None; self._pendiente = False
        for ev, fn in (("<ButtonPress-1>", self._press), ("<ButtonPress-2>", self._press), ("<ButtonPress-3>", self._press), ("<B1-Motion>", self._move),
                       ("<B2-Motion>", self._move), ("<B3-Motion>", self._move), ("<Double-Button-1>", self._reset), ("<MouseWheel>", self._rueda),
                       ("<Button-4>", self._rueda), ("<Button-5>", self._rueda), ("<Configure>", lambda e: self._pedir())):
            self.canvas.bind(ev, fn)

    # ------------------------------------------------------------------ API compatible con VistaCAD
    def mostrar(self, R, theta, texto=None, rapido=True, hq_en_reposo=True):
        self.R, self.theta = R, theta
        if texto is not None:
            self.canvas.itemconfig(self._txt, text=texto)
        self._dibujar()

    def poner_vista(self, nombre):
        self.azim, self.elev = VISTAS[nombre]; self.zoom = 1.0; self.pan = [0.0, 0.0]; self._dibujar()

    def ocultar_grupo(self, grupo, oculto):
        (self.grupos_ocultos.add if oculto else self.grupos_ocultos.discard)(grupo); self._dibujar()

    def _programar_hq(self):
        pass                                                           # la GPU siempre dibuja a calidad completa

    def guardar_png(self, archivo, dpi=110):
        w, h = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        self.app.render_gl.guardar_png(self.R, self.theta, archivo, azim=self.azim, elev=self.elev, zoom=self.zoom, pan=tuple(self.pan), ancho=w, alto=h,
                                       grupos_ocultos=self.grupos_ocultos, ocultar=self.ocultar_piezas)

    # ------------------------------------------------------------------ dibujo
    def _pedir(self):
        """Agrupa varias peticiones (arrastre, redimensionado) en un solo dibujo por turno del bucle de eventos."""
        if not self._pendiente:
            self._pendiente = True; self.canvas.after_idle(self._dibujar)

    def _dibujar(self):
        self._pendiente = False
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 50 or h < 50:
            w, h = int(self.canvas.cget("width")), int(self.canvas.cget("height"))
        from PIL import Image
        img = self.app.render_gl.renderizar(self.R, self.theta, self.azim, self.elev, self.zoom, tuple(self.pan), w, h, self.grupos_ocultos, self.ocultar_piezas)
        self._foto = self._ImageTk.PhotoImage(Image.fromarray(img))
        self.canvas.itemconfig(self._img, image=self._foto); self.canvas.coords(self._ayuda, w - 8, h - 6)

    # ------------------------------------------------------------------ ratón
    def _press(self, ev):
        self._drag = (ev.x, ev.y, self.azim, self.elev, list(self.pan), ev.num)

    def _move(self, ev):
        if self._drag is None:
            return
        x0, y0, a0, e0, p0, boton = self._drag
        if boton == 1:
            self.azim = a0 - (ev.x - x0) * 0.4
            self.elev = float(np.clip(e0 + (ev.y - y0) * 0.4, -89, 89))
        else:
            k = 2.0 / max(200, self.canvas.winfo_height()) / self.zoom
            self.pan = [p0[0] - (ev.x - x0) * k, p0[1] + (ev.y - y0) * k]
        self._pedir()

    def _rueda(self, ev):
        arriba = ev.num == 4 or getattr(ev, "delta", 0) > 0
        self.zoom = float(np.clip(self.zoom * (1.12 if arriba else 1 / 1.12), 0.4, 6.0)); self._pedir()

    def _reset(self, ev=None):
        self.azim, self.elev, self.zoom, self.pan = -55.0, 22.0, 1.0, [0.0, 0.0]; self._pedir()


def crear_vista(parent, app, tam=(6.0, 6.0), texto_inicial=""):
    """Vista por GPU si el render OpenGL está disponible; si no, la de matplotlib (por software)."""
    if getattr(app, "render_gl", None) is not None:
        return VistaCADGL(parent, app, tam, texto_inicial)
    return VistaCAD(parent, app, tam, texto_inicial)
