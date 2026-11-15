"""
Enlace serie con la ESP32-S3: envío/recepción de mensajes JSON con suma de
verificación XOR (ver docs/arquitectura_comunicacion.md, sección 5.1).

Un hilo lector despacha cada mensaje por nombre de evento (informe 5.6): la telemetría
puede llegar en cualquier momento, también mientras se espera una confirmación de giro,
así que va a su propio almacén y nunca se confunde con una confirmación ni con un error.
Los demás eventos van a una cola que lee `recibir()`.

Requiere: pip install pyserial
"""
import collections
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import (  # noqa: E402
    ACCION_SOLICITAR_TELEMETRIA,
    UART_BAUDRATE,
    desempaquetar_mensaje_uart,
    empaquetar_mensaje_uart,
)

from uart.telemetria_esp import AlmacenTelemetria, es_telemetria  # noqa: E402

try:
    import serial
except ImportError:
    serial = None  # permite importar este módulo en entornos sin pyserial (p. ej. para tests)

_TIMEOUT_LECTURA_S = 0.2   # el hilo lector despierta así de seguido para poder cerrarse


class EnlaceUART:
    def __init__(self, puerto: str, baudrate: int = UART_BAUDRATE, timeout: float = 5.0):
        if serial is None:
            raise RuntimeError("pyserial no está instalado (pip install pyserial)")
        self._ser = serial.Serial(puerto, baudrate=baudrate, timeout=_TIMEOUT_LECTURA_S)
        self.telemetria = AlmacenTelemetria()
        self._eventos = collections.deque()
        self._cond = threading.Condition()
        self._escritura = threading.Lock()
        self._activo = True
        self._lector = threading.Thread(target=self._leer, name="uart-lector", daemon=True)
        self._lector.start()

    def _leer(self):
        while self._activo:
            try:
                linea = self._ser.readline().decode("utf-8", errors="replace").strip()
            except (OSError, TypeError, ValueError):
                if not self._activo:
                    return
                time.sleep(_TIMEOUT_LECTURA_S)   # puerto caído: no girar en vacío
                continue
            if not linea:
                continue   # timeout de lectura, no llegó nada
            mensaje = desempaquetar_mensaje_uart(linea)
            if mensaje is None:
                print(f"[UART] Mensaje descartado (checksum inválido o formato incorrecto): {linea!r}")
            elif es_telemetria(mensaje):
                self.telemetria.guardar(mensaje)
            else:
                with self._cond:
                    self._eventos.append(mensaje)
                    self._cond.notify_all()

    def enviar(self, mensaje: dict):
        """Descarta los eventos que hayan quedado sin leer (p. ej. uno tardío de una orden
        anterior que ya se dio por fallida) antes de enviar: la ESP32-S3 solo emite eventos
        en respuesta a una orden, así que nada legítimo se pierde, y se evita confirmar una
        orden con el evento de otra. La telemetría no pasa por esa cola."""
        with self._escritura:
            with self._cond:
                self._eventos.clear()
            self._ser.write(empaquetar_mensaje_uart(mensaje))

    def solicitar_telemetria(self):
        """Pide la telemetría (informe 5.6). NO limpia la cola de eventos: otro hilo puede
        estar esperando una confirmación de giro."""
        with self._escritura:
            self._ser.write(empaquetar_mensaje_uart({"accion": ACCION_SOLICITAR_TELEMETRIA}))

    def recibir(self, timeout: float = None):
        """Siguiente evento de control (no telemetría), ya validado. Devuelve el dict, o
        None si pasó el timeout sin que llegara nada válido."""
        with self._cond:
            self._cond.wait_for(lambda: self._eventos, timeout=timeout)
            return self._eventos.popleft() if self._eventos else None

    def close(self):
        self._activo = False
        self._lector.join(timeout=1.0)
        self._ser.close()
