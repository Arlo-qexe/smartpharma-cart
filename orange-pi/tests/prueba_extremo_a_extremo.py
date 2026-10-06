"""
Prueba MANUAL de punta a punta contra la PC real (checklist PC-12): cámara
real (5 fotos reales), descubrimiento mDNS real, envío TCP real y consulta de
decisión real. Solo la ESP32-S3 se simula (EnlaceSimulado confirma los
eventos). El contenido de las fotos no importa: es un lote simulado.

Uso (desde orange-pi/, con el venv activado):
    python tests/prueba_extremo_a_extremo.py [--retraso SEGUNDOS]

`--retraso` pausa SEGUNDOS entre la confirmación `objeto_en_posicion` y la
ráfaga de fotos (p. ej. para colocar algo frente a la cámara). Por defecto 0.

Si la PC responde ERROR_REVISION_MANUAL, la prueba espera sin tope a que el
regente resuelva en el panel de la PC.
"""
import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ.parent / "shared"))

import main  # noqa: E402
from capture.camera import abrir_camara  # noqa: E402
from network.mdns_discovery import DescubridorPC  # noqa: E402
from protocol_constants import (  # noqa: E402
    ACCION_GIRAR_POSICION,
    ACCION_INTRODUCIR_OBJETO,
    EVENTO_EN_POSICION,
    EVENTO_OBJETO_EN_POSICION,
)


class EnlaceSimulado:
    """ESP32-S3 falsa: confirma `introducir_objeto` y cada `girar_posicion`."""

    def __init__(self, retraso_s: float = 0.0):
        self._pendiente = None
        self._retraso_s = retraso_s

    def enviar(self, mensaje):
        print(f"[esp32-sim] <- {mensaje}")
        accion = mensaje.get("accion")
        if accion == ACCION_INTRODUCIR_OBJETO:
            self._pendiente = {"evento": EVENTO_OBJETO_EN_POSICION}
            if self._retraso_s:
                print(f"[esp32-sim] objeto en posición; esperando {self._retraso_s:g}s antes de confirmar...")
                time.sleep(self._retraso_s)
        elif accion == ACCION_GIRAR_POSICION:
            self._pendiente = {"evento": EVENTO_EN_POSICION, "cara": mensaje["cara"]}

    def recibir(self, timeout=None):
        r, self._pendiente = self._pendiente, None
        return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--retraso", type=float, default=0.0,
                    help="segundos entre la llegada del objeto y la ráfaga de fotos")
    args = ap.parse_args()
    camara = abrir_camara()
    # Envuelve la captura para informar el tamaño de cada foto real.
    capturar = main.capturar_con_autoenfoque
    comprimir = main.comprimir_jpeg

    def comprimir_informando(frame):
        jpeg = comprimir(frame)
        print(f"[prueba] foto real {frame.shape[1]}x{frame.shape[0]}, JPEG {len(jpeg) / 1024:.0f} KB")
        return jpeg
    main.comprimir_jpeg = comprimir_informando

    t0 = time.time()
    try:
        continuar = main.ciclo_de_un_objeto(EnlaceSimulado(args.retraso), camara, DescubridorPC())
    finally:
        camara.release()
    print(f"[prueba] ciclo terminado en {time.time() - t0:.1f}s, continuar={continuar}")
