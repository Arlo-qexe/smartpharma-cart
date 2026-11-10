"""Cliente de la captura para identificación: manda las órdenes al firmware por una línea serie, recoge los datos y ejecuta el plan estándar.

`puerto` es cualquier objeto con la interfaz de pyserial (`write(bytes)`, `readline() -> bytes` con timeout), para poder probarlo sin placa.
Uso desde la línea de comandos: herramientas/identificar_motor.py. La parte numérica (ajuste del modelo) está en spm/identificacion.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .identificacion import Captura, ajustar_motor, analizar_rampa, guardar_csv, leer_capturas


class ErrorCaptura(Exception):
    pass


class Capturador:
    def __init__(self, puerto, log=print, reloj=time.monotonic, dormir=time.sleep):
        self.p, self.log, self.reloj, self.dormir = puerto, log, reloj, dormir

    # ---------------------------------------------------------------- bajo nivel
    def enviar(self, obj: dict) -> None:
        self.p.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _linea(self) -> str:
        raw = self.p.readline()
        return raw.decode("utf-8", errors="replace").strip() if raw else ""

    def esperar(self, cond, timeout: float, que: str, crudo: list | None = None) -> dict:
        """Lee líneas hasta que `cond(dict)` sea verdadera. Un {"err":...} del firmware lanza ErrorCaptura. Si se da `crudo`, guarda ahí TODAS las líneas leídas."""
        limite = self.reloj() + timeout
        while self.reloj() < limite:
            linea = self._linea()
            if not linea:
                continue
            if crudo is not None:
                crudo.append(linea)
            if not linea.startswith("{"):
                continue
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if "err" in d:
                raise ErrorCaptura("el firmware respondió error: %s" % linea)
            if cond(d):
                return d
        raise ErrorCaptura("sin respuesta del firmware (%s) en %.1f s" % (que, timeout))

    # ---------------------------------------------------------------- comandos
    def estado(self) -> dict:
        self.enviar({"cmd": "status"})
        return self.esperar(lambda d: d.get("info") == "status", 3.0, "status")

    def reset(self) -> None:
        self.enviar({"cmd": "reset"})
        self.esperar(lambda d: d.get("ack") == "reset", 3.0, "reset")

    def posicion(self, motor: int) -> int:
        """Cuentas actuales del motor, de la telemetría (20 Hz)."""
        d = self.esperar(lambda d: ("m%d" % motor) in d, 3.0, "telemetría")
        return int(d["m%d" % motor]["pos"])

    def capturar(self, motor: int, modo: str, duty: float, ms: int, pre_ms: int = 100, post_ms: int = 500, dec: int = 1,
                 max_deg: float = 720, hold: bool = False) -> Captura:
        self.enviar({"cmd": "capture", "motor": motor, "mode": modo, "duty": round(duty, 4), "ms": ms, "pre_ms": pre_ms, "post_ms": post_ms,
                     "dec": dec, "max_deg": max_deg, "hold": int(hold)})
        self.esperar(lambda d: d.get("ack") == "capture", 3.0, "ack de capture")
        fin = self.esperar(lambda d: d.get("ack") == "capture_done", (pre_ms + ms + post_ms) / 1000 + 5.0, "fin de la captura")
        if fin.get("abort") == 2:
            raise ErrorCaptura("la captura se interrumpió (stop/reset)")
        self.enviar({"cmd": "dump"})
        crudo: list[str] = []
        self.esperar(lambda d: d.get("ack") == "dump", 20.0, "datos de la captura", crudo)
        caps = leer_capturas(crudo)
        if not caps:
            raise ErrorCaptura("no llegaron datos de la captura")
        return caps[-1]


# ---------------------------------------------------------------- plan estándar
def niveles_de_escalon(breakaway: float, tope: float) -> list[float]:
    """Cuatro ciclos de trabajo por encima de la zona muerta, hasta el tope. Falla si no hay margen para separar K de u0."""
    if tope - breakaway < 0.1:
        raise ErrorCaptura("el motor solo arranca cerca del tope (arranque %.2f, tope %.2f): sube pwm_max o revisa alimentación/atasco" % (breakaway, tope))
    return [round(float(x), 2) for x in np.linspace(breakaway + 0.2 * (tope - breakaway), tope, 4)]


def ejecutar_plan(cap: Capturador, motor: int, salida, tope: float | None = None, ms: int = 500, max_deg: float = 720, max_deg_rampa: float = 1080,
                  ms_rampa: int = 4000, hold: bool = False, log=print):
    """Rampa triangular por sentido (halla la zona muerta) → escalones por encima de ella → modelo. Guarda cada captura como CSV en `salida`.
    Devuelve (capturas, ModeloMotor)."""
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    est = cap.estado()
    pwm_max = float(est["pwm_max"][motor - 1])
    if abs(float(est["ppr"][motor - 1]) - 1000) < 1e-6:
        log("AVISO: ppr = 1000 (valor de fábrica). Si no lo has medido, los °/s del modelo estarán escalados mal: usa 'ppr' primero.")
    tope = min(tope if tope is not None else pwm_max, pwm_max)
    if tope < 0.15:
        raise ErrorCaptura("pwm_max (%.2f) es demasiado bajo para identificar: súbelo con {\"cfg\":\"pwm_max\",\"motor\":%d,\"val\":0.6}" % (pwm_max, motor))
    log("Motor %d: ciclo de trabajo máximo %.2f (pwm_max %.2f), giro máximo por captura %.0f°." % (motor, tope, pwm_max, max_deg))
    cap.reset()
    caps, arranque = [], {}

    def guardar(c: Captura):
        ruta = salida / ("%s.csv" % c.etiqueta)
        guardar_csv(c, ruta)
        caps.append(c)
        return ruta

    for signo in (+1, -1):                                              # fase 1: rampa → zona muerta de cada sentido
        log("  rampa %s hasta %.2f ..." % ("+" if signo > 0 else "-", tope))
        c = cap.capturar(motor, "tri", signo * tope, ms_rampa, pre_ms=100, post_ms=300, dec=4, max_deg=max_deg_rampa, hold=hold)
        guardar(c)
        ba, _ = analizar_rampa(c)
        if ba is None:
            raise ErrorCaptura("el motor no gira ni con ciclo de trabajo %.2f en sentido %s: revisa alimentación (12 V), cableado del driver, atasco o el tope pwm_max" % (
                tope, "+" if signo > 0 else "-"))
        arranque[signo] = ba
        log("    arranque aproximado: %.2f" % ba)
        cap.dormir(0.4)
    for signo in (+1, -1):                                              # fase 2: escalones por encima de la zona muerta
        for d in niveles_de_escalon(arranque[signo], tope):
            log("  escalón %+.2f (%d ms) ..." % (signo * d, ms))
            guardar(cap.capturar(motor, "step", signo * d, ms, pre_ms=100, post_ms=600, dec=1, max_deg=max_deg, hold=hold))
            cap.dormir(0.4)
    modelo = ajustar_motor(caps)
    return caps, modelo


def medir_ppr(cap: Capturador, motor: int, pedir_vuelta, log=print) -> float:
    """Cuentas por vuelta del eje de salida: lee la posición, pide girar el eje UNA vuelta a mano (`pedir_vuelta()` bloquea hasta que se hizo) y lee otra vez."""
    p0 = cap.posicion(motor)
    pedir_vuelta()
    p1 = cap.posicion(motor)
    ppr = abs(p1 - p0)
    log("Contadas %d cuentas en una vuelta." % ppr)
    return float(ppr)
