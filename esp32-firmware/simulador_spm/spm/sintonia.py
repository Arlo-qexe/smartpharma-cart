"""Sintonía por simulación del lazo de posición del firmware (firmware/spm_esp32s3) con el modelo identificado de un motor (spm/identificacion.py).

Replica, línea por línea, lo que hace el firmware cada milisegundo: lectura del encoder (cuantizada y con el filtro de velocidad), referencia en modo «streaming»
(consignas cada `periodo_stream`, rampa lineal entre ellas, con el retraso de un periodo), PID de posición con avance o cascada P→PI, compensación de fricción (`pwm_min`),
banda de parada, anti-windup, cuantización del PWM a 10 bits. La planta es la identificada: τ·dω/dt + ω = K·(u − u0·signo(u)), con K y u0 distintos por sentido.

Las ganancias salen en las mismas unidades que `{"cfg":...}` del firmware (grados, ciclo de trabajo 0..1), así que se pueden mandar tal cual.
Es una simulación del motor LIBRE: con el mecanismo acoplado cambian la carga y la fricción (hay que repetir la identificación acoplada).
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

import numpy as np

PWM_MAX = 1023
VEL_ALPHA = 0.15
DT = 0.001


@dataclass
class Planta:
    K_pos: float = 270.0        # °/s por unidad de ciclo de trabajo
    K_neg: float = 270.0
    u0_pos: float = 0.04
    u0_neg: float = 0.03
    tau: float = 0.027
    ppr: float = 4532.0

    @classmethod
    def desde_modelo(cls, ruta, ppr: float):
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        return cls(abs(d["K_pos"]), abs(d["K_neg"]), d["u0_pos"], d["u0_neg"], (d["tau_pos"] + d["tau_neg"]) / 2, float(ppr))

    def variante(self, k=1.0, u0=1.0, tau=1.0):
        return replace(self, K_pos=self.K_pos * k, K_neg=self.K_neg * k, u0_pos=self.u0_pos * u0, u0_neg=self.u0_neg * u0, tau=self.tau * tau)

    @property
    def dpc(self) -> float:
        return 360.0 / self.ppr


@dataclass
class Ganancias:
    ctrl: str = "pid"                               # pid | casc
    kp: float = 0.02; ki: float = 0.006; kd: float = 0.001; kff: float = 0.0012
    kpp: float = 8.0; kpv: float = 0.002; kiv: float = 0.02; kffv: float = 0.0012
    pwm_min: float = 0.10; pwm_max: float = 0.60; band: float = 0.2
    vmax: float = 600.0

    def comandos(self, motor: int) -> list[dict]:
        """Las órdenes {"cfg":...} que dejan estas ganancias en el firmware (motor 1..3)."""
        nombres = ["kp", "ki", "kd", "kff"] if self.ctrl == "pid" else ["kpp", "kpv", "kiv", "kffv"]
        orden = [{"cfg": "ctrl", "motor": motor, "val": self.ctrl}]
        orden += [{"cfg": n, "motor": motor, "val": round(getattr(self, n), 6)} for n in nombres + ["pwm_min", "band"]]
        return orden


def _sgn(x: float) -> float:
    return -1.0 if x < 0 else 1.0


def simular(planta: Planta, g: Ganancias, t_ref, q_ref_deg, periodo_stream: float = 0.04, t_extra: float = 0.5) -> dict:
    """Sigue la trayectoria (t_ref [s], q_ref_deg [°]) como lo haría el firmware con consignas cada `periodo_stream`. Devuelve arrays a 1 kHz."""
    t_ref, q_ref = np.asarray(t_ref, float), np.asarray(q_ref_deg, float)
    n = int(round((t_ref[-1] + t_extra) / DT))
    dpc, tau = planta.dpc, planta.tau
    consignas = np.interp(np.arange(0, t_ref[-1] + periodo_stream, periodo_stream), t_ref, q_ref)          # lo que manda la interfaz
    theta = omega = 0.0
    pos, vel, cnt_prev = 0, 0.0, 0
    r = rv = target = 0.0                      # cuentas, cuentas/s
    seg_left, stream = 0.0, False
    integ = integ_v = 0.0
    u_duty = 0.0
    prox, idx = 0.0, 0
    th = np.empty(n); duty = np.empty(n); rr = np.empty(n)
    pmin, pmax, band = g.pwm_min, g.pwm_max, g.band
    es_pid = g.ctrl == "pid"
    for k in range(n):
        t = k * DT
        # --- encoder
        cnt = int(round(theta / dpc))
        d = cnt - cnt_prev
        cnt_prev = cnt
        pos += d
        vel += VEL_ALPHA * (d / DT - vel)
        # --- consigna nueva (streaming)
        if t >= prox - 1e-9 and idx < len(consignas):
            target = consignas[idx] / dpc
            if idx > 0:                                               # a partir de la 2.ª consigna: rampa lineal en el intervalo
                T = min(max(periodo_stream, 0.010), 0.25)
                rv = (target - r) / T
                seg_left, stream = T, True
            else:
                r = target
            idx += 1
            prox += periodo_stream
        if stream:
            r += rv * DT
            seg_left -= DT
            if seg_left <= 0:
                r, rv, stream = target, 0.0, False
        # --- control (controlStep)
        e = (r - pos) * dpc
        v = vel * dpc
        rvd = rv * dpc
        if es_pid:
            u_raw = g.kp * e + integ + g.kd * (rvd - v) + g.kff * rvd
            us = max(-pmax, min(pmax, u_raw))
            wind = us != u_raw and _sgn(e) == _sgn(u_raw)
            if not wind and abs(e) < 10.0:
                integ = max(-pmax, min(pmax, integ + g.ki * e * DT))
        else:
            vcmd = g.kpp * e + rvd
            ev = vcmd - v
            u_raw = g.kpv * ev + integ_v + g.kffv * vcmd
            us = max(-pmax, min(pmax, u_raw))
            wind = us != u_raw and _sgn(ev) == _sgn(u_raw)
            if not wind:
                integ_v = max(-pmax, min(pmax, integ_v + g.kiv * ev * DT))
        u = max(-pmax, min(pmax, u_raw))
        if band > 0 and abs(e) < band and abs(rvd) < 1.0 and abs(v) < 5.0:
            u = 0.0
            integ *= 0.99
            integ_v *= 0.99
        elif abs(u) > 0.002 and pmin > 0:
            u = _sgn(u) * (pmin + (1.0 - pmin) * abs(u))
            u = max(-pmax, min(pmax, u))
        u_duty = round(u * PWM_MAX) / PWM_MAX                            # el PWM es de 10 bits
        # --- planta (ZOH 1 ms)
        if u_duty > 0:
            ue = u_duty - planta.u0_pos if u_duty > planta.u0_pos else 0.0
            K = planta.K_pos
        elif u_duty < 0:
            ue = u_duty + planta.u0_neg if u_duty < -planta.u0_neg else 0.0
            K = planta.K_neg
        else:
            ue, K = 0.0, planta.K_pos
        omega += DT * (K * ue - omega) / tau
        theta += omega * DT
        th[k], duty[k], rr[k] = theta, u_duty, r * dpc
    t = np.arange(n) * DT
    ref_retrasada = np.interp(t - periodo_stream, t_ref, q_ref)           # el firmware reproduce la trayectoria con un periodo de retraso
    return {"t": t, "theta": th, "duty": duty, "r": rr, "ref": ref_retrasada, "ref_plan": np.interp(t, t_ref, q_ref), "dpc": dpc}


def metricas(res: dict, t_ref, q_ref_deg, vel_quieto: float = 1.0, min_quieto: float = 1.0) -> dict:
    """Errores de seguimiento (contra la trayectoria retrasada un periodo) y comportamiento en cada pausa (¿se queda quieto o vibra?).
    Cada pausa se mide en su segunda mitad (ya asentada)."""
    t, th, ref, duty = res["t"], res["theta"], res["ref"], res["duty"]
    err = th - ref
    quieto = np.abs(np.gradient(res["ref_plan"], t)) < vel_quieto
    pausas, i = [], 0
    while i < len(quieto):
        if quieto[i]:
            j = i
            while j < len(quieto) and quieto[j]:
                j += 1
            if (j - i) * DT >= min_quieto:
                pausas.append((i + (j - i) // 2, j))
            i = j
        else:
            i += 1
    mov = ~quieto
    out = {"error_max_mov_deg": float(np.abs(err[mov]).max()) if mov.any() else 0.0,
           "error_rms_deg": float(np.sqrt(np.mean(err ** 2))),
           "error_max_deg": float(np.abs(err).max()),
           "inestable": bool(np.abs(err).max() > 30.0 or not np.isfinite(err).all())}
    err_p = [float(np.abs(err[a:b]).max()) for a, b in pausas]
    vib = [float(th[a:b].max() - th[a:b].min()) for a, b in pausas]
    out["pausa_error_max_deg"] = max(err_p) if err_p else 0.0
    out["pausa_vibracion_pp_deg"] = max(vib) if vib else 0.0
    out["pausa_duty_medio"] = float(np.mean([np.mean(np.abs(duty[a:b])) for a, b in pausas])) if pausas else 0.0
    out["pausa_cambios_signo_por_s"] = float(np.mean([np.sum(np.diff(np.sign(duty[a:b])) != 0) / ((b - a) * DT) for a, b in pausas])) if pausas else 0.0
    out["duty_saturado_pct"] = float(100 * np.mean(np.abs(duty) >= 0.98 * max(1e-9, np.abs(duty).max())))
    return out


def costo(m: dict) -> float:
    if m["inestable"]:
        return 1e6
    return (m["error_max_mov_deg"] + 2.0 * m["error_rms_deg"] + 30.0 * m["pausa_error_max_deg"] + 10.0 * m["pausa_vibracion_pp_deg"]
            + 0.5 * m["pausa_cambios_signo_por_s"] + 20.0 * m["pausa_duty_medio"])


def evaluar(planta: Planta, g: Ganancias, t_ref, q_ref, robustez=((1.25, 1.0, 1.0), (0.75, 1.0, 1.0)), periodo_stream: float = 0.04) -> tuple[float, dict]:
    """Costo = nominal + 0.5 · cada variante de la planta (K ±25 %: p. ej. pasar de 9.6 V a 12 V, o una fuente que cae)."""
    m0 = metricas(simular(planta, g, t_ref, q_ref, periodo_stream), t_ref, q_ref)
    c = costo(m0)
    for k, u0, tau in robustez:
        c += 0.5 * costo(metricas(simular(planta.variante(k, u0, tau), g, t_ref, q_ref, periodo_stream), t_ref, q_ref))
    return c, m0


def _rango(ctrl: str, pmin: float):
    if ctrl == "pid":
        return {"kp": (0.01, 0.30), "ki": (0.0, 2.0), "kd": (0.0, 0.004)}
    return {"kpp": (2.0, 40.0), "kpv": (0.0005, 0.02), "kiv": (0.0, 0.5)}


def buscar(planta: Planta, t_ref, q_ref, ctrl: str = "pid", pwm_max: float = 0.7, iteraciones: int = 60, semilla: int = 1, log=None) -> tuple[Ganancias, dict, float]:
    """Búsqueda aleatoria + refinamiento local de las ganancias (kff/kffv y pwm_min salen de la identificación; band se prueba entre unos pocos valores)."""
    rng = random.Random(semilla)
    K = (planta.K_pos + planta.K_neg) / 2
    pwm_min = round(max(0.02, min(planta.u0_pos, planta.u0_neg) + 0.005), 3)
    kff = 1.0 / (K * (1.0 - pwm_min))
    base = Ganancias(ctrl=ctrl, kff=kff, kffv=kff, pwm_min=pwm_min, pwm_max=pwm_max, band=0.2, kd=0.0)
    if ctrl == "pid":
        base.kp, base.ki = 15.0 / K, 0.3 * 15.0 / K
    else:
        base.kpp, base.kpv, base.kiv = 10.0, 5.0 / K, 0.1
    mejor, (cmejor, mmejor) = base, evaluar(planta, base, t_ref, q_ref)
    rangos = _rango(ctrl, pwm_min)
    for i in range(iteraciones):
        cand = replace(mejor)
        escala = 0.6 if i < iteraciones // 2 else 0.2
        for nombre, (lo, hi) in rangos.items():
            if rng.random() < 0.7:
                v = getattr(cand, nombre)
                v = v * math.exp(rng.gauss(0, escala)) if v > 0 else rng.uniform(lo, lo + 0.05 * (hi - lo))
                setattr(cand, nombre, min(hi, max(lo, v)))
        if rng.random() < 0.2:
            cand.band = rng.choice([0.0, 0.08, 0.16, 0.24, 0.4])
        c, m = evaluar(planta, cand, t_ref, q_ref)
        if c < cmejor:
            mejor, cmejor, mmejor = cand, c, m
            if log:
                log("  it %3d  costo %.2f  %s" % (i, c, {k: round(getattr(cand, k), 5) for k in rangos}))
    return mejor, mmejor, cmejor


def conservadora(g: Ganancias, kp_max: float = 0.10, kpp_max: float = 12.0) -> Ganancias:
    """Versión para la PRIMERA prueba real: la ganancia de posición limitada, porque la sintonía por simulación tiende a subirla hasta el borde (el simulador no
    tiene holguras, elasticidad ni ruido eléctrico). Se sube poco a poco con el motor real a la vista."""
    return replace(g, kp=min(g.kp, kp_max), kpp=min(g.kpp, kpp_max))


def a_json(g: Ganancias, planta: Planta, metr: dict, motor: int, ruta, optimizadas: Ganancias | None = None, metr_opt: dict | None = None):
    d = {"motor": motor, "ganancias": asdict(g), "comandos": g.comandos(motor), "planta": asdict(planta), "metricas_simuladas": metr}
    if optimizadas is not None:
        d.update(ganancias_optimizadas=asdict(optimizadas), comandos_optimizadas=optimizadas.comandos(motor), metricas_optimizadas=metr_opt)
    Path(ruta).write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
