"""
Servidor de consultas de decisión del regente (puerto TCP_PUERTO_DECISION_DEFECTO).

La Orange Pi, tras un ERROR_REVISION_MANUAL, abre una conexión corta cada
INTERVALO_CONSULTA_DECISION_S y pregunta si el regente ya decidió. Una conexión
nueva por consulta, como en el envío de lotes (ver docs/propuesta_canal_regente.md,
opción A, y el bloque "Decisión del regente" de shared/protocol_constants.py).
"""
import logging
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_CONSULTA_JSON,
    CLAVE_DESTINO_JSON,
    CLAVE_ERROR_JSON,
    CLAVE_ESTADO_DECISION_JSON,
    CONSULTA_DECISION_REGENTE,
    ERROR_CONSULTA_INVALIDA,
    TIMEOUT_INACTIVIDAD_RECEPCION_S,
)

from estado_panel import EstadoPanel
from framing import ConexionSinDatos, FramingInvalido, enviar_json, recibir_json

log = logging.getLogger("decision")


class _ManejadorConsulta(socketserver.BaseRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(TIMEOUT_INACTIVIDAD_RECEPCION_S)
        try:
            try:
                consulta = recibir_json(conn)
            except FramingInvalido as e:
                log.warning("Consulta inválida de %s: %s", self.client_address, e)
                enviar_json(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
                return
            if consulta.get(CLAVE_CONSULTA_JSON) != CONSULTA_DECISION_REGENTE:
                enviar_json(conn, {CLAVE_ERROR_JSON: ERROR_CONSULTA_INVALIDA})
                return
            respuesta = self.server.estado.consultar_decision()
            enviar_json(conn, respuesta)
            self._registrar_si_cambio(respuesta)
        except ConexionSinDatos:
            log.info("%s abrió y cerró la conexión sin enviar datos (sonda de conectividad)",
                     self.client_address[0])
        except OSError as e:  # incluye ConnectionResetError y timeouts
            log.warning("Error con %s: %s", self.client_address, e)

    def _registrar_si_cambio(self, respuesta):
        """La Orange Pi consulta cada 2 s: se registra solo cuando cambia lo
        entregado a ese cliente, para ver (p. ej.) que recibió `resuelta`."""
        ip = self.client_address[0]
        clave = (respuesta.get(CLAVE_ESTADO_DECISION_JSON), respuesta.get(CLAVE_DESTINO_JSON))
        with self.server.lock_registro:
            if self.server.ultimo_entregado.get(ip) == clave:
                return
            self.server.ultimo_entregado[ip] = clave
        detalle = f" (destino {clave[1]})" if clave[1] else ""
        log.info("%s recibió: %s%s", ip, clave[0], detalle)


class _Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def crear_servidor_decision(estado: EstadoPanel, puerto: int, host: str = "0.0.0.0"):
    servidor = _Servidor((host, puerto), _ManejadorConsulta)
    servidor.estado = estado
    servidor.ultimo_entregado = {}      # ip -> (estado, destino) ya registrado
    servidor.lock_registro = threading.Lock()
    return servidor


def iniciar_servidor_decision(estado: EstadoPanel, puerto: int, host: str = "0.0.0.0"):
    """Levanta el servidor de consultas en un hilo daemon y lo devuelve."""
    servidor = crear_servidor_decision(estado, puerto, host)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor
