"""
Framing de mensajes JSON con prefijo de longitud (sección 4.2 del informe):
[4 bytes: longitud] + [bytes JSON UTF-8]. Lo usan el servidor de lotes
(server.py) y el servidor de consultas de decisión (servidor_decision.py).
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    FRAMING_LENGTH_BYTES,
    FRAMING_STRUCT_FORMAT,
    TAMANO_MAX_MENSAJE_JSON_BYTES,
)


class FramingInvalido(Exception):
    """El mensaje viola los límites de validación del framing."""


def recv_exacto(sock, n):
    datos = b""
    while len(datos) < n:
        chunk = sock.recv(n - len(datos))
        if not chunk:
            raise ConnectionResetError("Conexión cerrada durante recv")
        datos += chunk
    return datos


def enviar_json(sock, diccionario):
    payload = json.dumps(diccionario).encode("utf-8")
    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(payload)))
    sock.sendall(payload)


def recibir_json(sock, max_bytes=TAMANO_MAX_MENSAJE_JSON_BYTES):
    """Lee un mensaje JSON con prefijo de longitud. Lanza FramingInvalido si
    la longitud excede `max_bytes` o el contenido no es un objeto JSON."""
    (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, recv_exacto(sock, FRAMING_LENGTH_BYTES))
    if longitud > max_bytes:
        raise FramingInvalido(f"longitud {longitud} > {max_bytes}")
    try:
        datos = json.loads(recv_exacto(sock, longitud).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FramingInvalido(f"JSON inválido: {e}")
    if not isinstance(datos, dict):
        raise FramingInvalido("el JSON no es un objeto")
    return datos
