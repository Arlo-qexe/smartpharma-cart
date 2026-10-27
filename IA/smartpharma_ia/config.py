"""Carga y validación de la configuración (config/reconocimiento.json).

Todo lo que el equipo todavía no ha decidido (rangos FEFO, umbrales) vive en el
JSON, nunca en el código, para poder cambiarlo sin tocar módulos.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

RUTA_CONFIG_DEFECTO = Path(__file__).resolve().parents[1] / "config" / "reconocimiento.json"

ESTRATEGIAS = ("combinar", "mayoria")
CAMPOS_VALIDOS = ("nombre", "lote", "vencimiento")


@dataclass(frozen=True)
class RangoFefo:
    nombre: str
    hasta_dias: int | None     # None = sin tope (último rango)
    destino: str


@dataclass(frozen=True)
class Config:
    rangos: tuple[RangoFefo, ...]
    estrategia: str
    campos_obligatorios: tuple[str, ...]
    confianza_minima: float
    umbral_mayoria: float
    patron_lote: re.Pattern
    anios_atras_plausible: int
    anios_adelante_plausible: int
    estado_rangos: str         # "PLACEHOLDER_SIN_CONFIRMAR" hasta que el equipo los defina
    # Tope de tiempo por caja (s): pasado ese tiempo se dejan de leer caras y se decide con lo
    # leído (normalmente -> revisión manual con motivo `tiempo_agotado`). None = sin tope.
    tiempo_maximo_s: float | None = None
    # Reintento con prefiltro (smartpharma_ia/prefiltro.py) cuando un recorte de lote/fecha no da un valor válido.
    # Lista de variantes a probar, en orden; vacía = desactivado.
    prefiltro_variantes: tuple[str, ...] = ()
    # True: se deja de leer en cuanto las lecturas hechas ya dan lote y vencimiento válidos (más rápido,
    # pero sin confirmar con otras caras). False: se leen lote/fecha de todas las caras que los tengan.
    corte_temprano: bool = False


def cargar_config(ruta: str | Path | None = None) -> Config:
    ruta = Path(ruta) if ruta else RUTA_CONFIG_DEFECTO
    datos = json.loads(ruta.read_text(encoding="utf-8"))

    rangos = tuple(
        RangoFefo(r.get("nombre", ""), r["hasta_dias"], r["destino"])
        for r in datos["rangos_fefo"]
    )
    _validar_rangos(rangos)

    estrategia = datos.get("estrategia_consolidacion", "combinar")
    if estrategia not in ESTRATEGIAS:
        raise ValueError(f"estrategia_consolidacion inválida: {estrategia!r} (use {ESTRATEGIAS})")

    obligatorios = tuple(datos.get("campos_obligatorios", ["lote", "vencimiento"]))
    for c in obligatorios:
        if c not in CAMPOS_VALIDOS:
            raise ValueError(f"campo obligatorio desconocido: {c!r} (use {CAMPOS_VALIDOS})")

    tiempo_maximo_s = datos.get("tiempo_maximo_s", 7.0)
    if tiempo_maximo_s is not None and (isinstance(tiempo_maximo_s, bool)
                                        or not isinstance(tiempo_maximo_s, (int, float))
                                        or tiempo_maximo_s < 0):
        raise ValueError(f"tiempo_maximo_s inválido: {tiempo_maximo_s!r} (número >= 0, o null)")

    variantes = tuple(datos.get("prefiltro_variantes", ()))
    from .prefiltro import VARIANTES
    for v in variantes:
        if v not in VARIANTES:
            raise ValueError(f"prefiltro_variantes: {v!r} desconocida (use {tuple(VARIANTES)})")

    return Config(
        rangos=rangos,
        estrategia=estrategia,
        campos_obligatorios=obligatorios,
        confianza_minima=float(datos.get("confianza_minima", 0.6)),
        umbral_mayoria=float(datos.get("umbral_mayoria", 0.5)),
        patron_lote=re.compile(datos.get("patron_lote", r"^(?=.*\d)[A-Z0-9][A-Z0-9\-/]{2,14}$")),
        anios_atras_plausible=int(datos.get("anios_atras_plausible", 10)),
        anios_adelante_plausible=int(datos.get("anios_adelante_plausible", 15)),
        estado_rangos=datos.get("_estado_rangos", "DESCONOCIDO"),
        tiempo_maximo_s=float(tiempo_maximo_s) if tiempo_maximo_s else None,   # 0 / null = sin tope
        prefiltro_variantes=variantes,
        corte_temprano=bool(datos.get("corte_temprano", False)),
    )


def _validar_rangos(rangos: tuple[RangoFefo, ...]) -> None:
    if not rangos:
        raise ValueError("rangos_fefo no puede estar vacío")
    previo = None
    for i, r in enumerate(rangos):
        ultimo = i == len(rangos) - 1
        if r.hasta_dias is None and not ultimo:
            raise ValueError("solo el último rango FEFO puede tener hasta_dias = null")
        if ultimo and r.hasta_dias is not None:
            raise ValueError("el último rango FEFO debe tener hasta_dias = null (cubre el resto)")
        if r.hasta_dias is not None:
            if previo is not None and r.hasta_dias <= previo:
                raise ValueError("los rangos FEFO deben ir en orden ascendente de hasta_dias")
            previo = r.hasta_dias
        if not r.destino or not isinstance(r.destino, str):
            raise ValueError("cada rango FEFO necesita un destino (string)")
