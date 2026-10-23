"""Etapa 3 del esquema: validación de resultados y comparación entre fotos.

Valida formato de lote, plausibilidad de fecha y confianza, y consolida las
lecturas de las 5 caras en un único valor por campo.

Estrategias (config `estrategia_consolidacion`):
  - "combinar" (defecto): lote y fecha suelen estar en UNA sola cara de la caja,
    así que se combinan los campos de todas las caras. Si dos caras válidas
    discrepan en un campo y ninguna gana por mayoría estricta -> revisión manual.
  - "mayoria": exige que más de `umbral_mayoria` de las caras coincidan (es la
    regla del contrato actual, UMBRAL_VOTACION_MAYORIA). Con lote/fecha en una
    sola cara casi nunca se cumple; se deja para comparar.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .config import Config
from .tipos import LecturaCara


def lote_valido(lote: str | None, cfg: Config) -> bool:
    return bool(lote) and cfg.patron_lote.match(lote) is not None


def fecha_plausible(f: date | None, hoy: date, cfg: Config) -> bool:
    if f is None:
        return False
    return hoy.year - cfg.anios_atras_plausible <= f.year <= hoy.year + cfg.anios_adelante_plausible


def _elegir(candidatos: list[tuple[object, float]], total_caras: int, cfg: Config):
    """candidatos: [(valor, confianza)]. Devuelve (valor, confianza, motivo_error|None)."""
    if not candidatos:
        return None, 0.0, "ausente"

    grupos: dict[object, list[float]] = defaultdict(list)
    for valor, conf in candidatos:
        grupos[valor].append(conf)

    # ordenar por apoyo (nº de caras) y luego por confianza media
    ordenados = sorted(grupos.items(), key=lambda kv: (len(kv[1]), sum(kv[1]) / len(kv[1])), reverse=True)
    valor, confs = ordenados[0]

    if cfg.estrategia == "mayoria":
        if len(confs) / total_caras <= cfg.umbral_mayoria:
            return None, 0.0, "sin_mayoria"
    elif len(ordenados) > 1:
        segundo = ordenados[1][1]
        if len(confs) <= len(segundo):  # empate o sin ganador estricto
            return None, 0.0, "conflicto_entre_fotos"

    conf = sum(confs) / len(confs)
    if conf < cfg.confianza_minima:
        return None, conf, "confianza_baja"
    return valor, conf, None


def consolidar(lecturas: list[LecturaCara], hoy: date, cfg: Config) -> dict:
    """Devuelve {'nombre','gramaje','lote','vencimiento','confianza','errores':[...]}.

    `errores` lista los motivos por campo obligatorio fallido (vacía = todo bien).
    """
    total = len(lecturas)
    cand_lote = [
        (l.lote, l.confianza_lote) for l in lecturas if lote_valido(l.lote, cfg)
    ]
    cand_venc = [
        (l.vencimiento, l.confianza_vencimiento)
        for l in lecturas if fecha_plausible(l.vencimiento, hoy, cfg)
    ]
    cand_nombre = [(l.nombre, 1.0) for l in lecturas if l.nombre]

    lote, c_lote, e_lote = _elegir(cand_lote, total, cfg)
    venc, c_venc, e_venc = _elegir(cand_venc, total, cfg)
    nombre, _, e_nombre = _elegir(cand_nombre, total, cfg)
    gramaje = next((l.gramaje for l in lecturas if l.gramaje), None)

    errores = []
    for campo, err in (("lote", e_lote), ("vencimiento", e_venc), ("nombre", e_nombre)):
        if campo in cfg.campos_obligatorios and err:
            errores.append(f"{campo}:{err}")

    aceptadas = [c for campo, c in (("lote", c_lote), ("vencimiento", c_venc))
                 if campo in cfg.campos_obligatorios]
    return {
        "nombre": nombre,
        "gramaje": gramaje,
        "lote": lote,
        "vencimiento": venc,
        "confianza": min(aceptadas) if aceptadas else 0.0,
        "errores": errores,
    }
