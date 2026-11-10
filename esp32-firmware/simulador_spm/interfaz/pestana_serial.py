"""Pestaña 4 — Serial / ESP32: mismo protocolo JSON que el brazo 3DOF.

  TX  {"m1":a,"m2":b,"m3":c}  (grados de motor)   ·   {"motor":N,"pos":deg}   ·   {"cmd":"reset"}   ·   {"zero":N|"all"}
  RX  {"m1":{"posRef":pulsos,"pos":pulsos,"ok":true}, "m2":{...}, "m3":{...}}   (telemetría)

SUPUESTO: el firmware del SPM usa este mismo protocolo (la PCB ESP32 + BTS7960 es la del brazo). Los pulsos/vuelta de cada motor se configuran aquí.
SIN PROBAR CON HARDWARE REAL: se probó solo contra herramientas/esp32_simulado.py. Los comandos se validan contra los límites del CAD antes de enviarse.
"""
import json
import math
import queue
import threading
import time
from collections import deque
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import serial
    import serial.tools.list_ports
except ImportError:                                  # pyserial es opcional
    serial = None

from spm import FueraDeEspacioDeTrabajo
from . import tema
from .tema import PANEL, TEXT, MUTED, C_M

N_TEL = 400


class PestanaSerial:
    nombre = "Serial / ESP32"

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=tema.BG)
        self.ser = None; self.cola = queue.Queue(); self.cola_ui = queue.Queue(); self._rx_stop = threading.Event(); self._rx_thr = None
        self._stream_stop = threading.Event(); self._stream_thr = None; self._lock = threading.Lock()
        self.tel_t = deque(maxlen=N_TEL); self.tel_pos = [deque(maxlen=N_TEL) for _ in range(3)]; self.tel_ref = [deque(maxlen=N_TEL) for _ in range(3)]
        self._t0 = time.monotonic(); self._ult_espejo = 0.0; self._R_tel = np.eye(3)
        self.ppr = [app.trans.cuentas_por_vuelta_salida] * 3

        top = tk.Frame(self.frame, bg=PANEL, highlightthickness=1, highlightbackground=tema.BORDER); top.pack(fill="x", padx=8, pady=(8, 4))
        tema.etiqueta(top, "Puerto", MUTED).pack(side="left", padx=(10, 2), pady=8)
        self.puerto = ttk.Combobox(top, width=22, font=tema.FUENTE); self.puerto.pack(side="left")
        tema.boton(top, "⟳", self.refrescar_puertos).pack(side="left", padx=4)
        f, self.baud = tema.campo(top, "baudios", 115200, 8); f.pack(side="left", padx=8)
        self.btn_con = tema.boton(top, "Conectar", self.alternar, tema.BTN_PLAY); self.btn_con.pack(side="left", padx=4)
        self.estado = tema.etiqueta(top, "● desconectado", tema.ERR); self.estado.pack(side="left", padx=10)
        tema.boton(top, "■ STOP / RESET", self.detener_todo, tema.ERR).pack(side="right", padx=10)

        cuerpo = tk.Frame(self.frame, bg=tema.BG); cuerpo.pack(fill="both", expand=True, padx=8)
        izq = tk.Frame(cuerpo, bg=PANEL, width=420, highlightthickness=1, highlightbackground=tema.BORDER); izq.pack(side="left", fill="y"); izq.pack_propagate(False)
        tema.etiqueta(izq, "MOTORES (ángulo del eje de salida)", MUTED).pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_pos = []; self.set_var = []; self.ppr_var = []
        for j in range(3):
            fila = tk.Frame(izq, bg=tema.BG, highlightthickness=1, highlightbackground=C_M[j]); fila.pack(fill="x", padx=10, pady=3)
            tk.Label(fila, text="M%d" % (j + 1), bg=tema.BG, fg=C_M[j], font=tema.FUENTE_T, width=3).pack(side="left", padx=4)
            col = tk.Frame(fila, bg=tema.BG); col.pack(side="left", fill="x", expand=True)
            lp = tk.Label(col, text="actual —   ref —", bg=tema.BG, fg=TEXT, font=tema.FUENTE, anchor="w"); lp.pack(anchor="w"); self.lbl_pos.append(lp)
            r = tk.Frame(col, bg=tema.BG); r.pack(anchor="w", pady=2)
            e, v = tema.entrada(r, "0", 7); e.pack(side="left"); self.set_var.append(v)
            tema.boton(r, "MOVER", lambda j=j: self.mover(j), C_M[j]).pack(side="left", padx=4)
            tema.boton(r, "cero", lambda j=j: self._json({"zero": j + 1})).pack(side="left")
            f, pv = tema.campo(col, "pulsos/vuelta", int(self.ppr[j]), 7, bg=tema.BG); f.pack(anchor="w"); self.ppr_var.append(pv)
        r = tk.Frame(izq, bg=PANEL); r.pack(fill="x", padx=10, pady=4)
        tema.boton(r, "Mover los 3", self.mover_todos, tema.BTN_HOV).pack(side="left", padx=2)
        tema.boton(r, "Pose de Vista 3D →", lambda: self.enviar_pose(self.app.trans.a_motor(self.app.theta)), tema.BTN_PLAY).pack(side="left", padx=2)
        tema.boton(r, "Cero todos", lambda: self._json({"zero": "all"})).pack(side="left", padx=2)

        tema.etiqueta(izq, "TRAYECTORIA PLANIFICADA", MUTED).pack(anchor="w", padx=10, pady=(10, 2))
        r = tk.Frame(izq, bg=PANEL); r.pack(fill="x", padx=10)
        f, self.hz = tema.campo(r, "Hz", 25, 4); f.pack(side="left")
        tema.boton(r, "Transmitir", lambda: self.transmitir(self.app.tray), tema.BTN_PLAY).pack(side="left", padx=2)
        tema.boton(r, "Detener", self.detener_stream, tema.BTN_PAUS).pack(side="left", padx=2)
        self.lbl_stream = tema.etiqueta(izq, "", MUTED); self.lbl_stream.pack(anchor="w", padx=10)
        self.bloquear = tk.BooleanVar(value=True); self.espejo = tk.BooleanVar(value=True)
        for v, t in ((self.bloquear, "bloquear comandos fuera de límites (CAD)"), (self.espejo, "espejo en vivo: telemetría → modelo CAD")):
            tk.Checkbutton(izq, text=t, variable=v, bg=PANEL, fg=TEXT, selectcolor=tema.BTN_BG, activebackground=PANEL, font=tema.FUENTE).pack(anchor="w", padx=10)
        tema.boton(izq, "Aplicar pulsos/vuelta", self._aplicar_ppr).pack(fill="x", padx=10, pady=(6, 8))

        der = tk.Frame(cuerpo, bg=tema.BG); der.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.fig = Figure(figsize=(8, 4.6), facecolor=tema.BG)
        self.ax = [self.fig.add_subplot(3, 1, i + 1) for i in range(3)]
        self.fig.subplots_adjust(left=0.08, right=0.985, top=0.95, bottom=0.08, hspace=0.55)
        self.canvas = FigureCanvasTkAgg(self.fig, master=der); self.canvas.get_tk_widget().pack(fill="both", expand=True)
        for j, a in enumerate(self.ax): a.set_title("Telemetría M%d — posición (línea) vs referencia (punteada), °" % (j + 1), fontsize=7)
        self.lineas = [(a.plot([], [], color=C_M[j], lw=1.2)[0], a.plot([], [], color=C_M[j], lw=1.0, ls="--")[0]) for j, a in enumerate(self.ax)]

        cons = tk.Frame(self.frame, bg=PANEL, highlightthickness=1, highlightbackground=tema.BORDER); cons.pack(fill="x", padx=8, pady=8)
        tema.etiqueta(cons, " Consola serial (TX azul / RX gris)", MUTED).pack(anchor="w")
        self.log = tk.Text(cons, height=7, bg=tema.BG, fg=TEXT, font=(tema.FAMILIA, 8), relief="flat", state="disabled")
        self.log.pack(fill="x", padx=4, pady=4)
        for tag, col in (("tx", "#58a6ff"), ("rx", MUTED), ("info", tema.OK), ("err", tema.ERR)): self.log.tag_config(tag, foreground=col)
        self.refrescar_puertos()
        if serial is None: self._log("err", "pyserial no instalado: pip install pyserial (o sudo pacman -S python-pyserial)")

    # ------------------------------------------------------------------ conexión
    def refrescar_puertos(self):
        ps = [p.device for p in serial.tools.list_ports.comports()] if serial else []
        import glob
        ps += [p for p in glob.glob("/dev/pts/*") if p not in ps and p.split("/")[-1].isdigit()][:6]          # pty de esp32_simulado.py
        self.puerto["values"] = ps
        if ps and not self.puerto.get(): self.puerto.set(ps[0])

    def alternar(self):
        self._desconectar() if self.ser is not None else self._conectar()

    def _conectar(self):
        if serial is None:
            messagebox.showerror("pyserial", "Instala pyserial: sudo pacman -S python-pyserial"); return
        try:
            self.ser = serial.Serial(self.puerto.get(), int(self.baud.get()), timeout=0.1)
        except Exception as ex:
            self.ser = None; self._log("err", "[err] no se pudo abrir: %s" % ex); return
        self._rx_stop.clear(); self._t0 = time.monotonic()
        self._rx_thr = threading.Thread(target=self._rx, daemon=True); self._rx_thr.start()
        self.estado.config(text="● conectado %s" % self.puerto.get(), fg=tema.OK); self.btn_con.config(text="Desconectar", bg=tema.BTN_PAUS)
        self._log("info", "[info] conectado a %s" % self.puerto.get()); self._bombear()
        self.frame.after(1500, self._sincronizar)                  # tras el reinicio del ESP32 al abrir el puerto

    def _desconectar(self):
        self.detener_stream(); self._rx_stop.set()
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None; self.estado.config(text="● desconectado", fg=tema.ERR); self.btn_con.config(text="Conectar", bg=tema.BTN_PLAY); self._log("info", "[info] desconectado")

    def _rx(self):
        buf = b""
        while not self._rx_stop.is_set():
            try:
                ch = self.ser.read(256)
            except Exception:
                break
            if not ch: continue
            buf += ch
            while b"\n" in buf:
                lin, buf = buf.split(b"\n", 1)
                t = lin.decode("utf-8", errors="replace").strip()
                if t: self.cola.put(t)

    def _bombear(self):
        if self.ser is None: return
        n = 0
        while n < 200:
            try:
                lin = self.cola.get_nowait()
            except queue.Empty:
                break
            n += 1; self._procesar(lin)
        while True:                                              # mensajes de los hilos hacia la UI (Tk solo se toca desde el hilo principal)
            try:
                m = self.cola_ui.get_nowait()
            except queue.Empty:
                break
            if m[0] == "stream": self.lbl_stream.config(text="enviados %d · t=%.2f / %.1f s" % m[1:])
            else: self._log(m[1], m[2])
        self._pintar_tel(); self.frame.after(60, self._bombear)

    # ------------------------------------------------------------------ telemetría
    def _procesar(self, lin):
        try:
            d = json.loads(lin)
        except ValueError:
            self._log("rx", "RX ← " + lin); return
        if isinstance(d, dict) and all(k in d for k in ("m1", "m2", "m3")):
            now = time.monotonic() - self._t0; self.tel_t.append(now); pos = []
            for j, k in enumerate(("m1", "m2", "m3")):
                sub = d[k]; p = float(sub.get("pos", 0)) / self.ppr[j] * 360.0; r = float(sub.get("posRef", 0)) / self.ppr[j] * 360.0
                self.tel_pos[j].append(p); self.tel_ref[j].append(r); pos.append(p)
                self.lbl_pos[j].config(text="actual %+8.2f°   ref %+8.2f°" % (p, r))
            if self.espejo.get() and now - self._ult_espejo > 0.25:
                self._ult_espejo = now; self._espejar(pos)
        else:
            self._log("rx", "RX ← " + lin)

    def _espejar(self, motor_deg):
        app = self.app
        try:
            th = app.trans.a_nnr(np.radians(motor_deg)); R = app.spm.fk(th, self._R_tel)
        except FueraDeEspacioDeTrabajo:
            return
        self._R_tel = R; app.fijar_pose(R, th, origen="robot")

    def _pintar_tel(self):
        """Redibuja la telemetría a lo sumo 4 veces por segundo y solo si esta pestaña está visible (un redibujado de matplotlib cuesta ~0.2 s)."""
        ahora = time.monotonic()
        if not self.tel_t or self.app.actual != "serial" or ahora - getattr(self, "_ult_pintado", 0.0) < 0.25: return
        self._ult_pintado = ahora
        t = np.array(self.tel_t)
        for j, (lp, lr) in enumerate(self.lineas):
            p, r = np.array(self.tel_pos[j]), np.array(self.tel_ref[j]); lp.set_data(t, p); lr.set_data(t, r)
            a = self.ax[j]; a.set_xlim(t[0], max(t[-1], t[0] + 5)); lo, hi = min(p.min(), r.min()), max(p.max(), r.max()); c, s = (lo + hi) / 2, max(10.0, hi - lo)
            a.set_ylim(c - s * 0.6, c + s * 0.6)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ envío
    def _log(self, tag, texto):
        self.log.config(state="normal"); self.log.insert("end", texto + "\n", tag)
        if int(self.log.index("end-1c").split(".")[0]) > 400: self.log.delete("1.0", "50.0")
        self.log.see("end"); self.log.config(state="disabled")

    def _escribir(self, obj):
        with self._lock:
            self.ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _json(self, obj):
        if self.ser is None:
            messagebox.showwarning("Sin conexión", "Conéctate al ESP32 primero."); return False
        try:
            self._escribir(obj); self._log("tx", "TX → " + json.dumps(obj, separators=(",", ":"))); return True
        except Exception as ex:
            self._log("err", "[err] TX falló: %s" % ex); return False

    def _validar(self, motor_deg):
        """(ok, motivo): la pose resultante de esos motores está dentro de límites según el CAD."""
        app = self.app
        try:
            th = app.trans.a_nnr(np.radians(motor_deg)); R = app.spm.fk(th, app.R)
        except FueraDeEspacioDeTrabajo as ex:
            return False, "FK sin solución (%s)" % ex
        ok, motivo, *_ = app.estado(R, th)
        return ok, motivo

    def _base_motores(self):
        if self.tel_pos[0]:
            return [self.tel_ref[j][-1] for j in range(3)]
        return list(np.degrees(self.app.trans.a_motor(self.app.theta)))

    def _num(self, j):
        try:
            return float(self.set_var[j].get().replace(",", "."))
        except ValueError:
            return None

    def mover(self, j):
        v = self._num(j)
        if v is None: return
        m = self._base_motores(); m[j] = v
        if self.bloquear.get():
            ok, why = self._validar(m)
            if not ok: self._log("err", "[bloqueado] M%d=%.1f°: %s" % (j + 1, v, why)); return
        self._json({"motor": j + 1, "pos": round(v, 2)})

    def mover_todos(self):
        v = [self._num(j) for j in range(3)]
        if None not in v: self.enviar_pose(v)

    def enviar_pose(self, motor_rad_o_deg):
        """Envía los 3 motores. Acepta radianes (como sale de Transmision.a_motor) y los pasa a grados."""
        m = list(np.degrees(motor_rad_o_deg)) if np.max(np.abs(motor_rad_o_deg)) < 7 else list(motor_rad_o_deg)
        for j in range(3): self.set_var[j].set("%.2f" % m[j])
        if self.bloquear.get():
            ok, why = self._validar(m)
            if not ok: self._log("err", "[bloqueado] pose fuera de límites: %s" % why); return
        self._json({"m1": round(m[0], 2), "m2": round(m[1], 2), "m3": round(m[2], 2)})
        self.app.ir_a("serial")

    def _aplicar_ppr(self):
        try:
            self.ppr = [float(v.get().replace(",", ".")) for v in self.ppr_var]
        except ValueError:
            messagebox.showwarning("Pulsos/vuelta", "Escribe números."); return
        self._log("info", "[info] pulsos/vuelta = %s" % self.ppr)
        self._sincronizar()

    def _sincronizar(self):
        """Manda los pulsos/vuelta al firmware (comando {"cfg":"ppr"}) para que convierta grados ↔ cuentas igual que esta interfaz."""
        if self.ser is not None:
            self._json({"cfg": "ppr", "val": [float(x) for x in self.ppr]})

    # ------------------------------------------------------------------ trayectoria
    def transmitir(self, tray):
        if tray is None:
            messagebox.showinfo("Sin trayectoria", "Planifica o importa una trayectoria primero."); return
        if self.ser is None:
            messagebox.showwarning("Sin conexión", "Conéctate al ESP32 primero."); return
        if self.bloquear.get() and tray.violaciones:
            self._log("err", "[bloqueado] la trayectoria tiene %d muestras fuera de límites (primera t=%.2f s: %s)" % (len(tray.violaciones), tray.t[tray.violaciones[0][0]], tray.violaciones[0][1])); return
        if self._stream_thr is not None and self._stream_thr.is_alive():
            return
        try:
            hz = max(1.0, min(200.0, float(self.hz.get())))
        except ValueError:
            hz = 25.0
        self._stream_stop.clear()
        self._stream_thr = threading.Thread(target=self._stream, args=(np.degrees(tray.motor), tray.t, hz), daemon=True); self._stream_thr.start()
        self._log("info", "[info] transmitiendo %.1f s a %.0f Hz" % (tray.duracion, hz))

    def _stream(self, q, t, hz):
        per = 1.0 / hz; t0 = time.monotonic(); n = 0; tf = t[-1]
        try:
            while not self._stream_stop.is_set():
                el = time.monotonic() - t0
                i = int(min(np.searchsorted(t, el), len(t) - 1))
                self._escribir({"m1": round(float(q[i, 0]), 2), "m2": round(float(q[i, 1]), 2), "m3": round(float(q[i, 2]), 2)}); n += 1
                self.cola_ui.put(("stream", n, el, tf))
                if el >= tf: break
                s = t0 + n * per - time.monotonic()
                if s > 0 and self._stream_stop.wait(s): break
        except Exception as ex:
            self.cola_ui.put(("log", "err", "[err] stream: %s" % ex))
        self.cola_ui.put(("log", "info", "[info] transmisión finalizada (%d consignas)" % n))

    def detener_stream(self):
        self._stream_stop.set()

    def detener_todo(self):
        self.detener_stream()
        if self.ser is not None: self._json({"cmd": "reset"})

    def activar(self):
        self.refrescar_puertos()
