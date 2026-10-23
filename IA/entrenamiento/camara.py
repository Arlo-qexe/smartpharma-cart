"""Utilidades comunes de la Arducam (UVC): abrir, ajustar, leer un frame fresco y medir nitidez.

Las usan capturar.py (dataset) y en_vivo.py (prueba de punta a punta). Los valores de enfoque y
exposición dependen del driver de cada cámara: capturar.py --ajustar muestra lo que la cámara
REPORTA después de cada cambio, para ver si el driver realmente lo aplicó.
"""
import json
import sys
from pathlib import Path

import cv2

WIN = sys.platform == "win32"


def abrir(indice: int = 0, ancho: int = 1920, alto: int = 1080, config: dict | None = None):
    """Abre la cámara en MJPG (a 1080p el USB no da más con YUY2). None si no abre."""
    cam = cv2.VideoCapture(indice, cv2.CAP_DSHOW) if WIN else cv2.VideoCapture(indice)
    if not cam.isOpened():
        return None
    cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
    cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    aplicar(cam, config or {})
    return cam


def aplicar(cam, config: dict) -> None:
    """config: autofoco (bool, por defecto True), enfoque, exposicion (ausente = exposición automática)."""
    if config.get("autofoco", True):
        cam.set(cv2.CAP_PROP_AUTOFOCUS, 1)
    else:
        cam.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        if "enfoque" in config:
            cam.set(cv2.CAP_PROP_FOCUS, config["enfoque"])
    if "exposicion" in config:
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if WIN else 1)  # 0.25 = manual en DirectShow, 1 = manual en V4L2
        cam.set(cv2.CAP_PROP_EXPOSURE, config["exposicion"])
    else:
        cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if WIN else 3)  # automática


def estado(cam) -> dict:
    """Lo que la cámara reporta ahora (puede diferir de lo pedido si el driver lo ignora)."""
    return {
        "ancho": int(cam.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "alto": int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": round(cam.get(cv2.CAP_PROP_FPS), 1),
        "autofoco": bool(cam.get(cv2.CAP_PROP_AUTOFOCUS)),
        "enfoque": cam.get(cv2.CAP_PROP_FOCUS),
        "exposicion": cam.get(cv2.CAP_PROP_EXPOSURE),
    }


def leer(cam, descartar: int = 3):
    """Frame fresco: descarta los últimos que quedaron en el búfer para no guardar una imagen vieja."""
    for _ in range(descartar):
        cam.grab()
    ok, frame = cam.read()
    return frame if ok else None


def nitidez(frame) -> float:
    """Varianza del Laplaciano (más alta = más nítida). Compárala SOLO entre fotos de la misma escena y luz."""
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gris = cv2.resize(gris, (960, int(gris.shape[0] * 960 / gris.shape[1])))
    return float(cv2.Laplacian(gris, cv2.CV_64F).var())


def brillo(frame) -> float:
    return float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())


def cargar_config(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def guardar_config(ruta: Path, config: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(config, indent=2), encoding="utf-8")


def listar(maximo: int = 6) -> None:
    """Prueba los índices 0..maximo-1 e imprime cuáles abren y a qué resolución."""
    for i in range(maximo):
        cam = abrir(i)
        if cam is None:
            print(f"indice {i}: no abre")
            continue
        cam.read()
        e = estado(cam)
        print(f"indice {i}: abre, {e['ancho']}x{e['alto']}")
        cam.release()
