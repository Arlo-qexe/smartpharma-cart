"""Tipos de datos del módulo de reconocimiento. Sin dependencias externas."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# El único valor que se toma del contrato compartido. Ver ia/CLAUDE.md:
# este paquete NUNCA redefine constantes del protocolo.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import RESULTADO_ERROR_REVISION_MANUAL  # noqa: E402

ETIQUETA_COMPLETA = "completa"  # región = imagen completa (sin detector CNN)
ETIQUETAS_CAMPO = ("nombre", "gramaje", "lote", "fecha")


@dataclass(frozen=True)
class Region:
    """Región de interés detectada en una cara. caja = (x0, y0, x1, y1) en píxeles."""
    etiqueta: str
    caja: tuple[int, int, int, int]
    confianza: float = 1.0


@dataclass(frozen=True)
class TextoLeido:
    region: Region
    texto: str
    confianza: float


@dataclass
class LecturaCara:
    """Lo que se pudo extraer de UNA foto (una cara de la caja)."""
    indice: int
    nombre: str | None = None
    gramaje: str | None = None
    lote: str | None = None
    vencimiento: date | None = None
    confianza_lote: float = 0.0
    confianza_vencimiento: float = 0.0
    observaciones: list[str] = field(default_factory=list)


@dataclass
class ResultadoCaja:
    """Resultado final por caja. `clasificacion` es lo único que hoy viaja a la Orange Pi."""
    clasificacion: str                      # destino (p. ej. "TIPO_A") o ERROR_REVISION_MANUAL
    motivo: str | None = None               # por qué se fue a revisión manual (None si todo bien)
    nombre: str | None = None
    gramaje: str | None = None
    lote: str | None = None
    vencimiento: date | None = None
    confianza: float = 0.0                  # mínima entre los campos obligatorios aceptados
    dias_para_vencer: int | None = None
    lecturas: list[LecturaCara] = field(default_factory=list)

    @property
    def requiere_revision(self) -> bool:
        return self.clasificacion == RESULTADO_ERROR_REVISION_MANUAL


def resultado_error(motivo: str, lecturas: list[LecturaCara] | None = None) -> ResultadoCaja:
    return ResultadoCaja(
        clasificacion=RESULTADO_ERROR_REVISION_MANUAL,
        motivo=motivo,
        lecturas=lecturas or [],
    )
