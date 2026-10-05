"""
Contrato de entrada/salida con el módulo de reconocimiento (OCR + votación
FEFO), que desarrolla el equipo de IA (ver docs/arquitectura_comunicacion.md,
sección 7).

Este archivo es intencionalmente una CAJA NEGRA desde la capa de
comunicaciones: define únicamente la firma de la función y el formato de
entrada/salida esperado. No implementes aquí ningún modelo real — cuando el
equipo de IA entregue su código, se reemplaza el cuerpo de
`procesar_lote_ocr()` y nada más en el repo debería necesitar cambios.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import RESULTADO_ERROR_REVISION_MANUAL  # noqa: E402


def procesar_lote_ocr(imagenes: list) -> str:
    """
    CONTRATO (no modificar la firma sin actualizar shared/ y avisar al equipo de IA):

    Entrada:
        imagenes: list[bytes] — JPEGs de las 5 caras del objeto, en memoria.

    Salida:
        str — el tipo de producto identificado (p. ej. "TIPO_A"), o
        RESULTADO_ERROR_REVISION_MANUAL si no hay consenso suficiente entre
        las 5 lecturas o si `imagenes` está vacía.

    --- PLACEHOLDER ACTUAL (reemplazar cuando el equipo de IA entregue su módulo) ---
    """
    print(f"[ocr_interface] PLACEHOLDER: simulando reconocimiento sobre {len(imagenes)} imágenes")
    if not imagenes:
        return RESULTADO_ERROR_REVISION_MANUAL
    return "TIPO_A"
