"""
Servidor TCP falso que simula la PC: responde según el contrato definido en
shared/protocol_constants.py, sin ejecutar ningún reconocimiento real.

Úsalo para desarrollar y probar network/tcp_client.py sin depender de que el
lado `pc/` esté listo.

Uso:
    python3 tests/mock_pc_server.py
"""
import json
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_RESULTADO_JSON,
    FRAMING_STRUCT_FORMAT,
    RESULTADO_ERROR_REVISION_MANUAL,
    TCP_PUERTO_DEFECTO,
)


def _recv_exacto(sock, n):
    datos = b""
    while len(datos) < n:
        chunk = sock.recv(n - len(datos))
        if not chunk:
            raise ConnectionResetError("Conexión cerrada durante recv")
        datos += chunk
    return datos


def _recibir_lote(sock):
    raw_cantidad = _recv_exacto(sock, 4)
    (cantidad,) = struct.unpack(FRAMING_STRUCT_FORMAT, raw_cantidad)
    imagenes = []
    for _ in range(cantidad):
        raw_tam = _recv_exacto(sock, 4)
        (tam,) = struct.unpack(FRAMING_STRUCT_FORMAT, raw_tam)
        imagenes.append(_recv_exacto(sock, tam))
    return imagenes


def _enviar_respuesta(sock, diccionario):
    payload = json.dumps(diccionario).encode("utf-8")
    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(payload)))
    sock.sendall(payload)


def _manejar_cliente(conn, addr, respuesta_fija):
    print(f"[mock_pc] Conexión de {addr}")
    try:
        imagenes = _recibir_lote(conn)
        print(f"[mock_pc] Lote recibido: {len(imagenes)} imágenes, "
              f"tamaños: {[len(i) for i in imagenes]}")

        if len(imagenes) == 0:
            resultado = RESULTADO_ERROR_REVISION_MANUAL
        else:
            resultado = respuesta_fija

        _enviar_respuesta(conn, {CLAVE_RESULTADO_JSON: resultado})
        print(f"[mock_pc] Respuesta enviada: {resultado}")
    except (ConnectionResetError, struct.error, OSError) as e:
        print(f"[mock_pc] Error con {addr}: {e}")
    finally:
        conn.close()


def iniciar_mock(puerto: int = TCP_PUERTO_DEFECTO, respuesta_fija: str = "TIPO_A"):
    """Nota: este mock NO anuncia el servicio por mDNS — para probar el flujo
    completo de descubrimiento, apunta el cliente directamente a
    ('127.0.0.1', puerto), o registra el servicio con `zeroconf` aquí mismo
    si necesitas probar también la parte de mDNS."""
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(("0.0.0.0", puerto))
    servidor.listen(1)
    print(f"[mock_pc] Escuchando en el puerto {puerto} (sin mDNS, solo TCP directo)")

    while True:
        conn, addr = servidor.accept()
        hilo = threading.Thread(target=_manejar_cliente, args=(conn, addr, respuesta_fija), daemon=True)
        hilo.start()


if __name__ == "__main__":
    iniciar_mock()
