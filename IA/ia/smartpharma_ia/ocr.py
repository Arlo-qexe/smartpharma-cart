"""Etapa 2 del esquema: OCR sobre cada región.

Interfaz `Ocr.leer(imagen) -> (texto, confianza)`. Motores:
  - OcrRapid: RapidOCR (ONNX, corre en CPU, opcional: requirements-ocr.txt).
  - OcrGuionado: devuelve textos predefinidos; para pruebas sin motor real.
"""
from __future__ import annotations

from typing import Protocol

from PIL import Image


class OcrNoDisponible(RuntimeError):
    """El motor de OCR no está instalado o no pudo iniciar."""


class Ocr(Protocol):
    def leer(self, imagen: Image.Image) -> tuple[str, float]: ...


class OcrRapid:
    """Envuelve rapidocr-onnxruntime. Import perezoso: el paquete es opcional."""

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError as e:
            raise OcrNoDisponible(
                "rapidocr-onnxruntime no está instalado. "
                "Instálalo con: pip install -r requirements-ocr.txt"
            ) from e
        self._motor = RapidOCR()

    def leer(self, imagen: Image.Image) -> tuple[str, float]:
        """Recortes ajustados al texto: RapidOCR no lee si no hay margen (se añade borde blanco)
        ni texto girado 90° (se prueban orientaciones según la forma del recorte y se
        elige la de mayor confianza x texto leído). Medido sobre recortes reales del detector."""
        import numpy as np

        rgb = imagen.convert("RGB")
        ancho, alto = rgb.size
        giros = (90, 270, 0) if alto > 1.5 * ancho else (0, 180) if ancho > 1.5 * alto else (0,)
        mejor: tuple[str, float] = ("", 0.0)
        mejor_puntaje = 0.0
        for giro in giros:
            img = rgb.rotate(giro, expand=True) if giro else rgb
            for margen in (0, 40):  # con y sin borde: según el recorte, uno u otro lee mejor
                lienzo = img
                if margen:
                    lienzo = Image.new("RGB", (img.width + 2 * margen, img.height + 2 * margen), (255, 255, 255))
                    lienzo.paste(img, (margen, margen))
                resultado, _ = self._motor(np.asarray(lienzo))
                if not resultado:
                    continue
                # cada elemento: [caja, texto, confianza]; se ordena de arriba a abajo
                filas = sorted(resultado, key=lambda r: min(p[1] for p in r[0]))
                texto = "\n".join(str(f[1]) for f in filas)
                confianza = sum(float(f[2]) for f in filas) / len(filas)
                puntaje = confianza * sum(c.isalnum() for c in texto)  # confianza x texto leído
                if puntaje > mejor_puntaje:
                    mejor, mejor_puntaje = (texto, confianza), puntaje
        return mejor


class OcrGuionado:
    """Devuelve, en orden, los (texto, confianza) indicados. Solo para pruebas."""

    def __init__(self, respuestas: list[tuple[str, float]]):
        self._respuestas = list(respuestas)
        self._i = 0

    def leer(self, imagen: Image.Image) -> tuple[str, float]:
        if self._i >= len(self._respuestas):
            return "", 0.0
        r = self._respuestas[self._i]
        self._i += 1
        return r


def crear_ocr(nombre: str) -> Ocr:
    nombre = nombre.lower()
    if nombre == "rapidocr":
        return OcrRapid()
    raise ValueError(f"motor OCR desconocido: {nombre!r} (disponible: 'rapidocr')")
