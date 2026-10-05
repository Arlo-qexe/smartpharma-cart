"""
Cliente TCP: envía el lote de imágenes a la PC y espera la clasificación,
con reintentos y caché de mDNS (ver docs/arquitectura_comunicacion.md,
secciones 4.1 a 4.4).
"""
import json
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import (  # noqa: E402
    FRAMING_STRUCT_FORMAT,
    RESULTADO_ERROR_REVISION_MANUAL,
    TIMEOUT_CONEXION_TCP_S,
    TIMEOUT_RESPUESTA_RECONOCIMIENTO_S,
    TIMEOUT_TOTAL_TRANSACCION_S,
)


def _recv_exacto(sock, n):
    datos = b""
    while len(datos) < n:
        chunk = sock.recv(n - len(datos))
        if not chunk:
            raise ConnectionResetError("Conexión cerrada durante recv")
        datos += chunk
    return datos


def _recibir_json_con_longitud(sock):
    raw_len = _recv_exacto(sock, 4)
    (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, raw_len)
    return _recv_exacto(sock, longitud).decode("utf-8")


def procesar_lote(descubridor, imagenes: list) -> dict:
    """Envía `imagenes` (lista de bytes JPEG, puede estar vacía si la
    captura falló) a la PC, reintentando hasta TIMEOUT_TOTAL_TRANSACCION_S.

    Devuelve el dict de respuesta de la PC, o
    {"clasificacion": RESULTADO_ERROR_REVISION_MANUAL} si se agota el tiempo.
    Este es el único punto donde se debe llamar a
    `activar_alarma_local` en el orquestador (ver src/main.py), porque aquí
    es donde se sabe que la falla fue de red/PC, no de reconocimiento.
    """
    inicio = time.time()
    intento = 0

    while (time.time() - inicio) < TIMEOUT_TOTAL_TRANSACCION_S:
        intento += 1
        sock = None
        try:
            ip, puerto = descubridor.obtener_destino()
            sock = socket.create_connection((ip, puerto), timeout=TIMEOUT_CONEXION_TCP_S)

            sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(imagenes)))
            for img in imagenes:
                sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(img)))
                sock.sendall(img)

            sock.settimeout(TIMEOUT_RESPUESTA_RECONOCIMIENTO_S)
            respuesta = _recibir_json_con_longitud(sock)
            sock.close()
            return json.loads(respuesta)

        except (BrokenPipeError, ConnectionResetError, socket.timeout,
                ConnectionRefusedError, OSError) as e:
            print(f"[TCP] Intento {intento} fallido: {e}")
            if sock:
                sock.close()
            descubridor.invalidar()
            time.sleep(1)

    print("[TCP] Se agotó el tiempo total de la transacción con la PC.")
    return {"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}
