"""
Enlace serie con la ESP32-S3: envío/recepción de mensajes JSON con suma de
verificación XOR (ver docs/arquitectura_comunicacion.md, sección 5.1).

Requiere: pip install pyserial
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import (  # noqa: E402
    UART_BAUDRATE,
    desempaquetar_mensaje_uart,
    empaquetar_mensaje_uart,
)

try:
    import serial
except ImportError:
    serial = None  # permite importar este módulo en entornos sin pyserial (p. ej. para tests)


class EnlaceUART:
    def __init__(self, puerto: str, baudrate: int = UART_BAUDRATE, timeout: float = 5.0):
        if serial is None:
            raise RuntimeError("pyserial no está instalado (pip install pyserial)")
        self._ser = serial.Serial(puerto, baudrate=baudrate, timeout=timeout)

    def enviar(self, mensaje: dict):
        self._ser.write(empaquetar_mensaje_uart(mensaje))

    def recibir(self, timeout: float = None):
        """Lee una línea y valida su checksum. Devuelve el dict decodificado,
        o None si hubo timeout o el checksum no coincide (mensaje corrupto)."""
        if timeout is not None:
            self._ser.timeout = timeout
        linea = self._ser.readline().decode("utf-8", errors="replace").strip()
        if not linea:
            return None  # timeout, no llegó nada
        mensaje = desempaquetar_mensaje_uart(linea)
        if mensaje is None:
            print(f"[UART] Mensaje descartado (checksum inválido o formato incorrecto): {linea!r}")
        return mensaje

    def close(self):
        self._ser.close()
