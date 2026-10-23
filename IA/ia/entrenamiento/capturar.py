"""Captura del dataset con la Arducam (UVC, 1080p MJPG) montada en soporte fijo.

    python entrenamiento/capturar.py --listar                                   # qué índice es la Arducam
    python entrenamiento/capturar.py --ajustar --camara 1 --config ../dataset_ia/arducam/camara.json
    python entrenamiento/capturar.py --camara 1 --config ../dataset_ia/arducam/camara.json --salida ../dataset_ia/arducam/sesion01

--ajustar: vista en vivo para fijar enfoque y exposición (A autofoco, +/- enfoque, E/D exposición,
R exposición automática, S guarda camara.json, Q sale). Se hace UNA vez por montaje y luz.

Captura (cada caja = 5 caras, como CARAS_POR_OBJETO):
    ESPACIO guarda la cara actual   R guarda la misma cara con luz rasante (carpeta rasante/)
    U deshace la última foto        N salta a la caja siguiente        Q sale
Archivos: cajaNNN_caraK.jpg (numeración desde --primera-caja, 101 por defecto, para no chocar con
las fotos de celular 001-027). Cada foto queda registrada en captura_log.csv. Las fotos NO van al repo.
"""
import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camara  # noqa: E402

CARAS = 5
COLUMNAS_LOG = ["fecha_hora", "archivo", "tipo", "nitidez", "brillo", "ancho", "alto", "enfoque", "exposicion"]


def siguiente_caja(carpeta: Path, primera: int) -> int:
    nums = [int(m.group(1)) for p in carpeta.glob("caja*_cara*.jpg") if (m := re.match(r"caja(\d+)_", p.name))]
    return max(nums, default=primera - 1) + 1


