"""
Servidor de consultas de decisión del regente (puerto TCP_PUERTO_DECISION_DEFECTO).

La Orange Pi, tras un ERROR_REVISION_MANUAL, abre una conexión corta cada
INTERVALO_CONSULTA_DECISION_S y pregunta si el regente ya decidió. Una conexión
nueva por consulta, como en el envío de lotes (ver docs/propuesta_canal_regente.md,
opción A, y el bloque "Decisión del regente" de shared/protocol_constants.py).
"""
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_CONSULTA_JSON,
    CLAVE_ERROR_JSON,
    CONSULTA_DECISION_REGENTE,
    ERROR_CONSULTA_INVALIDA,
    TIMEOUT_INACTIVIDAD_RECEPCION_S,
)

from estado_panel import EstadoPanel
from framing import FramingInvalido, enviar_json, recibir_json


class _ManejadorConsulta(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(TIMEOUT_INACTIVIDAD_RECEPCION_S)
        try:
            try:
                consulta = recibir_json(conn)
            except FramingInvalido as e:
                print(f"[decision] Consulta inválida de {self.client_address}: {e}")
                enviar_json(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
                return
            if consulta.get(CLAVE_CONSULTA_JSON) != CONSULTA_DECISION_REGENTE:
                enviar_json(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
                return
            enviar_json(conn, self.server.estado.consultar_decision())
        except OSError as e:  # incluye ConnectionResetError y timeouts
            print(f"[decision] Error con {self.client_address}: {e}")


class _Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def crear_servidor_decision(estado: EstadoPanel, puerto: int, host: str = "0.0.0.0"):
    servidor = _Servidor((host, puerto), _ManejadorConsulta)
    servidor.estado = estado
    return servidor


def iniciar_servidor_decision(estado: EstadoPanel, puerto: int, host: str = "0.0.0.0"):
    """Levanta el servidor de consultas en un hilo daemon y lo devuelve."""
    servidor = crear_servidor_decision(estado, puerto, host)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor
