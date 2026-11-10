"""Placa SPM simulada: un «puerto serie» falso (write/readline, como pyserial) que habla el protocolo del firmware (firmware/README_firmware.md).

Sirve para probar interfaces y herramientas sin hardware y para reproducir FALLAS: con `falla="sin_giro"` la placa manda PWM pero el eje no gira (driver sin
alimentación, STBY/PWMA en bajo, cable del motor suelto...), con "solo_positivo"/"solo_negativo" un solo sentido funciona (un cable de IN1/IN2 suelto),
con "encoder_invertido" el encoder cuenta al revés. `factor_tiempo` > 1 hace correr el tiempo simulado más rápido que el real (la telemetría lleva el tiempo simulado).
Solo simula lo que necesita la identificación: lazo abierto (`duty`), `polarity`, `capture`/`dump`, `status`, `reset`, `stop`, `zero`, `cfg` y la telemetría a 20 Hz.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time

NM = 3
TS = 0.001


class PlacaSimulada:
    def __init__(self, falla: str = "ninguna", K: float = 900.0, tau: float = 0.06, u0: float = 0.08, ppr: float = 1540.0, pwm_max: float = 0.6,
                 factor_tiempo: float = 1.0):
        self.falla, self.K, self.tau, self.u0, self.factor = falla, K, tau, u0, factor_tiempo
        self.ppr = [ppr] * NM
        self.pwm_max = [pwm_max] * NM
        self.ctrl = ["pid"] * NM
        self.enc_sign = [1] * NM
        self.duty = [0.0] * NM
        self.theta = [0.0] * NM
        self.omega = [0.0] * NM
        self.estado = "run"
        self.objetivo: list[float | None] = [None] * NM          # consigna de posición [°] (lazo cerrado simplificado: no es el PID del firmware)
        self.t_ms = 0
        self.timeout = 0.05
        self._rx: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._cap = None
        self._activo = True
        self.enviados: list[dict] = []
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="placa-simulada")
        self._hilo.start()
        self._poner({"info": "SPM ESP32-S3 (simulada) listo", "control_hz": 1000})

    # ---------------------------------------------------------------- interfaz tipo pyserial
    @property
    def in_waiting(self) -> int:
        return self._rx.qsize()

    def readline(self) -> bytes:
        try:
            return (self._rx.get(timeout=self.timeout) + "\n").encode()
        except queue.Empty:
            return b""

    def write(self, datos: bytes) -> int:
        for linea in datos.decode("utf-8", errors="replace").split("\n"):
            if linea.strip().startswith("{"):
                self._comando(linea.strip())
        return len(datos)

    def reset_input_buffer(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._activo = False

    # ---------------------------------------------------------------- física
    def _poner(self, d: dict) -> None:
        self._rx.put(json.dumps(d, separators=(",", ":")))

    def _u_pedido(self, m: int) -> float:
        """Como el firmware: el duty en lazo abierto solo vale en estado «run» (en idle/fault los motores quedan libres)."""
        if self.estado != "run":
            return 0.0
        if self.ctrl[m] == "open":
            return self.duty[m]
        if self.objetivo[m] is None:
            return 0.0
        e = self.objetivo[m] - self.theta[m]                              # servo P con fricción compensada, solo para que las pruebas de la interfaz tengan lazo cerrado
        if abs(e) < 0.15:
            return 0.0
        u = max(-1.0, min(1.0, 0.05 * e))
        return math.copysign(max(abs(u), 0.06), u)

    def _u_efectivo(self, m: int, u: float) -> float:
        if self.falla == "sin_giro":
            return 0.0
        if self.falla == "solo_positivo" and u < 0:
            return 0.0
        if self.falla == "solo_negativo" and u > 0:
            return 0.0
        return u

    def _paso(self, m: int, u: float, dt: float = TS) -> None:
        u = self._u_efectivo(m, max(-self.pwm_max[m], min(self.pwm_max[m], u)))
        ue = u - math.copysign(self.u0, u) if abs(u) > self.u0 else 0.0
        self.omega[m] += dt * (self.K * ue - self.omega[m]) / self.tau
        self.theta[m] += self.omega[m] * dt

    def _cuentas(self, m: int) -> int:
        signo = -1 if self.falla == "encoder_invertido" else 1
        return int(round(signo * self.enc_sign[m] * self.theta[m] / 360.0 * self.ppr[m]))

    def _telemetria(self) -> dict:
        d = {"t": self.t_ms, "st": self.estado}
        for m in range(NM):
            u = self._u_pedido(m)
            u = max(-self.pwm_max[m], min(self.pwm_max[m], u))
            obj = self.objetivo[m]
            d["m%d" % (m + 1)] = {"posRef": int(round(obj / 360.0 * self.ppr[m])) if obj is not None else 0, "pos": self._cuentas(m), "ok": True,
                                  "u": int(round(u * 1000)), "e": round(obj - self.theta[m], 2) if obj is not None else 0.0}
        d["jit"], d["miss"] = 0, 0
        return d

    def _bucle(self) -> None:
        while self._activo:
            with self._lock:
                if self.estado != "capture":
                    for _ in range(50):
                        for m in range(NM):
                            self._paso(m, self._u_pedido(m))
                    self.t_ms += 50
                    self._poner(self._telemetria())
            time.sleep(0.05 / self.factor)

    # ---------------------------------------------------------------- comandos
    def _comando(self, linea: str) -> None:
        try:
            d = json.loads(linea)
        except ValueError:
            self._poner({"err": "json"})
            return
        self.enviados.append(d)
        with self._lock:
            self._atender(d)

    def _atender(self, d: dict) -> None:
        cmd = d.get("cmd")
        if cmd == "status":
            self._poner({"info": "status", "st": self.estado, "fault": 0, "hz": 1000, "telem_hz": 20, "ppr": self.ppr, "ctrl": [0, 0, 0],
                         "pwm_max": self.pwm_max, "enc_sign": self.enc_sign})
        elif cmd in ("reset", "enable"):
            self.estado = "run"
            self.duty = [0.0] * NM                       # resetMotor() del firmware pone el duty de lazo abierto a 0
            self.objetivo = list(self.theta)             # ...y el objetivo = la posición actual
            self._poner({"ack": cmd})
        elif cmd == "stop":
            self.estado = "idle"
            self.duty = [0.0] * NM
            self._poner({"ack": "stop"})
        elif cmd == "polarity":
            self._polaridad(bool(d.get("apply")))
        elif cmd == "capture":
            self._capturar(d)
        elif cmd == "dump":
            self._volcar()
        elif cmd == "save":
            self._poner({"ack": "save"})
        elif "cfg" in d:
            self._cfg(d)
        elif "duty" in d:
            n = int(d["duty"])
            if 1 <= n <= NM:
                self.duty[n - 1] = max(-1.0, min(1.0, float(d.get("val", 0))))
                self._poner({"ack": "duty"})
        elif "motor" in d and "pos" in d:
            n = int(d["motor"])
            if 1 <= n <= NM:
                self.objetivo[n - 1] = float(d["pos"])
        elif any(k in d for k in ("m1", "m2", "m3")):
            for m, k in enumerate(("m1", "m2", "m3")):
                if k in d:
                    self.objetivo[m] = float(d[k])
        elif "zero" in d:
            for m in range(NM):
                self.theta[m] = 0.0
            self._poner({"ack": "zero"})
        else:
            self._poner({"err": "unknown"})

    def _cfg(self, d: dict) -> None:
        nombre, val = d["cfg"], d.get("val")
        motores = [int(d["motor"]) - 1] if "motor" in d else list(range(NM))
        for m in motores:
            x = val[m] if isinstance(val, list) else val
            if nombre == "ppr":
                self.ppr[m] = float(x)
            elif nombre == "pwm_max":
                self.pwm_max[m] = max(0.05, min(1.0, float(x)))
            elif nombre == "ctrl":
                self.ctrl[m] = x
            elif nombre == "enc_sign":
                self.enc_sign[m] = -1 if float(x) < 0 else 1
            self.duty[m] = 0.0                           # cfgSet() también reinicia el motor (duty de lazo abierto a 0), como el firmware
            self.objetivo[m] = self.theta[m]
        self._poner({"ack": "cfg", "name": nombre})

    def _polaridad(self, aplicar: bool) -> None:
        for m in range(NM):
            ant = self._cuentas(m)
            for k in range(430):
                self._paso(m, 0.4 if k < 180 else 0.0)
            dp = self._cuentas(m) - ant
            res = "sin_movimiento" if abs(dp) < 3 else ("ok" if dp > 0 else "invertir_encoder")
            if aplicar and dp < -2:
                self.enc_sign[m] = -self.enc_sign[m]
            self.omega[m] = 0.0
            self._poner({"polarity": m + 1, "counts": dp, "result": res})

    def _capturar(self, d: dict) -> None:
        m, modo = int(d["motor"]) - 1, d.get("mode", "step")
        duty, ms = max(-1, min(1, float(d["duty"]))), int(d["ms"])
        pre, post, dec = int(d.get("pre_ms", 100)), int(d.get("post_ms", 500)), int(d.get("dec", 1))
        tope = float(d.get("max_deg", 360))
        pos0, muestras, th0 = self._cuentas(m), [], self.theta[m]
        abort = 0
        for k in range(pre + ms + post):
            t1 = k - pre
            f = 0.0
            if 0 <= t1 < ms:
                f = 1.0 if modo == "step" else (t1 / ms if modo == "ramp" else 1 - abs(2 * t1 / ms - 1))
            u = max(-self.pwm_max[m], min(self.pwm_max[m], duty * f))
            self._paso(m, u)
            if (k + 1) % dec == 0:
                muestras.append((k * 1000, self._cuentas(m), int(round(u * 1000))))
            if abs(self.theta[m] - th0) > tope:
                abort = 1
                break
        self.omega[m] = 0.0
        self._cap = dict(motor=m + 1, mode=modo, dec=dec, duty=duty, pre_ms=pre, ms=ms, post_ms=post, ppr=self.ppr[m], muestras=muestras, abort=abort)
        self._poner({"ack": "capture", "motor": m + 1, "mode": modo, "samples": len(muestras)})
        self._poner({"ack": "capture_done", "motor": m + 1, "n": len(muestras), "abort": abort})
        _ = pos0

    def _volcar(self) -> None:
        c = self._cap
        if not c:
            self._poner({"err": "dump", "hint": "sin datos: usa capture primero"})
            return
        self._poner({"cap": {"motor": c["motor"], "mode": c["mode"], "n": len(c["muestras"]), "dec": c["dec"], "duty": c["duty"], "pre_ms": c["pre_ms"],
                             "ms": c["ms"], "post_ms": c["post_ms"], "ppr": c["ppr"], "dpc": 360.0 / c["ppr"], "abort": c["abort"]}})
        m = c["muestras"]
        for i in range(0, len(m), 8):
            self._poner({"c": [v for fila in m[i:i + 8] for v in fila]})
        self._poner({"ack": "dump", "n": len(m)})
