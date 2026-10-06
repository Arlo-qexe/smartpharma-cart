"""
Cliente de la decisión del regente (opción A de docs/propuesta_canal_regente.md,
sección 4.5 de docs/arquitectura_comunicacion.md): tras un ERROR_REVISION_MANUAL
de la PC, consulta cada INTERVALO_CONSULTA_DECISION_S, con una conexión TCP
nueva por consulta, hasta que el regente resuelva.

La espera del regente es indefinida (decisión del equipo). Lo único que sí
tiene límite es la caída de la PC: si las consultas fallan de forma continua
durante TIMEOUT_TOTAL_TRANSACCION_S se activa la alarma física local (una vez).
"""
import json
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_CONSULTA_JSON,
    CLAVE_DESTINO_JSON,
    CLAVE_ERROR_JSON,
    CLAVE_ESTADO_DECISION_JSON,
    CONSULTA_DECISION_REGENTE,
    ESTADO_DECISION_NINGUNA,
    ESTADO_DECISION_PENDIENTE,
    ESTADO_DECISION_RESUELTA,
    FRAMING_STRUCT_FORMAT,
    INTERVALO_CONSULTA_DECISION_S,
    TCP_PUERTO_DECISION_DEFECTO,
    TIMEOUT_CONEXION_TCP_S,
    TIMEOUT_RESPUESTA_RECONOCIMIENTO_S,
    TIMEOUT_TOTAL_TRANSACCION_S,
)


class DecisionPerdida(Exception):
    """La PC respondió `ninguna`: perdió la decisión en espera (p. ej. se
    reinició). La caja debe quedarse en posición y activarse la alarma local."""


class RespuestaInesperada(Exception):
    """La PC respondió algo fuera del contrato (error de programación)."""


def _recv_exacto(sock, n):
    datos = b""
    while len(datos) < n:
        chunk = sock.recv(n - len(datos))
        if not chunk:
            raise ConnectionResetError("Conexión cerrada durante recv")
        datos += chunk
    return datos


def consultar_decision(ip: str, puerto: int = TCP_PUERTO_DECISION_DEFECTO) -> dict:
    """UNA consulta: conexión nueva, consulta con framing, lee la respuesta y
    cierra. Lanza OSError si la conexión o la lectura fallan."""
    consulta = json.dumps({CLAVE_CONSULTA_JSON: CONSULTA_DECISION_REGENTE}).encode("utf-8")
    with socket.create_connection((ip, puerto), timeout=TIMEOUT_CONEXION_TCP_S) as sock:
        sock.settimeout(TIMEOUT_RESPUESTA_RECONOCIMIENTO_S)
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(consulta)) + consulta)
        (longitud,) = struct.unpack(FRAMING_STRUCT_FORMAT, _recv_exacto(sock, 4))
        return json.loads(_recv_exacto(sock, longitud).decode("utf-8"))


def esperar_decision(descubridor, activar_alarma_local, desactivar_alarma_local=None,
                     intervalo: float = INTERVALO_CONSULTA_DECISION_S,
                     consultar=consultar_decision,
                     dormir=time.sleep, reloj=time.monotonic) -> str:
    """Bloquea (sin tope) hasta que el regente resuelva y devuelve el destino
    (`TIPO_X` o `DESCARTE`).

    - `pendiente`: sigue consultando cada `intervalo`.
    - `ninguna`: lanza DecisionPerdida (el llamador mantiene la caja y activa
      la alarma local).
    - Fallo de conexión: invalida la caché mDNS y reintenta; tras
      TIMEOUT_TOTAL_TRANSACCION_S seguidos de fallos llama una vez a
      `activar_alarma_local` y sigue intentando; en cuanto la PC vuelve a
      responder llama a `desactivar_alarma_local` (si se dio) y la alarma
      puede volver a activarse en una caída posterior.
    """
    fallando_desde = None
    alarma_activada = False

    while True:
        try:
            ip, _ = descubridor.obtener_destino_sin_bloquear()
            if ip is None:
                raise ConnectionError("PC no encontrada por mDNS")
            respuesta = consultar(ip)
        except (OSError, ValueError) as e:
            ahora = reloj()
            if fallando_desde is None:
                fallando_desde = ahora
            print(f"[decision] Consulta fallida: {e}")
            descubridor.invalidar()
            if not alarma_activada and ahora - fallando_desde >= TIMEOUT_TOTAL_TRANSACCION_S:
                print("[decision] PC inalcanzable durante la espera: alarma local")
                activar_alarma_local()
                alarma_activada = True
            dormir(intervalo)
            continue

        fallando_desde = None
        if alarma_activada:
            print("[decision] La PC volvió a responder: se apaga la alarma local")
            if desactivar_alarma_local is not None:
                desactivar_alarma_local()
            alarma_activada = False
        estado = respuesta.get(CLAVE_ESTADO_DECISION_JSON)
        if estado == ESTADO_DECISION_RESUELTA:
            destino = respuesta.get(CLAVE_DESTINO_JSON)
            if not isinstance(destino, str) or not destino:
                raise RespuestaInesperada(f"resuelta sin destino: {respuesta!r}")
            return destino
        if estado == ESTADO_DECISION_NINGUNA:
            raise DecisionPerdida("la PC no tiene decisión en espera")
        if estado != ESTADO_DECISION_PENDIENTE:
            raise RespuestaInesperada(
                f"respuesta fuera de contrato: {respuesta.get(CLAVE_ERROR_JSON) or respuesta!r}")
        dormir(intervalo)
