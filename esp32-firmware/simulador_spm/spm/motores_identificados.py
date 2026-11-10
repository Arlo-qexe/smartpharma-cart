"""Motores del SPM con la dinámica IDENTIFICADA (identificacion/modelo_motorN.json) y el lazo de posición del firmware (ganancias_motorN.json).

Cada motor se simula con `sintonia.simular` (réplica del firmware: encoder cuantizado, consignas en «streaming», cascada P→PI, zona muerta, saturación, PWM de 10 bits).
Con eso se ve cómo seguiría el plato una trayectoria real (p. ej. el programa de la caja) con los motores reales y no con los ideales de `MotoresSimulados`.

LÍMITES: modelos del motor LIBRE (sin mecanismo, 9.6 V, decaimiento lento); no incluyen la carga del plato, la gravedad, el rozamiento de las uniones ni
el acoplamiento entre patas. Los tres lazos se simulan por separado. El error de seguimiento incluye el retraso de un periodo de consigna (el del firmware).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import sintonia as sn
from .control import Registro
from .rot import angulo_entre

PPR = 4532.0                                                 # cuentas por vuelta del eje de salida (medido)
RUTA_IDENT = Path(__file__).resolve().parent.parent / "identificacion"


def cargar_planta(n: int, carpeta=RUTA_IDENT, ppr: float = PPR) -> sn.Planta:
    return sn.Planta.desde_modelo(Path(carpeta) / ("modelo_motor%d.json" % n), ppr)


def cargar_ganancias(n: int, carpeta=RUTA_IDENT) -> sn.Ganancias:
    d = json.loads((Path(carpeta) / ("ganancias_motor%d.json" % n)).read_text(encoding="utf-8"))["ganancias"]
    return sn.Ganancias(**{k: v for k, v in d.items() if k in sn.Ganancias.__dataclass_fields__})


def seguir_con_motores_identificados(spm, trans, lim, tray, plantas=None, ganancias=None, periodo_stream: float = 0.04, t_extra: float = 0.2):
    """Simula el seguimiento de `tray` con los 3 motores identificados. Devuelve (Registro, info).

    info: 'por_motor' (métricas de sintonia.metricas), 'error_motor_max_deg' (contra la consigna retrasada un periodo) y 'duty_max' por motor.
    """
    plantas = plantas or [cargar_planta(i) for i in (1, 2, 3)]
    ganancias = ganancias or [cargar_ganancias(i) for i in (1, 2, 3)]
    q0 = tray.motor[0]
    med = np.zeros_like(tray.motor)
    por_motor, err_mot, duty_max = [], [], []
    for j in range(3):
        q_ref = np.degrees(tray.motor[:, j] - q0[j])
        res = sn.simular(plantas[j], ganancias[j], tray.t, q_ref, periodo_stream, t_extra)
        med[:, j] = q0[j] + np.radians(np.interp(tray.t, res["t"], res["theta"]))
        por_motor.append(sn.metricas(res, tray.t, q_ref))
        err_mot.append(float(np.abs(np.interp(tray.t, res["t"], res["theta"] - res["ref"])).max()))
        duty_max.append(float(np.abs(res["duty"]).max()))
    Rest = np.zeros_like(tray.R); err = np.zeros(len(tray.t)); R_prev = tray.R[0]
    for k in range(len(tray.t)):
        Rest[k] = R_prev = spm.fk(trans.a_nnr(med[k]), R_prev)
        err[k] = math.degrees(angulo_entre(tray.R[k], Rest[k]))
    info = {"por_motor": por_motor, "error_motor_max_deg": err_mot, "duty_max": duty_max, "error_plato_max_deg": float(err.max()), "error_plato_rms_deg": float(np.sqrt(np.mean(err ** 2)))}
    return Registro(tray.t, tray.motor, med, tray.R, Rest, err), info
