"""Puente hacia el contrato actual de pc/src/ocr_interface.py.

NO está conectado todavía: `pc/` sigue usando su placeholder. Cuando el equipo
decida conectar este módulo, el cuerpo de `procesar_lote_ocr()` en
pc/src/ocr_interface.py se reduce a:

    from smartpharma_ia.adaptador_pc import procesar_lote_ocr as _ia
    def procesar_lote_ocr(imagenes): return _ia(imagenes)

(y `IA/` se añade al PYTHONPATH de la PC). Hasta entonces, pc/ no se toca.

Variables de entorno:
  IA_OCR      motor de OCR (defecto: "rapidocr")
  IA_CONFIG   ruta a un reconocimiento.json alternativo
  IA_MODELO   ruta al detector entrenado (best.onnx); sin definir = imagen completa
  IA_DEVICE   "cpu" (defecto) o "0" para GPU
"""
from __future__ import annotations

import logging
import os
import threading

from .config import cargar_config
from .detector import DetectorCNN
from .ocr import OcrNoDisponible, crear_ocr
from .pipeline import Reconocedor
from .tipos import RESULTADO_ERROR_REVISION_MANUAL, ResultadoCaja

log = logging.getLogger("smartpharma_ia")

_reconocedor: Reconocedor | None = None
_candado = threading.Lock()  # el servidor TCP atiende lotes en hilos


def _obtener() -> Reconocedor:
    global _reconocedor
    with _candado:
        if _reconocedor is None:
            cfg = cargar_config(os.environ.get("IA_CONFIG"))
            ruta_modelo = os.environ.get("IA_MODELO")  # best.onnx / best.pt; sin él, imagen completa
            detector = DetectorCNN(ruta_modelo) if ruta_modelo else None
            _reconocedor = Reconocedor(ocr=crear_ocr(os.environ.get("IA_OCR", "rapidocr")),
                                       detector=detector, config=cfg)
            if cfg.estado_rangos != "CONFIRMADO":
                log.warning("Rangos FEFO sin confirmar (%s): revisar IA/config/reconocimiento.json",
                            cfg.estado_rangos)
        return _reconocedor


def procesar_lote_detallado(imagenes: list[bytes]) -> ResultadoCaja:
    """Versión con todos los campos (lote, vencimiento, confianza...). Para S-05."""
    return _obtener().procesar(imagenes)


def procesar_lote_ocr(imagenes: list) -> str:
    """Misma firma y salida que el contrato vigente: destino o ERROR_REVISION_MANUAL."""
    try:
        resultado = procesar_lote_detallado(imagenes)
    except OcrNoDisponible:
        log.exception("OCR no disponible; se envía a revisión manual")
        return RESULTADO_ERROR_REVISION_MANUAL
    except Exception:  # noqa: BLE001
        log.exception("fallo inesperado en el reconocimiento")
        return RESULTADO_ERROR_REVISION_MANUAL
    if resultado.requiere_revision:
        log.info("revisión manual: %s", resultado.motivo)
    return resultado.clasificacion
