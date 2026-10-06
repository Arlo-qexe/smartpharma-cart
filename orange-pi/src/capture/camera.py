"""
Control de la cámara USB (UVC): autoenfoque y captura de la ráfaga de
5 fotos a 1080p, comprimidas a JPEG en memoria (ver
docs/arquitectura_comunicacion.md, sección 6.1).

Cámara de desarrollo: HP 430/435 FHD Webcam. Cámara final prevista: Arducam
USB (comportamiento UVC equivalente). La cámara se busca por NOMBRE en
/dev/v4l/by-id, nunca por índice: el índice /dev/videoN cambia entre
arranques y /dev/video0 en esta placa es el decodificador `cedrus`, no una
cámara.

Requiere: pip install opencv-python-headless
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import TIEMPO_CONVERGENCIA_AUTOENFOQUE_S  # noqa: E402

try:
    import cv2
except ImportError:
    cv2 = None  # permite importar este módulo sin OpenCV instalado (p. ej. para tests)

V4L_BY_ID = Path("/dev/v4l/by-id")
# Fragmentos (sin distinguir mayúsculas) del nombre by-id, por orden de preferencia.
# Se puede forzar otro con la variable de entorno SMARTPHARMA_CAMARA.
NOMBRES_CAMARA = ("arducam", "hp_430_435_fhd_webcam")
ANCHO_CAPTURA = 1920
ALTO_CAPTURA = 1080


def buscar_camara_por_nombre(nombres=None) -> str:
    """Devuelve la ruta /dev/videoN del nodo de captura (`-video-index0`) de la
    primera cámara cuyo nombre by-id contenga alguno de `nombres`."""
    if nombres is None:
        forzado = os.environ.get("SMARTPHARMA_CAMARA")
        nombres = (forzado,) if forzado else NOMBRES_CAMARA
    enlaces = sorted(V4L_BY_ID.glob("*-video-index0")) if V4L_BY_ID.is_dir() else []
    for nombre in nombres:
        for enlace in enlaces:
            if nombre.lower() in enlace.name.lower():
                return os.path.realpath(enlace)
    disponibles = [e.name for e in enlaces] or "ninguna"
    raise RuntimeError(f"No se encontró cámara {nombres}; disponibles en {V4L_BY_ID}: {disponibles}")


def abrir_camara(nombres=None, ancho: int = ANCHO_CAPTURA, alto: int = ALTO_CAPTURA):
    """Abre la cámara USB por nombre, en MJPG a `ancho`x`alto` (1080p) y con
    autoenfoque activado."""
    if cv2 is None:
        raise RuntimeError("opencv no está instalado (pip install opencv-python-headless)")
    ruta = buscar_camara_por_nombre(nombres)
    camara = cv2.VideoCapture(ruta, cv2.CAP_V4L2)
    if not camara.isOpened():
        raise RuntimeError(f"No se pudo abrir la cámara en {ruta}")
    # MJPG primero: a 1080p, YUYV por USB 2.0 da muy pocos fps.
    camara.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camara.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
    camara.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
    camara.set(cv2.CAP_PROP_AUTOFOCUS, 1)
    real = (int(camara.get(cv2.CAP_PROP_FRAME_WIDTH)), int(camara.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    if real != (ancho, alto):
        print(f"[camera] Aviso: se pidió {ancho}x{alto} y la cámara entrega {real[0]}x{real[1]}")
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
