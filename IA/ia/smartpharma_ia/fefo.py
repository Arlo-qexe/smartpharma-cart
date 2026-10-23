"""Etapa final: de la fecha de vencimiento al destino (rango FEFO).

Regla: se toma el PRIMER rango (en orden ascendente) cuyo `hasta_dias` es mayor
o igual a los días que faltan para vencer. `dias = vencimiento - hoy`; un
producto que vence hoy tiene dias = 0 (aún vigente); dias < 0 = vencido.
"""
from __future__ import annotations

from datetime import date

from .config import Config, RangoFefo


def dias_para_vencer(vencimiento: date, hoy: date) -> int:
    return (vencimiento - hoy).days


def rango_para(dias: int, cfg: Config) -> RangoFefo:
    for r in cfg.rangos:
        if r.hasta_dias is None or dias <= r.hasta_dias:
            return r
    raise AssertionError("config validada: el último rango siempre cubre el resto")


def destino_para(vencimiento: date, hoy: date, cfg: Config) -> str:
    return rango_para(dias_para_vencer(vencimiento, hoy), cfg).destino
