"""Interfaz de diagnóstico e identificación de los motores del SPM (adaptada de esp32_interfaz_control_PID_LGR_DB_SERVO.py: misma paleta y tarjetas de lectura).

    python herramientas/interfaz_identificacion.py                     (placa real: elige el COM y Conectar)
    python herramientas/interfaz_identificacion.py --simulado          (placa simulada, sin hardware)
    python herramientas/interfaz_identificacion.py --simulado --falla sin_giro     (reproduce «PWM aplicado pero el eje no gira»)

Pestaña 1 «Diagnóstico»: lecturas en vivo de un motor, test automático de la cadena ESP32 → driver → motor (¿gira en los dos sentidos?), manejo manual en lazo abierto,
prueba de polaridad y medición de cuentas por vuelta. Pestaña 2 «Identificación»: ejecuta el plan de captura (rampas + escalones), ajusta el modelo y lo dibuja.
Solo necesita tkinter, matplotlib, numpy y pyserial (nada de PyQt). Habla con firmware/spm_esp32s3 (JSON, 115200). Cierra el monitor serie antes de conectar.
"""
from __future__ import annotations

import argparse
import json
import queue
import statistics
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
import numpy as np  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from spm.identificacion import _g, ajustar_escalon, informe  # noqa: E402
from spm.identificacion_serial import Capturador, ErrorCaptura, ejecutar_plan  # noqa: E402

NM = 3
C = {"bg0": "#F0F2F5", "bg1": "#FFFFFF", "bg2": "#F7F8FA", "border": "#DDE1E9", "accent": "#2563EB", "accent_lt": "#EFF4FF", "ok": "#16A34A", "ok_lt": "#DCFCE7",
     "warn": "#DC2626", "warn_lt": "#FEE2E2", "amber": "#D97706", "amber_lt": "#FEF3C7", "muted": "#9AA3B2", "text": "#1E2533", "dim": "#6B7585",
     "u": "#8B5CF6", "pos": "#0EA5E9", "vel": "#D97706"}
UMBRAL_CUENTAS = 10             # menos de esto en una fase del test = «no giró»


# ------------------------------------------------------------------------------ diagnóstico (puro, probado sin interfaz)
def evaluar_cadena(res: dict, duty: float, estado: str = "run", tope: float | None = None) -> tuple[str, str, list[str]]:
    """res = {"+": (delta_cuentas, u_medio_permil[, duty_usado]), "-": (...)}: el último intento de cada sentido (el test sube el ciclo de trabajo hasta que el eje se mueve).
    `duty` se usa si no viene el duty de cada intento. Devuelve (nivel, título, líneas): nivel = ok | aviso | error."""
    def _t(x):
        return (x[0], x[1], x[2] if len(x) > 2 else duty)
    dp, up, dup = _t(res["+"])
    dn, un, dun = _t(res["-"])
    pedido = min(abs(dup), abs(dun)) * 1000
    if min(abs(up), abs(un)) < 0.6 * pedido:
        return "error", "La placa NO está aplicando el PWM pedido", [
            "u medido %d‰ / %d‰ y se pidieron %.0f‰ (estado de la placa: «%s»)." % (up, un, pedido, estado),
            "Si el estado no es «run» (tras un stop o una falla queda en «idle»/«fault»), pulsa Reset. Revisa también que pwm_max no sea menor que el duty pedido."]
    movio_p, movio_n = abs(dp) >= UMBRAL_CUENTAS, abs(dn) >= UMBRAL_CUENTAS
    if not movio_p and not movio_n:
        extra = ["Probé hasta +%.0f%% / −%.0f%%. Si pwm_max es menor que 1.0, súbelo (Ajustes) y repite: con esta reductora el motor puede necesitar más del 60 %%." % (abs(dup) * 100, abs(dun) * 100)] \
            if (tope or 1.0) < 0.99 else []
        return "error", "La placa manda PWM pero el eje NO gira: el fallo está después de la ESP32", extra + [
            "Cuentas: %+d con +%.0f%% y %+d con −%.0f%% (el encoder sí responde a mano, así que el fallo es de potencia, no del encoder)." % (dp, abs(dup) * 100, dn, abs(dun) * 100),
            "1) VM del TB6612 a 12 V medidos con multímetro CON el motor intentando girar (si cae, la fuente no da la corriente).",
            "2) STBY y PWMA del módulo a 3,3 V medidos (no solo cableados).",
            "3) Cables del motor en A01/A02 firmes (ni B01/B02). Mide ~8–9 Ω entre rojo y blanco.",
            "4) Prueba directa: desconecta GPIO 9 de AIN1 y toca AIN1 con 3V3: si el motor gira, el driver/motor/fuente están bien y falla el pin o el cable GPIO→AIN.",
            "5) Si el driver se calienta o el motor zumba sin girar, apaga: puede estar dañado o la fuente en corto."]
    if movio_p != movio_n:
        solo = "positivo" if movio_p else "negativo"
        cable = "AIN2 (GPIO 10)" if movio_p else "AIN1 (GPIO 9)"
        return "aviso", "Solo gira con duty %s" % solo, [
            "Cuentas: %+d (+) y %+d (−). Un solo sentido funciona." % (dp, dn),
            "Falla la mitad del puente: revisa el cable y el terminal de %s, y que no esté cruzado con otro pin." % cable]
    if dp < 0 < dn:
        return "aviso", "Gira en los dos sentidos pero el encoder cuenta al revés", [
            "Con duty positivo las cuentas BAJAN (%+d). Corrígelo con «Prueba de polaridad» o {\"cfg\":\"enc_sign\",\"motor\":N,\"val\":-1}." % dp]
    if dp * dn > 0:
        return "aviso", "Las cuentas suben (o bajan) en los dos sentidos", [
            "Cuentas %+d y %+d: el encoder no distingue el sentido (canal A o B suelto o repetido). Revisa amarillo y verde." % (dp, dn)]
    lineas = ["Cuentas: %+d con +%.0f%% y %+d con −%.0f%%. Encoder, driver, motor y fuente responden." % (dp, abs(dup) * 100, dn, abs(dun) * 100),
              "Empezó a girar con +%.0f %% y −%.0f %% de ciclo de trabajo (los niveles menores no lo movieron)." % (abs(dup) * 100, abs(dun) * 100)]
    if max(abs(dup), abs(dun)) > 0.55:
        lineas.append("Necesitó más del 50 %: es normal con esta reductora (fricción) y este driver (el PWM entra en decaimiento rápido). La identificación lo mide como zona muerta.")
    lineas.append("Siguiente: medir cuentas por vuelta y ejecutar el plan en la pestaña «Identificación» (con pwm_max en 1.0).")
    return "ok", "La cadena funciona: gira en los dos sentidos", lineas


