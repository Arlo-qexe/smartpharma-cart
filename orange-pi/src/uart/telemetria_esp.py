"""
Última telemetría recibida de la ESP32-S3 (informe 5.6), compartida entre el hilo que
lee el UART y los hilos que la consumen. Es solo observación: nada aquí puede frenar el ciclo.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import EVENTO_TELEMETRIA  # noqa: E402


class AlmacenTelemetria:
    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self._cond = threading.Condition()
        self._datos = None
        self._t = None
        self._n = 0

    def guardar(self, mensaje: dict):
        """Guarda un evento `telemetria` (sin el campo "evento")."""
        with self._cond:
            self._datos = {k: v for k, v in mensaje.items() if k != "evento"}
            self._t = self._reloj()
            self._n += 1
            self._cond.notify_all()

    def ultima(self):
        """(datos, edad_s) de la última telemetría, o (None, None) si aún no hubo ninguna."""
        with self._cond:
            if self._datos is None:
                return None, None
            return dict(self._datos), round(self._reloj() - self._t, 1)

    def contador(self) -> int:
        with self._cond:
            return self._n

    def esperar_nueva(self, desde: int, timeout: float) -> bool:
        """Espera hasta `timeout` s a que llegue una telemetría posterior a `desde`."""
        with self._cond:
            return self._cond.wait_for(lambda: self._n > desde, timeout=timeout)


def es_telemetria(mensaje) -> bool:
    return isinstance(mensaje, dict) and mensaje.get("evento") == EVENTO_TELEMETRIA
