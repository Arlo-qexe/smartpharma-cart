"""
ESP32-S3 simulada para probar el ciclo sin el hardware (pruebas de desarrollo; no es
parte del contrato). Misma interfaz que `EnlaceUART` (`enviar`, `recibir`, `close`):
confirma `activar_dispensador` con `objeto_en_posicion` y cada `girar_posicion` con
`en_posicion`, tras `retraso_s` segundos de "movimiento mecánico".

Si `retraso_s` supera el `timeout` con que el orquestador espera la confirmación, la
confirmación nunca llega (simula un fallo mecánico): el ciclo debe abortar el lote o
agotar los reintentos.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import (  # noqa: E402
    ACCION_ACTIVAR_ALARMA_LOCAL,
    ACCION_ACTIVAR_DISPENSADOR,
    ACCION_CLASIFICAR,
    ACCION_DESACTIVAR_ALARMA_LOCAL,
    ACCION_GIRAR_POSICION,
    EVENTO_EN_POSICION,
    EVENTO_OBJETO_EN_POSICION,
)

RETRASO_POR_DEFECTO_S = 2.5


class EnlaceSimulado:
    def __init__(self, retraso_s: float = RETRASO_POR_DEFECTO_S):
        self._pendiente = None
        self._retraso_s = retraso_s

    def enviar(self, mensaje: dict):
        print(f"[esp32-sim] <- {mensaje}")
        accion = mensaje.get("accion")
        if accion == ACCION_ACTIVAR_DISPENSADOR:
            self._pendiente = {"evento": EVENTO_OBJETO_EN_POSICION}
        elif accion == ACCION_GIRAR_POSICION:
            self._pendiente = {"evento": EVENTO_EN_POSICION, "cara": mensaje["cara"]}
        elif accion not in (ACCION_ACTIVAR_ALARMA_LOCAL, ACCION_DESACTIVAR_ALARMA_LOCAL,
                            ACCION_CLASIFICAR):
            print(f"[esp32-sim] acción desconocida, se ignora: {accion!r}")

    def recibir(self, timeout=None):
        evento, self._pendiente = self._pendiente, None
        if evento is None:
            return None
        if timeout is not None and self._retraso_s > timeout:
            time.sleep(timeout)
            print(f"[esp32-sim] {evento['evento']} tardaría {self._retraso_s:g}s > "
                  f"timeout {timeout:g}s: sin confirmación")
            return None
        time.sleep(self._retraso_s)
        print(f"[esp32-sim] -> {evento}")
        return evento

    def close(self):
        pass