# ------------------------------------------------------------------------------ enlace con la placa
class Enlace:
    """Puerto (pyserial o PlacaSimulada) + hilo lector. Cada objeto JSON recibido va a `al_json`; con `plan_activo`, TODAS las líneas también van a una cola
    que lee el Capturador del plan (por eso este objeto también tiene write/readline)."""

    def __init__(self, puerto, al_json):
        self.p, self.al_json, self.cola_plan, self.plan_activo = puerto, al_json, queue.Queue(), False
        self._vivo, self.error = True, None
        self.hilo = threading.Thread(target=self._leer, daemon=True, name="lector-placa")
        self.hilo.start()

    def _leer(self):
        buf = b""
        while self._vivo:
            try:
                datos = self.p.read(max(1, getattr(self.p, "in_waiting", 0) or 1)) if hasattr(self.p, "read") else self.p.readline()
            except Exception as e:  # noqa: BLE001   cable desconectado
                self.error = e
                return
            if not datos:
                continue
            buf += datos
            *lineas, buf = buf.split(b"\n")
            for crudo in lineas:
                linea = crudo.decode("utf-8", errors="replace").strip()
                if not linea:
                    continue
                if self.plan_activo:
                    self.cola_plan.put(linea)
                if linea.startswith("{"):
                    try:
                        self.al_json(json.loads(linea))
                    except ValueError:
                        pass

    def enviar(self, obj: dict) -> None:
        self.p.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def write(self, datos: bytes):
        return self.p.write(datos)

    def readline(self) -> bytes:
        try:
            return (self.cola_plan.get(timeout=0.05) + "\n").encode()
        except queue.Empty:
            return b""

    def cerrar(self):
        self._vivo = False
        try:
            self.p.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------------------ interfaz
class Tarjeta(tk.Frame):
    def __init__(self, padre, nombre, ancho=15):
        super().__init__(padre, bg=C["bg1"], highlightbackground=C["border"], highlightthickness=1)
        tk.Label(self, text=nombre, bg=C["bg1"], fg=C["dim"], font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(4, 0))
        self.v = tk.Label(self, text="—", bg=C["bg1"], fg=C["text"], font=("Consolas", 13, "bold"), width=ancho, anchor="w")
        self.v.pack(anchor="w", padx=8, pady=(0, 4))

    def poner(self, texto, color=None):
        self.v.config(text=texto, fg=color or C["text"])


