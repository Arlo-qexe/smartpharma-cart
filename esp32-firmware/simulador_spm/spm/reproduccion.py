"""Reproduce desde el PC, por la línea serie, el programa «4 caras + volcado» con los tres motores a la vez (modo streaming del firmware).

El planificador (spm/trayectoria.py) da los ángulos de MOTOR de cada instante; aquí se remuestrean a `dt` (40 ms), se mandan como {"m1":a,"m2":b,"m3":c}
en tiempo real y se registra la telemetría. El firmware interpola entre consignas y cierra el lazo de cada motor (ganancias de identificacion/ganancias_motorN.json).
Vigilancia: si la placa pasa a `fault`/`idle`, o un error de seguimiento supera `e_max` más de `t_e_max` s, se manda {"cmd":"stop"} y se aborta.
`puerto` es cualquier objeto con la interfaz de pyserial (write/readline); el reloj y el sueño se inyectan para poder probarlo sin placa.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NM = 3


def trayectoria_programa(tope_vel_degs: float = 130.0, espera: float = 5.0, dt: float = 0.04):
    """(t, q) con q en grados de motor, relativos a la pose inicial, remuestreados a `dt`. El tope de velocidad por motor alarga los tiempos si hace falta."""
    from . import SPM, cargar_config, desde_inclinacion, planificar_con_paradas
    from . import trayectoria as tj
    spm = SPM()
    trans, lim = cargar_config()
    lim.vel_motor_max = math.radians(tope_vel_degs)
    filas, _ = tj.programa_caja(spm, lim, 0.0, espera)
    Rs = [desde_inclinacion(*np.radians(w[:3])) for w in filas]
    Rx, Tx = tj.expandir_esperas(Rs, [w[4] for w in filas], [w[3] for w in filas[1:]])
    tr = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.02)
    q = np.degrees(tr.motor)
    q = q - q[0]
    t = np.arange(0.0, tr.t[-1] + 1e-9, dt)
    return t, np.column_stack([np.interp(t, tr.t, q[:, j]) for j in range(NM)])


class Abortado(Exception):
    pass


@dataclass
class Resultado:
    ok: bool = False
    motivo: str = ""
    filas: list = field(default_factory=list)       # (t_pc, ref1..3 [°], pos1..3 [°], u1..3 [‰], e1..3 [°])
    ppr: list = field(default_factory=lambda: [0.0] * NM)

    def metricas(self) -> dict:
        if not self.filas:
            return {}
        a = np.array(self.filas)
        e = a[:, 10:13]
        return {"muestras": len(a), "e_max_deg": np.abs(e).max(axis=0).tolist(), "e_rms_deg": np.sqrt((e ** 2).mean(axis=0)).tolist(),
                "u_max_permil": np.abs(a[:, 7:10]).max(axis=0).tolist()}


class Reproductor:
    def __init__(self, puerto, log=print, reloj=time.monotonic, dormir=time.sleep):
        self.p, self.log, self.reloj, self.dormir = puerto, log, reloj, dormir
        self.ult = None                    # última telemetría
        self.status = None

    # ---------------------------------------------------------------- bajo nivel
    def enviar(self, obj: dict) -> None:
        self.p.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def leer(self) -> None:
        """Lee y procesa las líneas disponibles (sin bloquear más que el timeout del puerto)."""
        while True:
            raw = self.p.readline()
            if not raw:
                return
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if "m1" in d and "st" in d:
                self.ult = d
            elif d.get("info") == "status":
                self.status = d
            elif "err" in d:
                self.log("placa: %s" % d)
            if self.p_waiting() == 0:
                return

    def p_waiting(self) -> int:
        return getattr(self.p, "in_waiting", 0)

    def esperar(self, cond, timeout: float, que: str):
        lim = self.reloj() + timeout
        while self.reloj() < lim:
            self.leer()
            if cond():
                return
            self.dormir(0.01)
        raise Abortado("sin respuesta de la placa: " + que)

    # ---------------------------------------------------------------- preparación
    def preparar(self, ganancias: list[Path] | None, ppr_esperado: float | None = 4532.0) -> None:
        """reset → ganancias de cada motor → comprobar ppr/estado → zero all (la pose en que están los motores pasa a ser el origen del programa)."""
        self.enviar({"cmd": "reset"})
        self.dormir(0.2)
        for ruta in ganancias or []:
            d = json.loads(Path(ruta).read_text(encoding="utf-8"))
            self.log("Ganancias: %s (%d órdenes)" % (Path(ruta).name, len(d["comandos"])))
            for c in d["comandos"]:
                self.enviar(c)
                self.dormir(0.03)
        self.status = None
        self.enviar({"cmd": "status"})
        self.esperar(lambda: self.status is not None, 3.0, "status")
        s = self.status
        if s.get("st") != "run" or s.get("fault"):
            raise Abortado("la placa no está en «run» (st=%s, fault=%s): pulsa Reset y revisa polaridad/cableado" % (s.get("st"), s.get("fault")))
        if ppr_esperado:
            malos = [i + 1 for i, p in enumerate(s.get("ppr", [])) if abs(p - ppr_esperado) > 0.5]
            if malos:
                raise Abortado("ppr distinto de %g en los motores %s (placa: %s): corrígelo y guarda los ajustes" % (ppr_esperado, malos, s.get("ppr")))
        self.enviar({"zero": "all"})
        self.dormir(0.3)
        self.enviar({"m1": 0.0, "m2": 0.0, "m3": 0.0})
        self.dormir(0.3)
        self.ult = None

    # ---------------------------------------------------------------- reproducción
    def reproducir(self, t: np.ndarray, q: np.ndarray, e_max: float = 15.0, t_e_max: float = 0.5, rango_max: float = 900.0,
                   progreso=None) -> Resultado:
        res = Resultado()
        res.ppr = list((self.status or {}).get("ppr", [0.0] * NM))
        if np.abs(q).max() > rango_max:
            raise Abortado("la trayectoria pide %.0f° en un motor (límite de seguridad %.0f°)" % (np.abs(q).max(), rango_max))
        dt = float(t[1] - t[0])
        t0 = self.reloj()
        t_exceso = 0.0
        ult_aviso = -1.0
        try:
            for k in range(len(t)):
                objetivo = t0 + t[k]
                self.enviar({"m1": round(float(q[k, 0]), 3), "m2": round(float(q[k, 1]), 3), "m3": round(float(q[k, 2]), 3)})
                while True:
                    self.leer()
                    ahora = self.reloj()
                    if ahora >= objetivo + dt:
                        break
                    self.dormir(min(0.005, max(0.0, objetivo + dt - ahora)))
                s = self.ult
                if s is None:
                    continue
                if s["st"] != "run":
                    raise Abortado("la placa pasó a «%s» en t=%.1f s" % (s["st"], t[k]))
                m = [s["m%d" % (j + 1)] for j in range(NM)]
                pos = [m[j]["pos"] * 360.0 / res.ppr[j] if res.ppr[j] else 0.0 for j in range(NM)]
                res.filas.append((t[k], *q[k], *pos, *[x["u"] for x in m], *[x["e"] for x in m]))
                e = max(abs(x["e"]) for x in m)
                t_exceso = t_exceso + dt if e > e_max else 0.0
                if t_exceso > t_e_max:
                    raise Abortado("error de seguimiento %.1f° durante más de %.1f s (t=%.1f s)" % (e, t_e_max, t[k]))
                if progreso and t[k] - ult_aviso >= 2.0:
                    ult_aviso = t[k]
                    progreso(t[k], t[-1], e)
            res.ok, res.motivo = True, "completado"
        except (Abortado, KeyboardInterrupt) as ex:
            self.enviar({"cmd": "stop"})
            res.motivo = str(ex) or "interrumpido por el usuario"
            self.log("PARADA: " + res.motivo)
        return res


def guardar_csv(res: Resultado, ruta: Path) -> None:
    cab = ["t_s"] + ["ref%d_deg" % i for i in (1, 2, 3)] + ["pos%d_deg" % i for i in (1, 2, 3)] + ["u%d_permil" % i for i in (1, 2, 3)] + ["e%d_deg" % i for i in (1, 2, 3)]
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("# ppr=%s resultado=%s\n" % (",".join("%g" % p for p in res.ppr), res.motivo))
        f.write(",".join(cab) + "\n")
        for fila in res.filas:
            f.write(",".join("%.3f" % x for x in fila) + "\n")
