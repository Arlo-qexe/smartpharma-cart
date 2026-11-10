"""Identificación de la planta de cada motor (motor + driver + carga) a partir de las capturas del firmware.

El firmware (`{"cmd":"capture",...}` + `{"cmd":"dump"}`, ver firmware/README_firmware.md) aplica un ciclo de trabajo en lazo abierto a UN motor y devuelve
(tiempo real, cuentas del encoder, ciclo de trabajo) a 1 kHz. Con eso se ajusta, por sentido de giro, el modelo que usa el diseño del control:

    τ · dω/dt + ω = K · (u − u0·sign(u))        ω = velocidad del eje de salida [°/s], u = ciclo de trabajo (-1..1)

  K   ganancia en régimen [°/s por unidad de ciclo de trabajo]      τ   constante de tiempo efectiva [s] (incluye el retraso del driver)
  u0  zona muerta cinética: ciclo de trabajo que hay que vencer      breakaway / u_stop   ciclo de trabajo al que el eje arranca / se detiene

Solo usa numpy (como el resto del simulador). Ver herramientas/identificar_motor.py para capturar y ajustar desde la línea de comandos.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np


@dataclass
class Captura:
    motor: int
    modo: str                    # step | ramp | tri
    dec: int
    duty: float                  # amplitud pedida
    pre_ms: int
    ms: int
    post_ms: int
    ppr: float
    dpc: float                   # ° del eje de salida por cuenta
    aborto: int                  # 0 completa · 1 superó max_deg · 2 stop/reset · 3 falla
    t: np.ndarray                # s desde la primera muestra
    pos: np.ndarray              # cuentas
    u: np.ndarray                # ciclo de trabajo realmente aplicado (-1..1; ya limitado por pwm_max)
    completa: bool = True        # False si el envío se cortó antes del ack

    @property
    def theta(self):
        return self.pos * self.dpc

    @property
    def etiqueta(self):
        return "motor%d_%s_%+.2f" % (self.motor, self.modo, self.duty)


# ------------------------------------------------------------------ lectura de lo que envía el firmware
def leer_capturas(lineas) -> list[Captura]:
    """Extrae todas las capturas de un flujo de líneas del puerto serie (o de un archivo): ignora telemetría y texto, y tolera cortes."""
    capturas, cab, filas, ok = [], None, [], False

    def cerrar(completa):
        nonlocal cab, filas
        if cab is not None and filas:
            a = np.array(filas, float)
            capturas.append(Captura(
                motor=int(cab["motor"]), modo=cab["mode"], dec=int(cab["dec"]), duty=float(cab["duty"]), pre_ms=int(cab["pre_ms"]), ms=int(cab["ms"]),
                post_ms=int(cab["post_ms"]), ppr=float(cab["ppr"]), dpc=float(cab["dpc"]), aborto=int(cab["abort"]),
                t=(a[:, 0] - a[0, 0]) * 1e-6, pos=a[:, 1], u=a[:, 2] * 1e-3, completa=completa and len(filas) == int(cab["n"])))
        cab, filas = None, []

    for linea in lineas:
        linea = linea.strip()
        if not linea.startswith("{"):
            continue
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        if "cap" in d:
            cerrar(False)                                       # una cabecera nueva sin ack del anterior: el anterior quedó incompleto
            cab = d["cap"]
        elif "c" in d and cab is not None:
            v = d["c"]
            filas += [v[i:i + 3] for i in range(0, len(v) - 2, 3)]
        elif d.get("ack") == "dump":
            cerrar(True)
    cerrar(False)
    return capturas


def reescalar(cap: Captura, ppr: float) -> Captura:
    """Misma captura con otras cuentas por vuelta: las cuentas son crudas, así que solo cambia el factor a grados (dpc = 360/ppr). Sirve cuando la placa
    tenía un ppr equivocado (p. ej. el 1000 de fábrica) durante la captura. K y la velocidad libre escalan con 1000/ppr; u0, τ y el arranque no dependen de él."""
    from dataclasses import replace
    return replace(cap, ppr=float(ppr), dpc=360.0 / float(ppr))


def guardar_csv(cap: Captura, ruta):
    """CSV con la cabecera como comentarios '# clave=valor' (se puede volver a ajustar sin el firmware)."""
    meta = dict(motor=cap.motor, mode=cap.modo, dec=cap.dec, duty=cap.duty, pre_ms=cap.pre_ms, ms=cap.ms, post_ms=cap.post_ms, ppr=cap.ppr, dpc=cap.dpc,
                abort=cap.aborto, completa=int(cap.completa))
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        for k, v in meta.items():
            f.write("# %s=%s\n" % (k, v))
        f.write("t_s,pos_cuentas,duty\n")
        for t, p, u in zip(cap.t, cap.pos, cap.u):
            f.write("%.6f,%d,%.4f\n" % (t, p, u))


def cargar_csv(ruta) -> Captura:
    meta, filas = {}, []
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            if linea.startswith("#"):
                k, _, v = linea[1:].strip().partition("=")
                meta[k] = v
            elif linea[:1].isdigit() or linea[:1] == "-":
                filas.append([float(x) for x in linea.split(",")])
    a = np.array(filas)
    return Captura(motor=int(meta["motor"]), modo=meta["mode"], dec=int(meta["dec"]), duty=float(meta["duty"]), pre_ms=int(meta["pre_ms"]),
                   ms=int(meta["ms"]), post_ms=int(meta["post_ms"]), ppr=float(meta["ppr"]), dpc=float(meta["dpc"]), aborto=int(meta["abort"]),
                   t=a[:, 0], pos=a[:, 1], u=a[:, 2], completa=bool(int(meta.get("completa", 1))))


# ------------------------------------------------------------------ ajuste de un escalón
@dataclass
class AjusteEscalon:
    duty: float
    K_efectiva: float            # velocidad en régimen [°/s] a ese ciclo de trabajo
    tau: float                   # s
    rms_deg: float               # residuo del ajuste de la posición
    ventana_s: float             # duración del tramo ajustado
    aviso: str = ""


def _g(t, tau):
    """Respuesta de posición a un escalón de velocidad de pendiente 1 en un sistema de 1er orden: t - τ(1 - e^{-t/τ})."""
    return t - tau * (1.0 - np.exp(-t / tau))


def ajustar_escalon(cap: Captura) -> AjusteEscalon | None:
    """θ(t) = θ0 + Ke·g(t - t_inicio; τ) sobre el tramo con el ciclo de trabajo aplicado. Para cada τ, (θ0, Ke) salen por mínimos cuadrados lineales."""
    act = np.abs(cap.u) > 0.5 * np.abs(cap.u).max()
    if act.sum() < 10:
        return None
    i0, i1 = int(np.argmax(act)), int(len(act) - 1 - np.argmax(act[::-1]))
    t = cap.t[i0:i1 + 1] - cap.t[i0]
    th = cap.theta[i0:i1 + 1]
    if len(t) < 10:
        return None
    mejor = None
    for tau in np.geomspace(0.003, 1.5, 160):
        A = np.column_stack([np.ones_like(t), _g(t, tau)])
        coef, *_ = np.linalg.lstsq(A, th, rcond=None)
        sse = float(np.sum((A @ coef - th) ** 2))
        if mejor is None or sse < mejor[0]:
            mejor = (sse, tau, coef)
    # refinamiento alrededor del mejor τ
    _, t0, _ = mejor
    for tau in np.linspace(t0 * 0.85, t0 * 1.15, 31):
        A = np.column_stack([np.ones_like(t), _g(t, tau)])
        coef, *_ = np.linalg.lstsq(A, th, rcond=None)
        sse = float(np.sum((A @ coef - th) ** 2))
        if sse < mejor[0]:
            mejor = (sse, tau, coef)
    sse, tau, coef = mejor
    u_med = float(np.mean(cap.u[i0:i1 + 1]))
    aviso = ""
    if abs(coef[1]) * t[-1] < 20 * cap.dpc:
        aviso = "casi no giró (≤ 20 cuentas): ciclo de trabajo bajo o motor trabado"
    elif t[-1] < 4 * tau:
        aviso = "ventana corta (%.2f s < 4τ): τ y K poco fiables; alarga ms" % t[-1]
    return AjusteEscalon(duty=u_med, K_efectiva=float(coef[1]), tau=float(tau), rms_deg=math.sqrt(sse / len(t)), ventana_s=float(t[-1]), aviso=aviso)


# ------------------------------------------------------------------ rampa / triángulo: arranque y parada
def _retraso_deteccion(K: float, tau: float, pendiente: float, umbral_deg: float) -> float:
    """Tiempo [s] que tarda un motor de 1er orden (K, τ), al que se le sube el ciclo de trabajo con 'pendiente' [1/s] justo por encima de su zona muerta,
    en recorrer 'umbral_deg'. posición(t) = K·s·(t²/2 - τ·t + τ²·(1 - e^{-t/τ})). Se resuelve por bisección."""
    f = lambda t: K * pendiente * (t * t / 2 - tau * t + tau * tau * (1 - math.exp(-t / tau))) - umbral_deg
    lo, hi = 0.0, 10.0
    if f(hi) < 0:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return hi


def analizar_rampa(cap: Captura, cuentas_umbral: int = 3, K: float | None = None, tau: float | None = None):
    """(breakaway, u_stop): ciclo de trabajo (valor absoluto) al que el eje arranca en la subida y al que se detiene en la bajada (solo 'tri').
    None si no se pudo determinar. Detectar el arranque con un umbral de cuentas lo retrasa: si se dan K y τ del motor (de los escalones) se
    descuenta ese retraso suponiendo fricción estática ≈ cinética; sin ellos el valor sale algo alto. u_stop es aproximado (la inercia lo baja)."""
    u = np.abs(cap.u)
    if len(u) < 20 or u.max() <= 0:
        return None, None
    ipk = int(np.argmax(u))
    inicio = int(np.argmax(u > 0))                                     # primera muestra con ciclo de trabajo aplicado
    fin = int(len(u) - 1 - np.argmax((u > 0)[::-1]))                   # última
    pos = cap.pos
    base = pos[:inicio].mean() if inicio > 0 else pos[0]
    sube = np.where(np.abs(pos[inicio:ipk + 1] - base) >= cuentas_umbral)[0]
    breakaway = float(u[inicio + sube[0]]) if len(sube) else None
    if breakaway is not None and K and tau:
        subida_s = cap.ms * 1e-3 / (2 if cap.modo == "tri" else 1)
        pendiente = float(u.max()) / subida_s
        breakaway = max(0.0, breakaway - pendiente * _retraso_deteccion(abs(K), tau, pendiente, cuentas_umbral * cap.dpc))
    u_stop = None
    if cap.modo == "tri" and fin - ipk > 10:
        w = max(2, int(round(0.04 / max(1e-9, float(np.median(np.diff(cap.t)))))))          # ventana de ~40 ms
        v = np.abs(pos[ipk + w:] - pos[ipk:-w]) * cap.dpc / (cap.t[ipk + w:] - cap.t[ipk:-w])
        umbral = 3.0 * cap.dpc / 0.04                                                         # ≈ 3 cuentas en 40 ms
        mov = [j for j in np.where(v > umbral)[0] if ipk + j + w // 2 <= fin]                 # solo mientras todavía hay ciclo de trabajo
        if mov:
            u_stop = float(u[ipk + int(mov[-1]) + w // 2])
    return breakaway, u_stop


# ------------------------------------------------------------------ modelo de un motor
@dataclass
class ModeloMotor:
    motor: int
    K_pos: float | None = None          # °/s por unidad de ciclo de trabajo, sentido +
    K_neg: float | None = None
    u0_pos: float | None = None         # zona muerta cinética
    u0_neg: float | None = None
    tau_pos: float | None = None        # s
    tau_neg: float | None = None
    tau_std: float | None = None
    breakaway_pos: float | None = None  # ciclo de trabajo de arranque (estático)
    breakaway_neg: float | None = None
    u_stop_pos: float | None = None
    u_stop_neg: float | None = None
    rms_deg: float = 0.0
    n_escalones: int = 0
    avisos: list = field(default_factory=list)
    sugerencias_firmware: dict = field(default_factory=dict)

    def a_json(self, ruta):
        Path(ruta).write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False), encoding="utf-8")


def _regresion(duty, ke):
    """Ke = K·(|u| - u0) con el signo del ciclo de trabajo → (K, u0, R²) por mínimos cuadrados sobre |u|."""
    x, y = np.abs(np.asarray(duty, float)), np.abs(np.asarray(ke, float)) * np.sign(np.asarray(ke, float) * np.asarray(duty, float))
    if len(x) < 2 or np.ptp(x) < 1e-3:
        return None
    K, b = np.polyfit(x, y, 1)
    r2 = 1.0 - np.sum((y - (K * x + b)) ** 2) / max(1e-12, np.sum((y - y.mean()) ** 2))
    return float(K), (float(-b / K) if abs(K) > 1e-9 else None), float(r2)


def ajustar_motor(capturas: list[Captura]) -> ModeloMotor:
    """Modelo de un motor a partir de varias capturas del MISMO motor (escalones de ambos signos y, mejor aún, un triángulo por sentido)."""
    if not capturas:
        raise ValueError("no hay capturas")
    motores = {c.motor for c in capturas}
    if len(motores) != 1:
        raise ValueError("las capturas son de varios motores: %s" % sorted(motores))
    mod = ModeloMotor(motor=motores.pop())
    pprs = sorted({round(c.ppr, 1) for c in capturas})
    if len(pprs) > 1:
        mod.avisos.append("hay capturas con ppr distinto (%s): no se deben mezclar (cada ppr cambia los °/s); separa las corridas en carpetas o usa --ppr para unificarlas"
                          % ", ".join("%g" % p for p in pprs))
    esc, rampas = {+1: [], -1: []}, []
    for c in capturas:
        if not c.completa:
            mod.avisos.append("%s: captura incompleta, se descarta" % c.etiqueta)
            continue
        if c.aborto not in (0, 1):
            mod.avisos.append("%s: abortada (código %d), se descarta" % (c.etiqueta, c.aborto))
            continue
        if c.modo == "step":
            a = ajustar_escalon(c)
            if a is None:
                mod.avisos.append("%s: sin tramo activo" % c.etiqueta)
                continue
            if a.aviso:
                mod.avisos.append("%s: %s" % (c.etiqueta, a.aviso))
            if not a.aviso.startswith("casi no giró"):
                esc[1 if a.duty > 0 else -1].append(a)
        else:
            rampas.append(c)
            if c.aborto == 1 and c.modo == "tri":
                mod.avisos.append("%s: se cortó por el límite de giro (max_deg) antes de bajar la rampa; la «parada» no es fiable y se omite" % c.etiqueta)
    todos = esc[1] + esc[-1]
    mod.n_escalones = len(todos)
    if todos:
        mod.rms_deg = float(np.sqrt(np.mean([a.rms_deg ** 2 for a in todos])))
        taus = np.array([a.tau for a in todos])
        mod.tau_std = float(taus.std())
    for signo, lado in ((+1, "pos"), (-1, "neg")):
        if not esc[signo]:
            continue
        r = _regresion([a.duty for a in esc[signo]], [a.K_efectiva for a in esc[signo]])
        setattr(mod, "tau_" + lado, float(np.mean([a.tau for a in esc[signo]])))
        if r is None:
            mod.avisos.append("sentido %s: hace falta más de un ciclo de trabajo distinto para separar K y u0" % lado)
            continue
        K, u0, r2 = r
        setattr(mod, "K_" + lado, K)
        setattr(mod, "u0_" + lado, u0)
        if r2 < 0.98:
            mod.avisos.append("sentido %s: la velocidad no es lineal con el ciclo de trabajo (R²=%.3f): saturación, tope de pwm_max o carga variable" % (lado, r2))
    for c in rampas:                                                    # arranque/parada, ahora que se conocen K y τ
        lado = "pos" if c.duty > 0 else "neg"
        ba, us = analizar_rampa(c, K=getattr(mod, "K_" + lado), tau=getattr(mod, "tau_" + lado))
        if c.aborto == 1:
            us = None                                                   # abortada a mitad de la bajada: lo último que giró no es la parada real
        if ba is not None:
            setattr(mod, "breakaway_" + lado, ba)
        if us is not None:
            setattr(mod, "u_stop_" + lado, us)
    for lado in ("pos", "neg"):
        K = getattr(mod, "K_" + lado)
        if K is not None and K <= 0:
            mod.avisos.append("sentido %s: K ≤ 0 → el encoder cuenta al revés del PWM (cfg enc_sign = -1, o {\"cmd\":\"polarity\",\"apply\":1})" % lado)
    if mod.K_pos and mod.K_neg and not 0.7 < abs(mod.K_neg / mod.K_pos) < 1.4:
        mod.avisos.append("K muy distinta según el sentido (%.0f vs %.0f °/s): carga asimétrica (gravedad) o driver desequilibrado" % (mod.K_pos, mod.K_neg))
    mod.sugerencias_firmware = sugerir_firmware(mod)
    return mod


def sugerir_firmware(m: ModeloMotor) -> dict:
    """Valores iniciales para {"cfg":...}. pwm_min = ciclo de trabajo en que el eje arranca (README_firmware, paso 5); kff compensa que el firmware
    mapea u → pwm_min + (1-pwm_min)·u, así que la pendiente efectiva es K·(1-pwm_min). Son un punto de partida: la sintonía se afina con el lazo cerrado."""
    K = [abs(k) for k in (m.K_pos, m.K_neg) if k]
    arr = [b for b in (m.breakaway_pos, m.breakaway_neg) if b is not None] or [u for u in (m.u0_pos, m.u0_neg) if u is not None]
    if not K:
        return {}
    pwm_min = float(np.clip(np.mean(arr), 0.0, 0.5)) if arr else 0.0
    Km = float(np.mean(K))
    return {"pwm_min": round(pwm_min, 3), "kff": round(1.0 / (Km * (1.0 - pwm_min)), 6), "K_media_degs": round(Km, 1),
            "tau_s": round(float(np.mean([t for t in (m.tau_pos, m.tau_neg) if t])), 4) if (m.tau_pos or m.tau_neg) else None,
            "vel_max_libre_degs": round(Km * (1.0 - pwm_min), 1)}


def informe(m: ModeloMotor) -> str:
    f = lambda x, fmt="%.3f": "—" if x is None else fmt % x
    lin = ["Motor %d  (%d escalones, residuo %.2f° rms)" % (m.motor, m.n_escalones, m.rms_deg),
           "            sentido +      sentido -",
           "  K [°/s]   %-13s  %s" % (f(m.K_pos, "%.1f"), f(m.K_neg, "%.1f")),
           "  τ [ms]    %-13s  %s" % (f(m.tau_pos and m.tau_pos * 1e3, "%.1f"), f(m.tau_neg and m.tau_neg * 1e3, "%.1f")),
           "  u0        %-13s  %s   (zona muerta cinética)" % (f(m.u0_pos), f(m.u0_neg)),
           "  arranque  %-13s  %s   (ciclo de trabajo al que el eje empieza a girar)" % (f(m.breakaway_pos), f(m.breakaway_neg)),
           "  parada    %-13s  %s   (al que se detiene al bajar)" % (f(m.u_stop_pos), f(m.u_stop_neg))]
    if m.sugerencias_firmware:
        s = m.sugerencias_firmware
        lin.append("  sugerido para el firmware: pwm_min=%s  kff=%s  (velocidad libre ≈ %s °/s, τ ≈ %s s)" % (s["pwm_min"], s["kff"], s["vel_max_libre_degs"], s["tau_s"]))
    lin += ["  AVISO: " + a for a in m.avisos]
    return "\n".join(lin)
