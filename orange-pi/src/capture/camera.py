"""
Control de la cámara USB (Arducam): autoenfoque y captura de la ráfaga de
5 fotos, comprimidas a JPEG en memoria (ver docs/arquitectura_comunicacion.md,
sección 6.1).

Requiere: pip install opencv-python
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import TIEMPO_CONVERGENCIA_AUTOENFOQUE_S  # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None  # permite importar este módulo sin OpenCV instalado (p. ej. para tests)


def abrir_camara(indice: int = 0):
    if cv2 is None:
        raise RuntimeError("opencv-python no está instalado (pip install opencv-python)")
    camara = cv2.VideoCapture(indice)
    camara.set(cv2.CAP_PROP_AUTOFOCUS, 1)
    return camara


def capturar_con_autoenfoque(camara, tiempo_espera_af: float = TIEMPO_CONVERGENCIA_AUTOENFOQUE_S):
    """Da tiempo al autoenfoque a converger (descartando frames) y devuelve
    el frame final. UVC genérico no siempre expone una señal de "enfoque
    listo", así que este patrón de espera + descarte es el más portable."""
    t0 = time.time()
    while time.time() - t0 < tiempo_espera_af:
        camara.read()
    ok, frame = camara.read()
    if not ok:
        raise RuntimeError("Fallo leyendo frame de la cámara")
    return frame


def comprimir_jpeg(frame, calidad: int = 90) -> bytes:
    """Comprime un frame a JPEG en memoria (nunca escribir a disco)."""
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    if not ok:
        raise RuntimeError("Fallo comprimiendo el frame a JPEG")
    return buffer.tobytes()


# TODO: implementar `capturar_ráfaga_completa(camara, enlace_uart)` en main.py,
# combinando esto con las órdenes `girar_posicion` / `en_posicion` del enlace
# UART y la pausa de estabilización (PAUSA_ESTABILIZACION_MECANICA_S).
