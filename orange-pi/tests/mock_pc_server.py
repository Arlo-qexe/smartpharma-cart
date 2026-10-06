"""
Servidor TCP falso que simula la PC: responde según el contrato definido en
shared/protocol_constants.py, sin ejecutar ningún reconocimiento real.

Atiende los DOS puertos de la PC:
  - TCP_PUERTO_DEFECTO (5000): lotes de imágenes -> {"clasificacion": ...}
  - TCP_PUERTO_DECISION_DEFECTO (5001): consulta de la decisión del regente
    (sección 4.5): pendiente -> resuelta, o ninguna.

Úsalo para desarrollar y probar network/ sin depender de que el lado `pc/`
esté listo.

Uso:
    python3 tests/mock_pc_server.py                      # lote OK -> TIPO_A
    python3 tests/mock_pc_server.py --error              # todo lote -> ERROR_REVISION_MANUAL,
                                                         # y el "regente" decide a los 6 s: TIPO_B
    python3 tests/mock_pc_server.py --error --segundos 20 --destino DESCARTE
    python3 tests/mock_pc_server.py --error --sin-decision   # la consulta responde "ninguna"

Un lote vacío (captura fallida) siempre responde ERROR_REVISION_MANUAL, como
la PC real. Un lote con error abre una decisión que reemplaza a la anterior;
un lote sin error la cierra (la consulta responde "ninguna"), igual que la PC.
"""
import argparse
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_CONSULTA_JSON,
    CLAVE_DESTINO_JSON,
    CLAVE_ERROR_JSON,
    CLAVE_ESTADO_DECISION_JSON,
    CLAVE_RESULTADO_JSON,
    CONSULTA_DECISION_REGENTE,
    ERROR_CONSULTA_INVALIDA,
    ESTADO_DECISION_NINGUNA,
    ESTADO_DECISION_PENDIENTE,
    ESTADO_DECISION_RESUELTA,
    FRAMING_STRUCT_FORMAT,
    RESULTADO_ERROR_REVISION_MANUAL,
    TAMANO_MAX_MENSAJE_JSON_BYTES,
    TCP_PUERTO_DECISION_DEFECTO,
    TCP_PUERTO_DEFECTO,
)


class DecisionEnEspera:
    """Una sola decisión en espera, sin IDs (como la PC real). Se "resuelve"
    sola `segundos_hasta_decision` después de abrirse; no se borra al leerla."""

    def __init__(self, segundos_hasta_decision: float, destino: str, con_decision: bool = True):
        self.segundos, self.destino, self.con_decision = segundos_hasta_decision, destino, con_decision
        self._abierta_en = None
        self._lock = threading.Lock()

    def abrir(self):
        with self._lock:
            self._abierta_en = time.monotonic() if self.con_decision else None

    def cerrar(self):
        with self._lock:
            self._abierta_en = None

    def consultar(self) -> dict:
        with self._lock:
            if self._abierta_en is None:
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_NINGUNA}
            if time.monotonic() - self._abierta_en < self.segundos:
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_PENDIENTE}
            return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_RESUELTA,
                    CLAVE_DESTINO_JSON: self.destino}


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


def _manejar_lote(conn, addr, respuesta_fija, decision):
    print(f"[mock_pc] Conexión de lotes {addr}")
    try:
        imagenes = _recibir_lote(conn)
        print(f"[mock_pc] Lote recibido: {len(imagenes)} imágenes, "
              f"tamaños: {[len(i) for i in imagenes]}")

        resultado = RESULTADO_ERROR_REVISION_MANUAL if len(imagenes) == 0 else respuesta_fija
        if resultado == RESULTADO_ERROR_REVISION_MANUAL:
            decision.abrir()   # antes de responder: la 1.ª consulta ya ve "pendiente"
        else:
            decision.cerrar()

        _enviar_respuesta(conn, {CLAVE_RESULTADO_JSON: resultado})
        print(f"[mock_pc] Respuesta enviada: {resultado}")
    except (ConnectionResetError, struct.error, OSError) as e:
        print(f"[mock_pc] Error con {addr}: {e}")
    finally:
        conn.close()


def _manejar_consulta(conn, addr, decision):
    try:
        conn.settimeout(5)
        (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, _recv_exacto(conn, 4))
        if longitud > TAMANO_MAX_MENSAJE_JSON_BYTES:
            _enviar_respuesta(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
            return
        try:
            consulta = json.loads(_recv_exacto(conn, longitud).decode("utf-8"))
        except ValueError:
            consulta = None
        if not isinstance(consulta, dict) or consulta.get(CLAVE_CONSULTA_JSON) != CONSULTA_DECISION_REGENTE:
            _enviar_respuesta(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
            return
        respuesta = decision.consultar()
        print(f"[mock_pc] Consulta de decisión de {addr}: {respuesta}")
        _enviar_respuesta(conn, respuesta)
    except (ConnectionResetError, struct.error, OSError) as e:
        print(f"[mock_pc] Error de consulta con {addr}: {e}")
    finally:
        conn.close()


def _escuchar(puerto, manejador, args_extra, nombre):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(("0.0.0.0", puerto))
    servidor.listen(5)
    print(f"[mock_pc] {nombre}: escuchando en el puerto {puerto} (sin mDNS, solo TCP directo)")

    def bucle():
        while True:
            conn, addr = servidor.accept()
            threading.Thread(target=manejador, args=(conn, addr, *args_extra), daemon=True).start()

    threading.Thread(target=bucle, daemon=True).start()
    return servidor


def iniciar_mock(puerto: int = TCP_PUERTO_DEFECTO, respuesta_fija: str = "TIPO_A",
                 puerto_decision: int = TCP_PUERTO_DECISION_DEFECTO,
                 segundos_hasta_decision: float = 6.0, destino_decision: str = "TIPO_B",
                 con_decision: bool = True, bloquear: bool = True):
    """Nota: este mock NO anuncia el servicio por mDNS — para probar el flujo
    completo de descubrimiento, apunta el cliente directamente a
    ('127.0.0.1', puerto), o registra el servicio con `zeroconf` aquí mismo
    si necesitas probar también la parte de mDNS. Con `bloquear=False`
    devuelve (decision, [sockets]) para que una prueba lo controle."""
    decision = DecisionEnEspera(segundos_hasta_decision, destino_decision, con_decision)
    sockets = [
        _escuchar(puerto, _manejar_lote, (respuesta_fija, decision), "lotes"),
        _escuchar(puerto_decision, _manejar_consulta, (decision,), "decisión"),
    ]
    if not bloquear:
        return decision, sockets
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--error", action="store_true", help="todo lote responde ERROR_REVISION_MANUAL")
    ap.add_argument("--segundos", type=float, default=6.0, help="segundos hasta que el regente decide")
    ap.add_argument("--destino", default="TIPO_B", help="destino que decide el regente (TIPO_X o DESCARTE)")
    ap.add_argument("--sin-decision", action="store_true", help="la consulta responde 'ninguna'")
    a = ap.parse_args()
    iniciar_mock(respuesta_fija=RESULTADO_ERROR_REVISION_MANUAL if a.error else "TIPO_A",
                 segundos_hasta_decision=a.segundos, destino_decision=a.destino,
                 con_decision=not a.sin_decision)
