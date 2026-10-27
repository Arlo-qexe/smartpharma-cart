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


RESOLUCIONES_PRUEBA = ((1920, 1080), (2592, 1944), (3264, 2448), (3840, 2160), (4056, 3040))


def probar_resoluciones(indice: int = 0, carpeta: Path | None = None, config: dict | None = None,
                        resoluciones=RESOLUCIONES_PRUEBA) -> None:
    """Para cada resolución pedida, imprime lo que la cámara REALMENTE entrega, los fps medidos con
    lectura continua y la nitidez, y guarda una foto de muestra (carpeta) para comparar a ojo.
    Pon una caja con lote bajo la cámara, en su posición real, antes de correrlo.
    Los drivers UVC a veces aceptan el valor pedido y entregan otro, o interpolan: compara la
    foto de muestra (el texto del lote debe verse más fino, no solo más grande)."""
    import time

    if carpeta:
        carpeta.mkdir(parents=True, exist_ok=True)
    print(f"{'pedida':>11} {'entrega':>11} {'fps':>5} {'nitidez':>8}  muestra")
    for ancho, alto in resoluciones:
        cam = abrir(indice, ancho, alto, config)
        if cam is None:
            print(f"{ancho}x{alto:<5} no abre la cámara")
            continue
        try:
            for _ in range(15):  # deja converger autoenfoque/exposición
                cam.read()
            t0, n, frame = time.perf_counter(), 0, None
            while n < 10:
                ok, f = cam.read()
                if ok:
                    frame, n = f, n + 1
                elif time.perf_counter() - t0 > 15:
                    break
            fps = n / (time.perf_counter() - t0) if n else 0.0
            if frame is None:
                print(f"{ancho}x{alto:<5} sin imagen")
                continue
            h, w = frame.shape[:2]
            muestra = ""
            if carpeta:
                ruta = carpeta / f"prueba_{w}x{h}.jpg"
                cv2.imwrite(str(ruta), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                muestra = ruta.name
            nota = "" if (w, h) == (ancho, alto) else "  <- el driver cambió la resolución pedida"
            print(f"{ancho:>5}x{alto:<5} {w:>5}x{h:<5} {fps:5.1f} {nitidez(frame):8.0f}  {muestra}{nota}")
        finally:
            cam.release()
