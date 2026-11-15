"""
ESP32-S3 simulada para probar el ciclo sin el hardware (pruebas de desarrollo; no es
parte del contrato). Misma interfaz que `EnlaceUART` (`enviar`, `recibir`, `close`):
confirma `activar_dispensador` con `objeto_en_posicion` y cada `girar_posicion` con
`en_posicion`, tras `retraso_s` segundos de "movimiento mecánico".

También responde `solicitar_telemetria` (informe 5.6) con valores inventados pero con todos
los campos del contrato, al instante y sin interrumpir un movimiento.

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
    EVENTO_TELEMETRIA,
    TELEM_NUM_ENCODERS,
    TELEM_NUM_PWM,
)
from uart.telemetria_esp import AlmacenTelemetria  # noqa: E402

RETRASO_POR_DEFECTO_S = 2.5


class EnlaceSimulado:
    def __init__(self, retraso_s: float = RETRASO_POR_DEFECTO_S):
        self._pendiente = None
        self._retraso_s = retraso_s
        self.telemetria = AlmacenTelemetria()
        self._t0 = time.monotonic()
        self._alarma = False
        self._mensajes = 0
        self._moviendo = False
        self._pasos = 0

    def solicitar_telemetria(self):
        """Responde al instante con valores inventados (no es un sensor real)."""
        self._pasos += 1
        self.telemetria.guardar({
            "evento": EVENTO_TELEMETRIA,
            "t_ms": int((time.monotonic() - self._t0) * 1000),
            "estado": "moviendo" if self._moviendo else "reposo",
            "alarma": self._alarma,
            "giro_dps": [0.0, 0.0, 0.0],
            "corriente_ma": [0, 0],
            "enc": [self._pasos * (i + 1) for i in range(TELEM_NUM_ENCODERS)],
            "pwm": [0] * TELEM_NUM_PWM,
            "uart_ok": self._mensajes,
            "uart_err": 0,
            "heap": 200000,
            "temp_c": 40.0,
        })

    def enviar(self, mensaje: dict):
        print(f"[esp32-sim] <- {mensaje}")
        self._mensajes += 1
        accion = mensaje.get("accion")
        if accion == ACCION_ACTIVAR_ALARMA_LOCAL:
            self._alarma = True
        elif accion == ACCION_DESACTIVAR_ALARMA_LOCAL:
            self._alarma = False
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
        self._moviendo = True
        if timeout is not None and self._retraso_s > timeout:
            time.sleep(timeout)
            self._moviendo = False
            print(f"[esp32-sim] {evento['evento']} tardaría {self._retraso_s:g}s > "
                  f"timeout {timeout:g}s: sin confirmación")
            return None
        time.sleep(self._retraso_s)
        self._moviendo = False
        print(f"[esp32-sim] -> {evento}")
        return evento

    def close(self):
        pass
