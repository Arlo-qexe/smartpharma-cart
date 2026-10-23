"""Etapa 1 del esquema: detección de regiones de interés (nombre, gramaje, lote, fecha).

Interfaz intercambiable. Hoy solo existe `DetectorImagenCompleta` (sin entrenar):
devuelve la imagen entera como una única región y deja que el OCR + el parseo
por palabras clave encuentren lote y vencimiento. Cuando exista un dataset
etiquetado, se entrena un detector CNN y se implementa `DetectorCNN` con esta
misma interfaz — nada más del pipeline cambia.
"""
from __future__ import annotations

from typing import Protocol

from PIL import Image

from .tipos import ETIQUETA_COMPLETA, ETIQUETAS_CAMPO, Region


class Detector(Protocol):
    def detectar(self, imagen: Image.Image) -> list[Region]: ...


class DetectorImagenCompleta:
    """Sin modelo: una sola región que cubre toda la foto."""

    def detectar(self, imagen: Image.Image) -> list[Region]:
        ancho, alto = imagen.size
        return [Region(ETIQUETA_COMPLETA, (0, 0, ancho, alto), 1.0)]


class DetectorCNN:
    """Detector YOLO (ultralytics) entrenado con entrenamiento/entrenar.py.

    - entrada: PIL.Image RGB de una cara;
    - salida: una Region por clase (la de mayor confianza) con etiqueta en ETIQUETAS_CAMPO;
    - `ruta_modelo`: best.pt o best.onnx. CPU por defecto; GPU con IA_DEVICE=0 (variable de entorno).
    """

    def __init__(self, ruta_modelo: str, conf_minima: float = 0.25):
        import os

        from ultralytics import YOLO  # import perezoso: solo hace falta con detector entrenado

        self._modelo = YOLO(ruta_modelo, task="detect")
        self._conf = conf_minima
        self._device = os.environ.get("IA_DEVICE", "cpu")

    def detectar(self, imagen: Image.Image) -> list[Region]:
        res = self._modelo.predict(imagen, conf=self._conf, device=self._device, verbose=False)[0]
        mejores: dict[str, Region] = {}
        for caja, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
            etiqueta = res.names[int(cls)]
            if etiqueta not in ETIQUETAS_CAMPO:
                continue
            if etiqueta not in mejores or conf > mejores[etiqueta].confianza:
                x0, y0, x1, y1 = (int(round(v)) for v in caja)
                mejores[etiqueta] = Region(etiqueta, (x0, y0, x1, y1), float(conf))
        return list(mejores.values())
