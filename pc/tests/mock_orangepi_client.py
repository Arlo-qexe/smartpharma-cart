"""
Cliente TCP falso que simula la Orange Pi: se conecta directamente (sin
mDNS) al servidor de la PC, envía un lote de imágenes de prueba, y muestra
la respuesta. Úsalo para probar src/server.py sin depender del hardware real.

Uso:
    # en una terminal:
    python3 src/server.py
    # en otra terminal:
    python3 tests/mock_orangepi_client.py
"""
import json
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_RESULTADO_JSON,
    FRAMING_STRUCT_FORMAT,
    TCP_PUERTO_DEFECTO,
)


def enviar_lote_de_prueba(ip="127.0.0.1", puerto=TCP_PUERTO_DEFECTO, num_imagenes=5, tamano_bytes=2000):
    sock = socket.create_connection((ip, puerto), timeout=3)

    imagenes = [bytes([i % 256]) * tamano_bytes for i in range(num_imagenes)]

    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(imagenes)))
    for img in imagenes:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(img)))
        sock.sendall(img)

    raw_len = sock.recv(4)
    (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, raw_len)
    payload = sock.recv(longitud)
    respuesta = json.loads(payload.decode("utf-8"))

    print(f"Respuesta del servidor: {respuesta[CLAVE_RESULTADO_JSON]}")
    sock.close()


def enviar_lote_vacio(ip="127.0.0.1", puerto=TCP_PUERTO_DEFECTO):
    """Simula un fallo de captura del lado Orange Pi (ver sección 4.2:
    cantidad de imágenes = 0 -> la PC debe responder ERROR_REVISION_MANUAL
    sin intentar OCR)."""
    sock = socket.create_connection((ip, puerto), timeout=3)
    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, 0))
    raw_len = sock.recv(4)
    (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, raw_len)
    payload = sock.recv(longitud)
    respuesta = json.loads(payload.decode("utf-8"))
    print(f"Respuesta del servidor (lote vacío): {respuesta[CLAVE_RESULTADO_JSON]}")
    sock.close()


if __name__ == "__main__":
    print("--- Caso normal (5 imágenes) ---")
    enviar_lote_de_prueba()
    print("\n--- Caso de fallo de captura (lote vacío) ---")
    enviar_lote_vacio()