def guardar(frame, ruta: Path) -> None:
    """imencode + write_bytes (cv2.imwrite falla en silencio con tildes en la ruta de Windows)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("No se pudo codificar la foto")
    ruta.write_bytes(buf.tobytes())


def registrar(log: Path, archivo: str, tipo: str, frame=None, est: dict | None = None) -> None:
    nuevo = not log.exists()
    with open(log, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(COLUMNAS_LOG)
        fila = [datetime.now().isoformat(timespec="seconds"), archivo, tipo]
        if frame is not None and est is not None:
            fila += [round(camara.nitidez(frame), 1), round(camara.brillo(frame), 1),
                     frame.shape[1], frame.shape[0], est["enfoque"], est["exposicion"]]
        w.writerow(fila)


def texto(vista, lineas, color=(0, 255, 0)):
    for i, t in enumerate(lineas):
        cv2.putText(vista, t, (10, 28 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)


def ajustar(cam, config: dict, ruta_config: Path) -> None:
    paso_exp = 1 if camara.WIN else 20
    while True:
        frame = camara.leer(cam, 1)
        if frame is None:
            print("Fallo leyendo frame", file=sys.stderr)
            return
        e = camara.estado(cam)
        vista = cv2.resize(frame, (960, 540))
        texto(vista, [f"autofoco={e['autofoco']} enfoque={e['enfoque']} exposicion={e['exposicion']}",
                      f"nitidez={camara.nitidez(frame):.0f} brillo={camara.brillo(frame):.0f}  {e['ancho']}x{e['alto']}",
                      "A autofoco | +/- enfoque | E/D exposicion | R exp. auto | S guardar | Q salir"])
        cv2.imshow("ajustar", vista)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            return
        if k == ord("a"):
            config["autofoco"] = not config.get("autofoco", True)
        elif k in (ord("+"), ord("=")):
            config.update(autofoco=False, enfoque=float(e["enfoque"]) + 5)
        elif k == ord("-"):
            config.update(autofoco=False, enfoque=float(e["enfoque"]) - 5)
        elif k == ord("e"):
            config["exposicion"] = float(e["exposicion"]) + paso_exp
        elif k == ord("d"):
            config["exposicion"] = float(e["exposicion"]) - paso_exp
        elif k == ord("r"):
            config.pop("exposicion", None)
        elif k == ord("s"):
            camara.guardar_config(ruta_config, config)
            print("guardado", ruta_config, config)
        if k in (ord("a"), ord("+"), ord("="), ord("-"), ord("e"), ord("d"), ord("r")):
            camara.aplicar(cam, config)


def capturar(cam, a) -> None:
    salida, ras, log = a.salida, a.salida / "rasante", a.salida / "captura_log.csv"
    salida.mkdir(parents=True, exist_ok=True)
    caja, cara = siguiente_caja(salida, a.primera_caja), 1
    ultimo = None  # (caja, cara) de la última foto normal guardada: R la repite con luz rasante
    pila = []  # fotos guardadas, para deshacer
    msg = ""
    while True:
        frame = camara.leer(cam, 1)
        if frame is None:
            print("Fallo leyendo frame", file=sys.stderr)
            return
        n = camara.nitidez(frame)
        nitida = n >= a.nitidez_min
        vista = cv2.resize(frame, (960, 540))
        texto(vista, [f"caja{caja:03d} cara {cara}/{CARAS}   nitidez={n:.0f} {'OK' if nitida else 'BORROSA'}",
                      "ESPACIO guardar | R rasante | U deshacer | N caja nueva | Q salir", msg],
              (0, 255, 0) if nitida else (0, 0, 255))
        cv2.imshow("captura", vista)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            return
        if k == ord("n"):
            caja, cara, ultimo, msg = caja + 1, 1, None, ""
        elif k == ord(" "):
            if not nitida:
                msg = "no guardada: borrosa (revisa el enfoque)"
                continue
            foto = camara.leer(cam)
            if foto is None:
                continue
            ruta = salida / f"caja{caja:03d}_cara{cara}.jpg"
            guardar(foto, ruta)
            registrar(log, ruta.name, "normal", foto, camara.estado(cam))
            pila.append(ruta)
            ultimo, msg = (caja, cara), f"guardada {ruta.name}"
            print(msg)
            cara += 1
            if cara > CARAS:
                caja, cara = caja + 1, 1
        elif k == ord("r"):
            if ultimo is None:
                msg = "primero guarda la cara normal (ESPACIO)"
                continue
            foto = camara.leer(cam)
            if foto is None:
                continue
            ruta = ras / f"caja{ultimo[0]:03d}_cara{ultimo[1]}.jpg"
            guardar(foto, ruta)
            registrar(log, f"rasante/{ruta.name}", "rasante", foto, camara.estado(cam))
            pila.append(ruta)
            msg = f"guardada rasante/{ruta.name}"
            print(msg)
        elif k == ord("u") and pila:
            ruta = pila.pop()
            ruta.unlink(missing_ok=True)
            registrar(log, ruta.name if ruta.parent == salida else f"rasante/{ruta.name}", "deshecha")
            msg = f"deshecha {ruta.name}"
            print(msg)
            if ruta.parent == salida:  # volver a esa cara
                caja, cara, ultimo = int(ruta.name[4:7]), int(ruta.stem.split("cara")[1]), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listar", action="store_true", help="prueba los índices de cámara y sale")
    ap.add_argument("--ajustar", action="store_true", help="vista para fijar enfoque/exposición y guardar --config")
    ap.add_argument("--salida", type=Path, help="carpeta de la sesión, p. ej. dataset_ia/arducam/sesion01")
    ap.add_argument("--camara", type=int, default=0, help="índice de la Arducam (ver --listar)")
    ap.add_argument("--config", type=Path, default=Path("camara.json"))
    ap.add_argument("--ancho", type=int, default=1920)
    ap.add_argument("--alto", type=int, default=1080)
    ap.add_argument("--primera-caja", type=int, default=101, help="número de la primera caja de la carpeta vacía")
    ap.add_argument("--nitidez-min", type=float, default=0.0,
                    help="no guarda fotos con nitidez menor (0 = solo informa; calíbrala con --ajustar)")
    a = ap.parse_args()

    if a.listar:
        camara.listar()
        return 0
    if not a.ajustar and a.salida is None:
        ap.error("falta --salida (o usa --listar / --ajustar)")
    config = camara.cargar_config(a.config)
    cam = camara.abrir(a.camara, a.ancho, a.alto, config)
    if cam is None:
        print(f"No se pudo abrir la cámara {a.camara}", file=sys.stderr)
        return 1
    e = camara.estado(cam)
    print(f"Cámara entrega {e['ancho']}x{e['alto']} · config: {config or 'autofoco y exposición automática'}")
    try:
        ajustar(cam, config, a.config) if a.ajustar else capturar(cam, a)
    finally:
        cam.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
