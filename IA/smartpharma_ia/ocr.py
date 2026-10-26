"""Etapa 2 del esquema: OCR sobre cada región.

Interfaz `Ocr.leer(imagen, exhaustivo=True) -> (texto, confianza)`. `exhaustivo=False` pide UNA
sola pasada (para fotos completas, donde probar giros y márgenes cuesta segundos y no aporta).
Motores:
  - OcrRapid: RapidOCR (ONNX, corre en CPU, opcional: requirements-ocr.txt).
  - OcrGuionado: devuelve textos predefinidos; para pruebas sin motor real.
"""
from __future__ import annotations

from typing import Protocol

from PIL import Image


class OcrNoDisponible(RuntimeError):
    """El motor de OCR no está instalado o no pudo iniciar."""


class Ocr(Protocol):
    def leer(self, imagen: Image.Image, exhaustivo: bool = True) -> tuple[str, float]: ...


# RapidOCR AGRANDA toda imagen cuyo lado corto sea menor de `limit_side_len` (736 por defecto) hasta ese
# tamaño: un recorte del detector de 86 px se leía a ~8x su tamaño y cada llamada tardaba ~1,3 s en CPU.
# A 192 px tarda ~0,11 s y lee igual (medido sobre 20 recortes reales del detector: 18 de 20 con texto,
# frente a 19 de 20).
LADO_DETECCION_DEFECTO = 192

# Una lectura se da por buena (y se dejan de probar orientaciones) con esta confianza y al menos este
# número de caracteres alfanuméricos. Medido sobre 52 recortes de lote y fecha: con 0,95 se hacen ~3,9
# llamadas por recorte en vez de 8 y el texto coincide con el de probar las 8 en 49 de 52 casos.
# Los recortes GRANDES también salen caros: un nombre de 997x539 px tardó 4,4 s (8 orientaciones sobre una
# imagen de ~1000 px). Por encima de este lado se reducen antes de leer; el texto de un recorte así es
# grande y se sigue leyendo bien.
MAX_LADO_RECORTE = 640

CONFIANZA_SUFICIENTE = 0.95
CARACTERES_SUFICIENTES = 4


def _intentos(ancho: int, alto: int) -> list:
    """(giro, margen) en el orden en que conviene probarlos. Con borde (40 px) lee mejor casi siempre;
    sin borde queda como último recurso. Las CUATRO orientaciones se prueban en cualquier recorte: la
    ganadora no es predecible (medido: 90°, 270°, 180° y 0° ganan según la caja), aunque se empieza por
    la más probable según la forma: texto vertical -> 90°/270°; texto horizontal -> 0°/180°."""
    giros = (90, 270, 180, 0) if alto > 1.5 * ancho else (0, 180, 90, 270)
    return [(g, 40) for g in giros] + [(g, 0) for g in giros]


class OcrRapid:
    """Envuelve rapidocr-onnxruntime. Import perezoso: el paquete es opcional."""

    def __init__(self, lado_deteccion: int | None = LADO_DETECCION_DEFECTO) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError as e:
            raise OcrNoDisponible(
                "rapidocr-onnxruntime no está instalado. "
                "Instálalo con: pip install -r requirements-ocr.txt"
            ) from e
        # lado_deteccion=None deja el comportamiento de fábrica de RapidOCR (más lento con recortes pequeños).
        self._motor = RapidOCR(det_limit_side_len=lado_deteccion, det_limit_type="min") if lado_deteccion else RapidOCR()

    def leer(self, imagen: Image.Image, exhaustivo: bool = True) -> tuple[str, float]:
        """Recortes ajustados al texto: RapidOCR no lee si no hay margen (se añade borde blanco)
        ni texto girado (se prueban orientaciones según la forma del recorte; se elige la de mayor
        confianza x texto leído y se PARA en cuanto una lectura es suficientemente buena).

        exhaustivo=False (foto completa, sin detector): UNA sola pasada a 0° y sin margen. Los
        giros y márgenes multiplicaban por 4 las llamadas (≈1 s c/u en CPU, 20 por caja) y no
        aportan en una foto entera: el borde no hace falta y RapidOCR ya endereza por sí mismo
        las líneas puestas al revés (clasificador de ángulo)."""
        import numpy as np

        rgb = imagen.convert("RGB")
        ancho, alto = rgb.size
        if exhaustivo and max(ancho, alto) > MAX_LADO_RECORTE:
            k = MAX_LADO_RECORTE / max(ancho, alto)
            rgb = rgb.resize((max(1, round(ancho * k)), max(1, round(alto * k))), Image.LANCZOS)
            ancho, alto = rgb.size
        intentos = _intentos(ancho, alto) if exhaustivo else [(0, 0)]
        mejor: tuple[str, float] = ("", 0.0)
        mejor_puntaje = 0.0
        for giro, margen in intentos:
            img = rgb.rotate(giro, expand=True) if giro else rgb
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
            caracteres = sum(c.isalnum() for c in texto)
            puntaje = confianza * caracteres  # confianza x texto leído
            if puntaje > mejor_puntaje:
                mejor, mejor_puntaje = (texto, confianza), puntaje
            if exhaustivo and confianza >= CONFIANZA_SUFICIENTE and caracteres >= CARACTERES_SUFICIENTES:
                break
        return mejor


class OcrGuionado:
    """Devuelve, en orden, los (texto, confianza) indicados. Solo para pruebas."""

    def __init__(self, respuestas: list[tuple[str, float]]):
        self._respuestas = list(respuestas)
        self._i = 0

    def leer(self, imagen: Image.Image, exhaustivo: bool = True) -> tuple[str, float]:
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
