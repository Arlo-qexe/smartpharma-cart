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
    CLAVE_CONSULTA_JSON,
    CLAVE_RESULTADO_JSON,
    CONSULTA_DECISION_REGENTE,
    CONSULTA_ESTADO_CICLO,
    CONSULTA_ORDEN_CICLO,
    CONSULTA_TELEMETRIA,
    FRAMING_STRUCT_FORMAT,
    TCP_PUERTO_DECISION_DEFECTO,
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


def _leer_respuesta(sock):
    (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, _recv_exacto(sock, 4))
    return json.loads(_recv_exacto(sock, longitud).decode("utf-8"))


def consultar_decision(ip="127.0.0.1", puerto=TCP_PUERTO_DECISION_DEFECTO):
    """Imita UNA consulta de la Orange Pi tras un ERROR_REVISION_MANUAL: conexión
    nueva, consulta con framing, lee la respuesta y cierra. Devuelve el dict, p. ej.
    {"estado": "pendiente"} o {"estado": "resuelta", "destino": "TIPO_A"}."""
    consulta = json.dumps({CLAVE_CONSULTA_JSON: CONSULTA_DECISION_REGENTE}).encode("utf-8")
    with socket.create_connection((ip, puerto), timeout=3) as sock:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(consulta)) + consulta)
        return _leer_respuesta(sock)


def consultar_orden_ciclo(ip="127.0.0.1", puerto=TCP_PUERTO_DECISION_DEFECTO):
    """Imita UNA consulta de la Orange Pi al arrancar (informe 4.7): devuelve
    {"orden": "esperando"} o {"orden": "iniciar"}."""
    consulta = json.dumps({CLAVE_CONSULTA_JSON: CONSULTA_ORDEN_CICLO}).encode("utf-8")
    with socket.create_connection((ip, puerto), timeout=3) as sock:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(consulta)) + consulta)
        return _leer_respuesta(sock)


def consultar_estado_ciclo(ip="127.0.0.1", puerto=TCP_PUERTO_DECISION_DEFECTO):
    """Imita UNA consulta de la Orange Pi ANTES de cada objeto (informe 4.8): devuelve
    {"ciclo": "continuar"} o {"ciclo": "detener"}."""
    consulta = json.dumps({CLAVE_CONSULTA_JSON: CONSULTA_ESTADO_CICLO}).encode("utf-8")
    with socket.create_connection((ip, puerto), timeout=3) as sock:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(consulta)) + consulta)
        return _leer_respuesta(sock)


def telemetria_de_ejemplo(t=0):
    """Una telemetría con la forma del contrato (informe 4.9 y 5.6), con valores inventados que varían con `t`."""
    esp32 = {"t_ms": 123456 + 1000 * t, "estado": "reposo" if t % 6 < 4 else "moviendo", "alarma": False,
             "giro_dps": [0.1, -0.2, 0.0], "corriente_ma": [120 + t % 7, 95 + t % 5],
             "enc": [1200 + 10 * t, -300, 0, 55, 900], "pwm": [0] * 11, "uart_ok": 120 + t, "uart_err": 0,
             "heap": 210000 - 100 * t, "temp_c": 41.5 + (t % 10) / 10}
    orange_pi = {"t_s": 600 + 5 * t, "fase": "esperando_orden", "cpu_temp_c": 48.0 + (t % 8) / 2,
                 "cpu_pct": 12.0 + t % 20, "ram_pct": 38.0, "disco_pct": 41.0, "wifi_dbm": -52,
                 "rtt_pc_ms": 14, "camara": "disponible", "uart": "ok", "ciclos": t, "lotes_vacios": 0,
                 "ultimo_ciclo_s": 21.4}
    return {"orange_pi": orange_pi, "esp32": esp32, "esp32_edad_s": 0.8}


def enviar_telemetria(mensaje=None, ip="127.0.0.1", puerto=TCP_PUERTO_DECISION_DEFECTO):
    """Imita UNA telemetría de la Orange Pi (informe 4.9): devuelve {"ok": true}, o {"error": ...} si la
    PC no la aceptó. `mensaje` lleva `orange_pi`, `esp32` y `esp32_edad_s` (sin `consulta`)."""
    cuerpo = json.dumps({CLAVE_CONSULTA_JSON: CONSULTA_TELEMETRIA, **(mensaje or telemetria_de_ejemplo())}).encode("utf-8")
    with socket.create_connection((ip, puerto), timeout=3) as sock:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(cuerpo)) + cuerpo)
        return _leer_respuesta(sock)


def enviar_lote_de_prueba(ip="127.0.0.1", puerto=TCP_PUERTO_DEFECTO, num_imagenes=5, tamano_bytes=2000):
    sock = socket.create_connection((ip, puerto), timeout=3)

    imagenes = [bytes([i % 256]) * tamano_bytes for i in range(num_imagenes)]

    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(imagenes)))
    for img in imagenes:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(img)))
        sock.sendall(img)

    respuesta = _leer_respuesta(sock)

    print(f"Respuesta del servidor: {respuesta[CLAVE_RESULTADO_JSON]}")
    sock.close()


def enviar_lote_vacio(ip="127.0.0.1", puerto=TCP_PUERTO_DEFECTO):
    """Simula un fallo de captura del lado Orange Pi (ver sección 4.2:
    cantidad de imágenes = 0 -> la PC debe responder ERROR_REVISION_MANUAL
    sin intentar OCR)."""
    sock = socket.create_connection((ip, puerto), timeout=3)
    sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, 0))
    respuesta = _leer_respuesta(sock)
    print(f"Respuesta del servidor (lote vacío): {respuesta[CLAVE_RESULTADO_JSON]}")
    sock.close()


if __name__ == "__main__":
    print("--- Caso normal (5 imágenes) ---")
    enviar_lote_de_prueba()
    print("\n--- Caso de fallo de captura (lote vacío) ---")
    enviar_lote_vacio()
