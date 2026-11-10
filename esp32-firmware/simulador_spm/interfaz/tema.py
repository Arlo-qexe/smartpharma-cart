"""Tema visual (el mismo del brazo 3DOF: paleta oscura tipo GitHub) y utilidades de widgets."""
import tkinter as tk
import matplotlib

BG, PANEL, BORDER, TEXT, ACCENT, MUTED = "#0d1117", "#161b22", "#30363d", "#c9d1d9", "#f0f6fc", "#8b949e"
BTN_BG, BTN_HOV, BTN_PLAY, BTN_PAUS, BTN_RSET, BTN_DIS = "#21262d", "#388bfd", "#238636", "#9e6a03", "#6e4091", "#1c2128"
OK, WARN, ERR = "#3fb950", "#d29922", "#f85149"
C_M = ["#58a6ff", "#3fb950", "#f78166"]                 # motores 1, 2, 3
import sys
FAMILIA = "Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"
FUENTE = (FAMILIA, 9)
FUENTE_B = (FAMILIA, 9, "bold")
FUENTE_T = (FAMILIA, 12, "bold")


def aplicar_tema_mpl():
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": PANEL, "axes.edgecolor": BORDER, "axes.labelcolor": TEXT, "axes.titlecolor": ACCENT,
        "axes.grid": True, "grid.color": "#21262d", "grid.linewidth": 0.7, "xtick.color": MUTED, "ytick.color": MUTED, "text.color": TEXT,
        "font.family": "monospace", "font.size": 8, "legend.facecolor": PANEL, "legend.edgecolor": BORDER,
    })


def boton(parent, texto, comando, bg=BTN_BG, ancho=None, **kw):
    b = tk.Button(parent, text=texto, command=comando, bg=bg, fg=ACCENT, activebackground=BTN_HOV, activeforeground="white", relief="flat",
                  bd=0, highlightthickness=1, highlightbackground=BORDER, font=FUENTE_B, padx=10, pady=4, cursor="hand2", **kw)
    if ancho: b.config(width=ancho)
    return b


def etiqueta(parent, texto, color=TEXT, fuente=FUENTE, **kw):
    return tk.Label(parent, text=texto, bg=kw.pop("bg", PANEL), fg=color, font=fuente, **kw)


def entrada(parent, valor="", ancho=7):
    v = tk.StringVar(value=str(valor))
    e = tk.Entry(parent, textvariable=v, width=ancho, bg=BTN_BG, fg=ACCENT, insertbackground=ACCENT, relief="flat", font=FUENTE,
                 highlightthickness=1, highlightbackground=BORDER, highlightcolor=BTN_HOV)
    return e, v


def campo(parent, texto, valor, ancho=7, bg=PANEL):
    """Etiqueta + entrada en una fila; devuelve (frame, StringVar)."""
    f = tk.Frame(parent, bg=bg)
    tk.Label(f, text=texto, bg=bg, fg=MUTED, font=FUENTE).pack(side="left")
    e, v = entrada(f, valor, ancho); e.pack(side="left", padx=(4, 8))
    return f, v


def deslizador(parent, texto, desde, hasta, valor, comando, resolucion=0.1, color=BTN_HOV, ancho=250):
    """Fila: texto + valor numérico + Scale horizontal. Devuelve (frame, DoubleVar, Scale, Label_valor)."""
    f = tk.Frame(parent, bg=PANEL)
    v = tk.DoubleVar(value=valor)
    cab = tk.Frame(f, bg=PANEL); cab.pack(fill="x")
    tk.Label(cab, text=texto, bg=PANEL, fg=TEXT, font=FUENTE).pack(side="left")
    lv = tk.Label(cab, text="%.1f" % valor, bg=PANEL, fg=color, font=FUENTE_B); lv.pack(side="right")
    def _cmd(x):
        lv.config(text="%.1f" % float(x)); comando(float(x))
    s = tk.Scale(f, from_=desde, to=hasta, orient="horizontal", resolution=resolucion, variable=v, command=_cmd, showvalue=False, length=ancho,
                 bg=PANEL, fg=TEXT, troughcolor=BTN_BG, highlightthickness=0, activebackground=color, sliderrelief="flat", bd=0)
    s.pack(fill="x")
    return f, v, s, lv


def poner_deslizador(v, s, lv, valor):
    """Cambia un deslizador por programa sin disparar su comando."""
    cmd = s.cget("command"); s.config(command=""); v.set(valor); lv.config(text="%.1f" % valor); s.config(command=cmd)