class App:
    def __init__(self, root: tk.Tk, simulado: bool = False, falla: str = "ninguna", salida: Path | None = None):
        self.root, self.enlace, self.q_ui = root, None, queue.Queue()
        self.simulado, self.falla = tk.BooleanVar(value=simulado), falla
        self.salida = salida or (RAIZ / "identificacion")
        self.datos = [deque(maxlen=1500) for _ in range(NM)]          # (t_s, cuentas, u‰) por motor
        self.ult = [None] * NM
        self.extra = [(0, 0.0)] * NM                                   # (posRef en cuentas, error en °) de la telemetría
        self.ctrl_cargado = [None] * NM                                # «pid»/«casc» del último juego de ganancias enviado a cada motor
        self.estado, self.t_tel, self.jit_miss = "—", 0.0, (0, 0)
        self.marcas, self.ppr = deque(maxlen=60), [1540.0] * NM        # instantes de llegada (tasa) y ppr usado para mostrar grados
        self.pwm_max = [0.6] * NM
        self.prueba, self.ppr_medida, self.ocupado = None, None, False
        self.dormir_plan, self.factor_sim, self.u0_sim = time.sleep, 1.0, 0.08
        root.title("SPM — Diagnóstico e identificación de motores")
        root.geometry("1280x800")
        root.configure(bg=C["bg0"])
        root.protocol("WM_DELETE_WINDOW", self.cerrar)
        self._estilo()
        self._barra()
        self.libreta = ttk.Notebook(root)
        self.libreta.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._tab_diag()
        self._tab_ident()
        self.refrescar_puertos()
        self.log("Listo. Elige el COM y pulsa Conectar (cierra antes el monitor serie), o marca «Modo simulado».")
        self._ciclo()

    # ------------------------------------------------------------ construcción
    def _estilo(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=C["bg0"], foreground=C["text"], font=("Segoe UI", 9))
        s.configure("TNotebook", background=C["bg0"])
        s.configure("TNotebook.Tab", padding=(18, 6), background=C["bg2"])
        s.map("TNotebook.Tab", background=[("selected", C["bg1"])], foreground=[("selected", C["accent"])])
        s.configure("TButton", padding=(10, 5), background=C["bg1"])
        s.configure("TLabelframe", background=C["bg0"])
        s.configure("TLabelframe.Label", background=C["bg0"], foreground=C["dim"], font=("Segoe UI", 8, "bold"))
        s.configure("TFrame", background=C["bg0"])
        s.configure("TCheckbutton", background=C["bg0"])
        s.configure("TRadiobutton", background=C["bg0"])

    def _barra(self):
        b = ttk.Frame(self.root)
        b.pack(fill="x", padx=8, pady=6)
        ttk.Label(b, text="Puerto").pack(side="left")
        self.cb_puerto = ttk.Combobox(b, width=28, state="readonly")
        self.cb_puerto.pack(side="left", padx=4)
        ttk.Button(b, text="↻", width=3, command=self.refrescar_puertos).pack(side="left")
        self.btn_con = ttk.Button(b, text="Conectar", command=self.conectar)
        self.btn_con.pack(side="left", padx=6)
        ttk.Checkbutton(b, text="Modo simulado", variable=self.simulado).pack(side="left", padx=6)
        self.lbl_con = tk.Label(b, text="● sin conectar", fg=C["muted"], bg=C["bg0"], font=("Segoe UI", 10, "bold"))
        self.lbl_con.pack(side="left", padx=12)
        tk.Button(b, text="⛔  PARAR TODO", bg=C["warn"], fg="white", font=("Segoe UI", 11, "bold"), relief="flat", padx=14, command=self.parar).pack(side="right")

    def _tab_diag(self):
        f = ttk.Frame(self.libreta)
        self.libreta.add(f, text="  1 · Diagnóstico  ")
        cont = ttk.Frame(f, width=410)                                # columna con barra de desplazamiento: ya no cabe todo a la vez
        cont.pack(side="left", fill="y", padx=8, pady=8)
        cont.pack_propagate(False)
        lienzo = tk.Canvas(cont, highlightthickness=0, bg=C["bg0"])
        barra = ttk.Scrollbar(cont, orient="vertical", command=lienzo.yview)
        izq = ttk.Frame(lienzo)
        izq.bind("<Configure>", lambda _: lienzo.configure(scrollregion=lienzo.bbox("all")))
        lienzo.create_window((0, 0), window=izq, anchor="nw", width=385)
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        lienzo.bind("<Enter>", lambda _: lienzo.bind_all("<MouseWheel>", lambda e: lienzo.yview_scroll(-1 * (e.delta // 120), "units")))
        lienzo.bind("<Leave>", lambda _: lienzo.unbind_all("<MouseWheel>"))
        fm = ttk.LabelFrame(izq, text="MOTOR")
        fm.pack(fill="x")
        self.var_motor = tk.IntVar(value=1)
        for m in range(1, NM + 1):
            ttk.Radiobutton(fm, text="Motor %d" % m, variable=self.var_motor, value=m).pack(side="left", padx=8, pady=4)
        g = ttk.LabelFrame(izq, text="LECTURAS EN VIVO")
        g.pack(fill="x", pady=6)
        self.t_pos, self.t_ang, self.t_vel = Tarjeta(g, "Posición [cuentas]"), Tarjeta(g, "Ángulo [°]"), Tarjeta(g, "Velocidad [°/s]")
        self.t_u, self.t_est, self.t_hz = Tarjeta(g, "u aplicado [‰]"), Tarjeta(g, "Estado de la placa"), Tarjeta(g, "Telemetría")
        self.t_obj, self.t_err = Tarjeta(g, "Objetivo [°]"), Tarjeta(g, "Error al objetivo [°]")
        for i, t in enumerate((self.t_pos, self.t_ang, self.t_vel, self.t_u, self.t_obj, self.t_err, self.t_est, self.t_hz)):
            t.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=3)
        g.columnconfigure((0, 1), weight=1)
        ft = ttk.LabelFrame(izq, text="TEST AUTOMÁTICO DE LA CADENA (ESP32 → driver → motor)")
        ft.pack(fill="x", pady=4)
        self.btn_test = ttk.Button(ft, text="▶  Probar el motor en los dos sentidos", command=self.iniciar_prueba)
        self.btn_test.pack(fill="x", padx=6, pady=6)
        fp = ttk.LabelFrame(izq, text="CONTROL DE POSICIÓN (lazo cerrado, motor LIBRE)")
        fp.pack(fill="x", pady=4)
        r = ttk.Frame(fp)
        r.pack(fill="x", padx=6, pady=3)
        ttk.Button(r, text="Cargar ganancias sintonizadas", command=self.cargar_ganancias).pack(side="left")
        self.var_opt = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="optimizadas", variable=self.var_opt).pack(side="left", padx=6)
        r = ttk.Frame(fp)
        r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="Ir a [°]").pack(side="left")
        self.var_destino = tk.StringVar(value="90")
        ttk.Entry(r, textvariable=self.var_destino, width=7).pack(side="left", padx=4)
        ttk.Button(r, text="Ir", command=self.ir_a).pack(side="left")
        ttk.Button(r, text="Volver a 0", command=lambda: self.ir_a(0.0)).pack(side="left", padx=4)
        ttk.Button(r, text="Guardar CSV", command=self.guardar_telemetria).pack(side="left")
        fa = ttk.LabelFrame(izq, text="MANEJO MANUAL (lazo abierto, motor LIBRE)")
        fa.pack(fill="x", pady=4)
        self.sl = tk.Scale(fa, from_=-100, to=100, resolution=5, orient="horizontal", label="ciclo de trabajo [%]", bg=C["bg0"], highlightthickness=0, length=330)
        self.sl.set(0)
        self.sl.pack(padx=6)
        r = ttk.Frame(fa)
        r.pack(fill="x", padx=6, pady=4)
        ttk.Button(r, text="Aplicar", command=self.aplicar_manual).pack(side="left")
        ttk.Button(r, text="Pulso 1,5 s", command=self.pulso).pack(side="left", padx=6)
        ttk.Button(r, text="Duty 0", command=lambda: self.duty(0)).pack(side="left")
        fh = ttk.LabelFrame(izq, text="AJUSTES")
        fh.pack(fill="x", pady=4)
        r = ttk.Frame(fh)
        r.pack(fill="x", padx=6, pady=3)
        ttk.Button(r, text="Prueba de polaridad", command=self.polaridad).pack(side="left")
        ttk.Button(r, text="Reset", command=lambda: self.enviar({"cmd": "reset"})).pack(side="left", padx=6)
        ttk.Button(r, text="Volver a PID", command=self.volver_pid).pack(side="left")
        r = ttk.Frame(fh)
        r.pack(fill="x", padx=6, pady=3)
        ttk.Label(r, text="pwm_max").pack(side="left")
        self.sp_pwm = ttk.Spinbox(r, from_=0.05, to=1.0, increment=0.05, width=6)
        self.sp_pwm.set("0.6")
        self.sp_pwm.pack(side="left", padx=4)
        ttk.Button(r, text="Aplicar", command=self.aplicar_pwm).pack(side="left")
        r = ttk.Frame(fh)
        r.pack(fill="x", padx=6, pady=3)
        self.btn_vuelta = ttk.Button(r, text="Medir cuentas por vuelta", command=self.medir_vuelta)
        self.btn_vuelta.pack(side="left")
        ttk.Label(r, text="en").pack(side="left", padx=(6, 2))
        self.sp_vueltas = ttk.Spinbox(r, from_=1, to=30, width=3)
        self.sp_vueltas.set("5")
        self.sp_vueltas.pack(side="left")
        ttk.Label(r, text="vueltas").pack(side="left", padx=(2, 0))
        self.lbl_ppr = ttk.Label(r, text="")
        self.lbl_ppr.pack(side="left", padx=6)
        self.var_ppr = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.var_ppr, width=7).pack(side="left", padx=(8, 2))
        ttk.Button(r, text="Aplicar ppr", command=self.aplicar_ppr).pack(side="left")
        r = ttk.Frame(fh)
        r.pack(fill="x", padx=6, pady=3)
        ttk.Button(r, text="Guardar ajustes en la placa", command=lambda: self.enviar({"cmd": "save"})).pack(side="left")
        ttk.Label(r, text="(ppr y pwm_max sobreviven a un reinicio)").pack(side="left", padx=6)
        der = ttk.Frame(f)
        der.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 8))
        self.res = tk.Label(der, text="Sin resultado todavía. Conecta y pulsa «Probar el motor en los dos sentidos».", bg=C["bg2"], fg=C["dim"], font=("Segoe UI", 12, "bold"),
                            anchor="w", justify="left", padx=10, pady=8, wraplength=760)
        self.res.pack(fill="x")
        self.det = tk.Label(der, text="", bg=C["bg0"], fg=C["text"], anchor="w", justify="left", wraplength=800, font=("Segoe UI", 9))
        self.det.pack(fill="x", pady=(2, 4))
        der.bind("<Configure>", lambda e: (self.det.config(wraplength=max(300, e.width - 24)), self.res.config(wraplength=max(300, e.width - 24))))   # que el texto no se corte
        self.fig = Figure(figsize=(7, 5), dpi=100)
        self.ax = self.fig.subplots(3, 1, sharex=True)
        self.fig.subplots_adjust(left=0.1, right=0.98, top=0.97, bottom=0.08, hspace=0.12)
        self.l_pos, = self.ax[0].plot([], [], color=C["pos"], lw=1.6)
        self.l_obj, = self.ax[0].plot([], [], "--", color=C["muted"], lw=1.0)
        self.l_vel, = self.ax[1].plot([], [], color=C["vel"], lw=1.4)
        self.l_u, = self.ax[2].plot([], [], color=C["u"], lw=1.6)
        for a, t in zip(self.ax, ("posición [cuentas]", "velocidad [°/s]", "u [‰]")):
            a.set_ylabel(t, fontsize=8)
            a.grid(True, alpha=0.3)
            a.tick_params(labelsize=8)
        self.ax[2].set_xlabel("tiempo de la placa [s]", fontsize=8)
        self.canvas = FigureCanvasTkAgg(self.fig, master=der)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.txt = tk.Text(der, height=7, font=("Consolas", 9), bg=C["bg1"], state="disabled")
        self.txt.pack(fill="x", pady=(4, 0))

    def _tab_ident(self):
        f = ttk.Frame(self.libreta)
        self.libreta.add(f, text="  2 · Identificación  ")
        izq = ttk.Frame(f, width=360)
        izq.pack(side="left", fill="y", padx=8, pady=8)
        izq.pack_propagate(False)
        ttk.Label(izq, text="Usa el motor elegido en la pestaña 1.\nPRIMERO: motor LIBRE (sin el mecanismo) y el test de la cadena en verde.", justify="left").pack(anchor="w")
        fo = ttk.LabelFrame(izq, text="PLAN (rampas ± y 4 escalones por sentido)")
        fo.pack(fill="x", pady=8)
        self.e_tope, self.e_ms, self.e_deg = tk.StringVar(value=""), tk.StringVar(value="500"), tk.StringVar(value="720")
        for i, (t, v) in enumerate((("Tope de ciclo de trabajo (vacío = pwm_max)", self.e_tope), ("Duración de cada escalón [ms]", self.e_ms), ("Giro máximo por captura [°]", self.e_deg))):
            ttk.Label(fo, text=t).grid(row=i, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(fo, textvariable=v, width=8).grid(row=i, column=1, padx=6)
        ttk.Label(fo, text="Carpeta de salida").grid(row=3, column=0, sticky="w", padx=6)
        self.e_sal = tk.StringVar(value=str(self.salida))
        ttk.Entry(fo, textvariable=self.e_sal, width=34).grid(row=4, column=0, columnspan=2, padx=6, pady=3)
        self.btn_plan = ttk.Button(izq, text="▶  Ejecutar plan de identificación", command=self.ejecutar_plan)
        self.btn_plan.pack(fill="x", pady=4)
        self.lbl_plan = ttk.Label(izq, text="", wraplength=340, justify="left")
        self.lbl_plan.pack(anchor="w")
        self.txt_inf = tk.Text(izq, height=18, font=("Consolas", 9), bg=C["bg1"], state="disabled")
        self.txt_inf.pack(fill="both", expand=True, pady=6)
        der = ttk.Frame(f)
        der.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 8))
        self.fig2 = Figure(figsize=(7, 5), dpi=100)
        self.ax2 = self.fig2.subplots(2, 1)
        self.fig2.subplots_adjust(left=0.1, right=0.97, top=0.95, bottom=0.08, hspace=0.3)
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=der)
        self.canvas2.get_tk_widget().pack(fill="both", expand=True)
        self._dibujar_vacio()

    # ------------------------------------------------------------ utilidades
    def log(self, texto: str):
        self.txt.config(state="normal")
        self.txt.insert("end", time.strftime("%H:%M:%S  ") + texto + "\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def motor(self) -> int:
        return self.var_motor.get()

    def enviar(self, obj: dict):
        if self.enlace is None:
            self.log("Primero conecta la placa.")
            return
        self.enlace.enviar(obj)

    def refrescar_puertos(self):
        try:
            from serial.tools import list_ports
            ps = [f"{p.device} — {p.description}" for p in sorted(list_ports.comports())]
        except Exception:  # noqa: BLE001
            ps = []
        self.cb_puerto["values"] = ps
        cand = [p for p in ps if "USB" in p.upper() or "CH340" in p.upper() or "CP210" in p.upper()]
        self.cb_puerto.set(cand[0] if len(cand) == 1 else (ps[0] if len(ps) == 1 else self.cb_puerto.get()))

    # ------------------------------------------------------------ conexión
    def conectar(self):
        if self.enlace is not None:
            self.enlace.cerrar()
            self.enlace = None
            self.btn_con.config(text="Conectar")
            self.lbl_con.config(text="● sin conectar", fg=C["muted"])
            return
        try:
            if self.simulado.get():
                from spm.placa_simulada import PlacaSimulada
                puerto = PlacaSimulada(falla=self.falla, factor_tiempo=self.factor_sim, u0=self.u0_sim)
            else:
                import serial
                nombre = self.cb_puerto.get().split(" — ")[0]
                if not nombre:
                    messagebox.showwarning("Puerto", "Elige el puerto COM de la ESP32 (↻ si no aparece).")
                    return
                puerto = serial.Serial()
                puerto.port, puerto.baudrate, puerto.timeout = nombre, 115200, 0.05
                puerto.dtr = puerto.rts = False
                puerto.open()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("No se pudo abrir el puerto", "%s\n\n¿Está abierto el monitor serie u otro programa? Ciérralo." % e)
            return
        self.enlace = Enlace(puerto, lambda d: self.q_ui.put(d))
        for d in self.datos:
            d.clear()
        self.btn_con.config(text="Desconectar")
        self.log("Conectado. Esperando telemetría…")
        self.enviar({"cmd": "status"})

    def parar(self):
        if self.enlace is None:
            return
        self.prueba = None
        for m in range(1, NM + 1):
            self.enlace.enviar({"duty": m, "val": 0})
        self.enlace.enviar({"cmd": "stop"})
        self.log("PARAR enviado: duty 0 en los tres motores y stop (el eje sigue un momento por inercia).")

    # ------------------------------------------------------------ recepción (hilo de la interfaz)
    def _drena(self):
        for _ in range(400):
            try:
                d = self.q_ui.get_nowait()
            except queue.Empty:
                return
            if "plan_fin" in d:
                self._plan_fin(d["plan_fin"])
            elif d.get("info") == "plan":
                self.lbl_plan.config(text=d["txt"])
                self.log(d["txt"])
            elif "t" in d and "m1" in d:
                self._telemetria(d)
            elif d.get("info") == "status":
                self.pwm_max = [float(x) for x in d.get("pwm_max", self.pwm_max)]
                self.ppr = [float(x) for x in d.get("ppr", self.ppr)]
                self.log("Placa: estado %s, ppr %s, pwm_max %s" % (d.get("st"), d.get("ppr"), d.get("pwm_max")))
            elif "polarity" in d:
                self.log("Polaridad motor %d: %s (%d cuentas)" % (d["polarity"], d["result"], d["counts"]))
            elif "err" in d or "ack" in d or "info" in d:
                if d.get("ack") not in ("duty",):
                    self.log(json.dumps(d, ensure_ascii=False))

    def _telemetria(self, d: dict):
        self.t_tel = d["t"] / 1000.0
        self.estado = d.get("st", "—")
        self.jit_miss = (d.get("jit", 0), d.get("miss", 0))
        self.marcas.append(time.monotonic())
        for m in range(NM):
            x = d.get("m%d" % (m + 1))
            if x:
                self.ult[m] = (int(x["pos"]), int(x["u"]))
                self.extra[m] = (int(x.get("posRef", 0)), float(x.get("e", 0.0)))
                self.datos[m].append((self.t_tel, int(x["pos"]), int(x["u"]), int(x.get("posRef", 0)), float(x.get("e", 0.0))))

    # ------------------------------------------------------------ test de la cadena (máquina de estados con el tiempo de la placa)
    NIVELES_TEST = (0.5, 0.65, 0.8, 1.0)          # ciclos de trabajo que prueba, de menor a mayor, hasta que el eje se mueve (con tope en pwm_max)

    def iniciar_prueba(self):
        if self.enlace is None:
            messagebox.showinfo("Test", "Primero conecta la placa.")
            return
        if self.ocupado or self.prueba:
            return
        if len(self.marcas) < 5 or time.monotonic() - self.marcas[-1] > 1.0:
            self.res.config(text="No llega telemetría de la placa", bg=C["warn_lt"], fg=C["warn"])
            self.det.config(text="Revisa el puerto/baudios (115200) y que el firmware del SPM esté cargado (si ves otro texto, el firmware es otro). Pulsa RESET en la placa.")
            return
        m = self.motor()
        tope = self.pwm_max[m - 1]
        niveles = [d for d in self.NIVELES_TEST if d <= tope + 1e-9] or [tope]
        fases = [("+", +d, 1.0) for d in niveles] + [("-", -d, 1.0) for d in niveles]
        self.prueba = {"m": m, "fases": fases, "i": -1, "res": {}, "ok": {}, "tope": tope}
        self.res.config(text="Probando el motor %d…  (el eje va a girar)" % m, bg=C["amber_lt"], fg=C["amber"])
        self.det.config(text="Sube el ciclo de trabajo (%s %%) hasta que el eje se mueve, primero en + y luego en −." % ", ".join("%.0f" % (d * 100) for d in niveles))
        self.btn_test.state(["disabled"])
        if self.estado != "run":                                      # tras un stop/falla la placa ignora el duty: se reinicia antes
            self.log("La placa estaba en «%s»: envío Reset antes de probar." % self.estado)
            self.enviar({"cmd": "reset"})
            self.prueba["esperar_run"] = time.monotonic()
        else:
            self._arrancar_prueba()

    def _arrancar_prueba(self):
        self.enviar({"cfg": "ctrl", "motor": self.prueba["m"], "val": "open"})
        self._fase_siguiente()

    def _fase_siguiente(self):
        p = self.prueba
        p["i"] += 1
        while p["i"] < len(p["fases"]) and p["ok"].get(p["fases"][p["i"]][0]):    # ese sentido ya giró: se saltan sus niveles mayores
            p["i"] += 1
        if p["i"] >= len(p["fases"]):
            self._terminar_prueba()
            return
        nombre, duty, dur = p["fases"][p["i"]]
        self.enviar({"duty": p["m"], "val": duty})
        p.update(t0=self.t_tel, pos0=(self.ult[p["m"] - 1] or (0, 0))[0], us=[], nombre=nombre, dur=dur, duty=duty)

    def _paso_prueba(self):
        p = self.prueba
        if not p:
            return
        if "esperar_run" in p:
            if self.estado == "run":
                del p["esperar_run"]
                self._arrancar_prueba()
            elif time.monotonic() - p["esperar_run"] > 2.5:
                self.prueba = None
                self.res.config(text="La placa no sale del estado «%s» tras Reset" % self.estado, bg=C["warn_lt"], fg=C["warn"])
                self.det.config(text="Mira el registro de abajo; si dice «fault», revisa polaridad del encoder y cableado.")
                self.btn_test.state(["!disabled"])
            return
        if time.monotonic() - self.marcas[-1] > 2.0 if self.marcas else True:
            self.prueba = None
            self.enviar({"duty": p["m"], "val": 0})
            self.res.config(text="Se perdió la telemetría durante la prueba", bg=C["warn_lt"], fg=C["warn"])
            self.btn_test.state(["!disabled"])
            return
        if "espera" in p:                                             # 0,4 s con el motor sin potencia entre intentos
            if self.t_tel >= p["espera"]:
                del p["espera"]
                self._fase_siguiente()
            return
        pos, u = self.ult[p["m"] - 1] or (0, 0)
        p["us"].append(u)
        if self.t_tel - p["t0"] >= p["dur"]:
            delta = pos - p["pos0"]
            p["res"][p["nombre"]] = (delta, statistics.mean(p["us"]) if p["us"] else 0, p["duty"])
            if abs(delta) >= UMBRAL_CUENTAS:
                p["ok"][p["nombre"]] = True
            self.enviar({"duty": p["m"], "val": 0})
            p["espera"] = self.t_tel + 0.4

    def _terminar_prueba(self):
        p, self.prueba = self.prueba, None
        self.enviar({"duty": p["m"], "val": 0})
        nivel, titulo, lineas = evaluar_cadena(p["res"], 0.5, self.estado, p["tope"])
        col = {"ok": (C["ok_lt"], C["ok"]), "aviso": (C["amber_lt"], C["amber"]), "error": (C["warn_lt"], C["warn"])}[nivel]
        self.res.config(text="Motor %d: %s" % (p["m"], titulo), bg=col[0], fg=col[1])
        self.det.config(text="\n".join(lineas))
        self.log("Test motor %d: %s" % (p["m"], titulo))
        self.btn_test.state(["!disabled"])

    # ------------------------------------------------------------ manejo manual
    def duty(self, v: float):
        m = self.motor()
        if self.estado != "run" and self.enlace is not None:          # tras un stop/falla la placa ignora el duty
            self.log("La placa estaba en «%s»: Reset enviado." % self.estado)
            self.enlace.enviar({"cmd": "reset"})
            self.root.after(250, lambda: self._duty_ya(m, v))
            return
        self._duty_ya(m, v)

    def _duty_ya(self, m: int, v: float):
        self.enviar({"cfg": "ctrl", "motor": m, "val": "open"})
        self.enviar({"duty": m, "val": float(v)})

    def aplicar_manual(self):
        v = self.sl.get() / 100.0
        if abs(v) > self.pwm_max[self.motor() - 1] + 1e-9:
            self.log("Ojo: pwm_max es %.2f; la placa limita el duty a ese valor." % self.pwm_max[self.motor() - 1])
        self.duty(v)

    def pulso(self):
        self.aplicar_manual()
        m = self.motor()
        self.root.after(1500, lambda: self.enlace and self.enlace.enviar({"duty": m, "val": 0}))

    def volver_pid(self):
        m = self.motor()
        self.enviar({"duty": m, "val": 0})
        self.enviar({"cfg": "ctrl", "motor": m, "val": "pid"})

    def polaridad(self):
        if messagebox.askyesno("Prueba de polaridad", "Cada motor girará ~0,2 s al 40 % (ignora pwm_max). Con el motor LIBRE. ¿Continuar?"):
            self.enviar({"cmd": "polarity", "apply": 1})

    def aplicar_pwm(self):
        self.enviar({"cfg": "pwm_max", "motor": self.motor(), "val": float(self.sp_pwm.get())})
        self.root.after(300, lambda: self.enviar({"cmd": "status"}))

    def medir_vuelta(self):
        if self.ppr_medida is None:
            if self.ult[self.motor() - 1] is None:
                self.log("Sin telemetría todavía.")
                return
            self.ppr_medida = self.ult[self.motor() - 1][0]
            self.vueltas = max(1, int(float(self.sp_vueltas.get() or 1)))
            self.btn_vuelta.config(text="Terminé las vueltas")
            self.lbl_ppr.config(text="Gira el eje de SALIDA %d vuelta(s) completas (marca el eje con cinta)…" % self.vueltas)
        else:
            total = abs(self.ult[self.motor() - 1][0] - self.ppr_medida)
            n = total / self.vueltas                                  # varias vueltas: el error de alinear la marca se reparte entre todas
            self.ppr_medida = None
            self.btn_vuelta.config(text="Medir cuentas por vuelta")
            self.lbl_ppr.config(text="%.1f cuentas/vuelta (%d en %d vueltas)" % (n, total, self.vueltas))
            self.ppr_listo = round(n, 1)
            self.var_ppr.set("%g" % self.ppr_listo)                   # queda en el campo para aplicarlo (o escribir otro valor)

    def aplicar_ppr(self):
        try:
            n = float(self.var_ppr.get().replace(",", ".")) if self.var_ppr.get().strip() else getattr(self, "ppr_listo", None)
        except ValueError:
            n = None
        if not n or n < 10:
            self.log("Escribe las cuentas por vuelta en el campo (o mídelas primero).")
            return
        self.enviar({"cfg": "ppr", "motor": self.motor(), "val": n})
        self.ppr[self.motor() - 1] = n
        self.enviar({"cmd": "status"})

    # ------------------------------------------------------------ control de posición
    def cargar_ganancias(self):
        m = self.motor()
        ruta = Path(self.e_sal.get()) / ("ganancias_motor%d.json" % m)
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            messagebox.showerror("Ganancias", "No pude leer %s:\n%s\n\nGenera las ganancias con herramientas/sintonizar_motor.py." % (ruta, e))
            return
        cmds = d["comandos_optimizadas"] if (self.var_opt.get() and "comandos_optimizadas" in d) else d["comandos"]
        self.log("Cargando ganancias %s de %s (%d órdenes)." % ("optimizadas" if cmds is not d["comandos"] else "conservadoras", ruta.name, len(cmds)))
        if self.estado != "run":
            self.enviar({"cmd": "reset"})
        for c in cmds:
            self.enviar(c)
        self.ctrl_cargado[m - 1] = cmds[0]["val"]
        self.enviar({"cmd": "status"})

    def ir_a(self, destino=None):
        m = self.motor()
        try:
            x = float(self.var_destino.get().replace(",", ".")) if destino is None else float(destino)
        except ValueError:
            self.log("Escribe la posición en grados.")
            return
        if abs(x) > 400:
            messagebox.showwarning("Posición", "Por seguridad la interfaz solo manda posiciones de hasta ±400° (eje del motor).")
            return
        if self.ctrl_cargado[m - 1] is None and not messagebox.askyesno(
                "Sin ganancias sintonizadas", "No has cargado ganancias: el firmware usaría las de fábrica (pensadas para otro motor) y puede vibrar o pasarse. ¿Continuar?"):
            return
        ctrl = self.ctrl_cargado[m - 1] or "pid"
        if self.estado != "run":
            self.enviar({"cmd": "reset"})
        self.enviar({"cfg": "ctrl", "motor": m, "val": ctrl})
        self.root.after(200, lambda: self.enviar({"motor": m, "pos": x}))
        self.log("Motor %d: ir a %.1f° (modo %s)." % (m, x, ctrl))

    def guardar_telemetria(self):
        m = self.motor()
        d = self.datos[m - 1]
        if len(d) < 5:
            self.log("Todavía no hay telemetría que guardar.")
            return
        carpeta = Path(self.e_sal.get())
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / ("telemetria_motor%d_%s.csv" % (m, time.strftime("%Y%m%d_%H%M%S")))
        with open(ruta, "w", encoding="utf-8", newline="") as fh:
            fh.write("# motor=%d ppr=%g\nt_s,pos_cuentas,u_permil,objetivo_cuentas,error_deg\n" % (m, self.ppr[m - 1]))
            for fila in d:
                fh.write("%.3f,%d,%d,%d,%.3f\n" % tuple(fila))
        self.log("Telemetría guardada: %s" % ruta)

    # ------------------------------------------------------------ plan de identificación
    def ejecutar_plan(self):
        if self.enlace is None or self.ocupado:
            self.log("Conecta la placa primero." if self.enlace is None else "Ya hay un plan en curso.")
            return
        m = self.motor()
        try:
            tope = float(self.e_tope.get()) if self.e_tope.get().strip() else None
            ms, deg = int(self.e_ms.get()), float(self.e_deg.get())
        except ValueError:
            messagebox.showerror("Plan", "Revisa los números del plan.")
            return
        if abs(self.ppr[m - 1] - 1000.0) < 1e-6 and not messagebox.askyesno(
                "Cuentas por vuelta sin medir", "La placa tiene ppr = 1000 (valor de fábrica). Si no lo has medido, K y las velocidades saldrán en °/s equivocados "
                "(los CSV se pueden recalcular después, pero es mejor medirlo ahora: «Medir cuentas por vuelta» y «Aplicar ppr»).\n\n¿Ejecutar el plan de todos modos?"):
            return
        if not messagebox.askyesno("Plan de identificación", "Motor %d LIBRE, 12 V encendida, mano en el interruptor. Cada captura gira el eje hasta %.0f°. ¿Empezar?" % (m, deg)):
            return
        self.ocupado = True
        self.btn_plan.state(["disabled"])
        self.btn_test.state(["disabled"])
        self.lbl_plan.config(text="Ejecutando…")
        self.salida = Path(self.e_sal.get())
        threading.Thread(target=self._plan_hilo, args=(m, tope, ms, deg), daemon=True).start()

    def _plan_hilo(self, m, tope, ms, deg):
        res = {"m": m}
        try:
            self.enlace.plan_activo = True
            while not self.enlace.cola_plan.empty():
                self.enlace.cola_plan.get_nowait()
            cap = Capturador(self.enlace, log=lambda *a: self.q_ui.put({"info": "plan", "txt": " ".join(str(x) for x in a)}), reloj=time.monotonic, dormir=self.dormir_plan)
            res["caps"], res["modelo"] = ejecutar_plan(cap, m, self.salida, tope, ms, deg, log=lambda *a: self.q_ui.put({"info": "plan", "txt": " ".join(str(x) for x in a)}))
        except ErrorCaptura as e:
            res["error"] = str(e)
            try:
                self.enlace.enviar({"cmd": "stop"})
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            res["error"] = "fallo inesperado: %r" % e
        finally:
            self.enlace.plan_activo = False
        self.q_ui.put({"plan_fin": res})

    def _plan_fin(self, res: dict):
        self.ocupado = False
        self.btn_plan.state(["!disabled"])
        self.btn_test.state(["!disabled"])
        self.txt_inf.config(state="normal")
        self.txt_inf.delete("1.0", "end")
        if "error" in res:
            self.lbl_plan.config(text="Falló: " + res["error"])
            self.txt_inf.insert("end", "ERROR: " + res["error"])
        else:
            self.lbl_plan.config(text="Listo. CSV y modelo en %s" % self.salida)
            self.txt_inf.insert("end", informe(res["modelo"]))
            res["modelo"].a_json(self.salida / ("modelo_motor%d.json" % res["m"]))
            self._dibujar_ajuste(res["caps"])
        self.txt_inf.config(state="disabled")

    def _dibujar_vacio(self):
        for a, t in zip(self.ax2, ("Escalones: medido (fino) y modelo ajustado (grueso)", "Rampa triangular: posición y ciclo de trabajo")):
            a.clear()
            a.set_title(t, fontsize=9)
            a.grid(True, alpha=0.3)
        self.canvas2.draw_idle()

    def _dibujar_ajuste(self, caps):
        a0, a1 = self.ax2
        a0.clear()
        a1.clear()
        for c in caps:
            if c.modo == "step":
                aj = ajustar_escalon(c)
                if aj is None:
                    continue
                act = np.abs(c.u) > 0.5 * np.abs(c.u).max()
                i0, i1 = int(np.argmax(act)), int(len(act) - 1 - np.argmax(act[::-1]))
                t = c.t[i0:i1 + 1] - c.t[i0]
                a0.plot(t, c.theta[i0:i1 + 1] - c.theta[i0], color=C["pos"], lw=0.8)
                a0.plot(t, aj.K_efectiva * _g(t, aj.tau), color=C["warn"], lw=1.2, alpha=0.8)
        a0.set_title("Escalones: medido (azul) y modelo ajustado (rojo)", fontsize=9)
        a0.set_ylabel("°", fontsize=8)
        tri = [c for c in caps if c.modo == "tri" and c.duty > 0]
        if tri:
            c = tri[0]
            a1.plot(c.t, c.theta - c.theta[0], color=C["pos"], label="posición [°]")
            b = a1.twinx()
            b.plot(c.t, c.u * 1000, color=C["u"], lw=1, label="u [‰]")
            b.tick_params(labelsize=8)
        a1.set_title("Rampa triangular (+): posición y ciclo de trabajo", fontsize=9)
        a1.set_xlabel("s", fontsize=8)
        for a in (a0, a1):
            a.grid(True, alpha=0.3)
            a.tick_params(labelsize=8)
        self.canvas2.draw_idle()

    # ------------------------------------------------------------ refresco
    def _ciclo(self):
        self._drena()
        self._paso_prueba()
        if self.enlace is not None and self.enlace.error is not None:
            self.lbl_con.config(text="● SE PERDIÓ EL PUERTO", fg=C["warn"])
        elif self.enlace is not None:
            vivo = bool(self.marcas) and time.monotonic() - self.marcas[-1] < 1.0
            self.lbl_con.config(text="● telemetría OK" if vivo else "● conectado, sin telemetría", fg=C["ok"] if vivo else C["amber"])
        self._lecturas()
        if getattr(self, "_t_graf", 0) < time.monotonic() - 0.4:
            self._t_graf = time.monotonic()
            self._graficas()
        self._id_ciclo = self.root.after(50, self._ciclo)

    def _lecturas(self):
        m = self.motor() - 1
        u = self.ult[m]
        dpc = 360.0 / self.ppr[m]
        if u is None:
            return
        self.t_pos.poner("%d" % u[0])
        self.t_ang.poner("%.1f" % (u[0] * dpc))
        d = self.datos[m]
        if len(d) > 6:
            v = (d[-1][1] - d[-6][1]) * dpc / max(1e-3, d[-1][0] - d[-6][0])
            self.t_vel.poner("%.1f" % v)
        self.t_u.poner("%d" % u[1], C["u"] if u[1] else None)
        po, ee = self.extra[m]
        self.t_obj.poner("%.1f" % (po * dpc))
        self.t_err.poner("%+.2f" % ee, C["warn"] if abs(ee) > 1.0 else None)
        self.t_est.poner(self.estado, C["warn"] if self.estado == "fault" else None)
        hz = (len(self.marcas) - 1) / max(1e-3, self.marcas[-1] - self.marcas[0]) if len(self.marcas) > 5 and time.monotonic() - self.marcas[-1] < 1.0 else 0
        self.t_hz.poner("%.0f Hz  miss %d" % (hz, self.jit_miss[1]), C["ok"] if hz > 10 else C["warn"])

    def _graficas(self):
        if self.libreta.index(self.libreta.select()) != 0:
            return
        d = self.datos[self.motor() - 1]
        if len(d) < 3:
            return
        a = np.array(d)
        t, pos, u = a[:, 0], a[:, 1], a[:, 2]
        dpc = 360.0 / self.ppr[self.motor() - 1]
        k = 4
        vel = np.full_like(pos, np.nan)
        vel[k:] = (pos[k:] - pos[:-k]) * dpc / np.maximum(1e-3, t[k:] - t[:-k])
        t0 = max(t[0], t[-1] - 30)
        for ln, y in ((self.l_pos, pos), (self.l_vel, vel), (self.l_u, u)):
            ln.set_data(t, y)
        self.l_obj.set_data(t, a[:, 3] if a.shape[1] > 3 else pos)
        for ax in self.ax:
            ax.set_xlim(t0, t[-1] + 0.5)
            ax.relim(visible_only=False)
            m = t >= t0
            y = ax.lines[0].get_ydata()[m]
            if np.isfinite(y).any():
                lo, hi = np.nanmin(y), np.nanmax(y)
                ax.set_ylim(lo - 0.05 * (hi - lo) - 1, hi + 0.05 * (hi - lo) + 1)
        self.canvas.draw_idle()

    def cerrar(self):
        try:
            self.root.after_cancel(self._id_ciclo)
            self.parar()
            if self.enlace:
                self.enlace.cerrar()
        finally:
            self.root.destroy()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simulado", action="store_true", help="placa simulada (sin hardware)")
    ap.add_argument("--falla", default="ninguna", choices=["ninguna", "sin_giro", "solo_positivo", "solo_negativo", "encoder_invertido"], help="falla de la placa simulada")
    ap.add_argument("--salida", type=Path, help="carpeta para los CSV y el modelo (def.: identificacion/)")
    a = ap.parse_args(argv)
    root = tk.Tk()
    App(root, a.simulado, a.falla, a.salida)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
