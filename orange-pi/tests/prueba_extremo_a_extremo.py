"""
Prueba MANUAL de punta a punta contra la PC real (checklist PC-12): cámara
real (5 fotos reales), descubrimiento mDNS real, envío TCP real y consulta de
decisión real. Solo la ESP32-S3 se simula (EnlaceSimulado confirma los
eventos). El contenido de las fotos no importa: es un lote simulado.

Uso (desde orange-pi/, con el venv activado):
    python tests/prueba_extremo_a_extremo.py [--retraso SEGUNDOS]

`--retraso` simula lo que tarda el mecanismo en cada movimiento (activar el
dispensador y cada uno de los 5 giros): la ESP32-S3 simulada tarda SEGUNDOS en
confirmar. Por defecto 2.5 s. Si supera el `timeout` con que main.py espera la
confirmación (5 s), la confirmación nunca llega: simula un fallo mecánico y
main.py debe abortar el lote (informe 6.2) o agotar los reintentos (5.4).

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
from uart.esp_simulada import EnlaceSimulado  # noqa: E402


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--retraso", type=float, default=2.5,
                    help="segundos que tarda cada movimiento en confirmarse (def. 2.5)")
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
