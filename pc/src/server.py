"""
Servidor TCP de la PC: recibe el lote de imágenes de la Orange Pi, lo pasa al
contrato de reconocimiento (ocr_interface.py), y responde con la
clasificación (ver docs/arquitectura_comunicacion.md, secciones 4 y 8).

Si el lote termina en ERROR_REVISION_MANUAL, la PC deja una decisión en espera
para el regente; la Orange Pi la consulta por servidor_decision.py.
"""
import logging
import os
import signal
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_RESULTADO_JSON,
    FRAMING_LENGTH_BYTES,
    FRAMING_STRUCT_FORMAT,
    MAX_IMAGENES_POR_LOTE,
    RESULTADO_ERROR_REVISION_MANUAL,
    TAMANO_MAX_IMAGEN_BYTES,
    TCP_PUERTO_DECISION_DEFECTO,
    TCP_PUERTO_DEFECTO,
    TIMEOUT_INACTIVIDAD_RECEPCION_S,
)

from auditoria import Auditoria, ErrorRegistro, ruta_db_desde_entorno
from estado_panel import estado
from inventario import Inventario
from framing import ConexionSinDatos, FramingInvalido, enviar_json, recv_exacto
from mdns_service import detener_servicio_mdns, registrar_servicio_mdns
from ocr_interface import procesar_lote_ocr
from panel_web import PANEL_HOST, PANEL_PUERTO, iniciar_panel
from registro import configurar_logging
from servidor_decision import iniciar_servidor_decision
from usuarios import INACTIVIDAD_TURNO_S, GestorUsuarios

log = logging.getLogger("server")
log_alarma = logging.getLogger("alarma")


def _recibir_lote(sock):
    (cantidad,) = struct.unpack(
        FRAMING_STRUCT_FORMAT, recv_exacto(sock, FRAMING_LENGTH_BYTES, inicio_mensaje=True))
    if cantidad > MAX_IMAGENES_POR_LOTE:
        raise FramingInvalido(f"cantidad de imágenes {cantidad} > {MAX_IMAGENES_POR_LOTE}")
    imagenes = []
    for _ in range(cantidad):
        (tam,) = struct.unpack(FRAMING_STRUCT_FORMAT, recv_exacto(sock, FRAMING_LENGTH_BYTES))
        if tam > TAMANO_MAX_IMAGEN_BYTES:
            raise FramingInvalido(f"tamaño de imagen {tam} > {TAMANO_MAX_IMAGEN_BYTES}")
        imagenes.append(recv_exacto(sock, tam))
    return imagenes


def disparar_alarma_dashboard(motivo: str, lote_id: int | None = None) -> int:
    """Registra la alarma en el panel web (panel_web.py) para que el regente
    la vea (ver docs/arquitectura_comunicacion.md, sección 8). Devuelve su id."""
    alarma_id = estado.registrar_alarma(motivo, lote_id)
    log_alarma.warning("Disparada — motivo: %s", motivo)
    return alarma_id


def _manejar_cliente(conn, addr):
    log.info("Conexión de %s", addr)
    try:
        conn.settimeout(TIMEOUT_INACTIVIDAD_RECEPCION_S)
        try:
            imagenes = _recibir_lote(conn)
        except FramingInvalido as e:
            log.warning("Framing inválido de %s: %s", addr, e)
            estado.registrar_contacto(addr[0])
            alarma_id = disparar_alarma_dashboard("framing_invalido")
            # La decisión se abre ANTES de responder: la primera consulta de la
            # Orange Pi ya debe encontrarla "pendiente".
            estado.abrir_decision(alarma_id)
            enviar_json(conn, {CLAVE_RESULTADO_JSON: RESULTADO_ERROR_REVISION_MANUAL})
            return

        estado.registrar_contacto(addr[0])
        motivo_alarma = None
        if len(imagenes) == 0:
            log.warning("Lote vacío de %s — captura fallida o reintento tras perder la conexión", addr)
            resultado = RESULTADO_ERROR_REVISION_MANUAL
            motivo_alarma = "fallo_captura"
        else:
            log.info("Lote recibido de %s: %d imágenes", addr, len(imagenes))
            resultado = procesar_lote_ocr(imagenes)
            if resultado == RESULTADO_ERROR_REVISION_MANUAL:
                motivo_alarma = "sin_consenso_ocr"

        lote_id = estado.registrar_lote(addr[0], imagenes, resultado)
        if motivo_alarma:
            alarma_id = disparar_alarma_dashboard(motivo_alarma, lote_id)
            estado.abrir_decision(alarma_id)
        else:
            # Llegó un lote sin error: la Orange Pi ya siguió adelante.
            estado.cerrar_decision()

        enviar_json(conn, {CLAVE_RESULTADO_JSON: resultado})
        log.info("Respuesta enviada a %s: %s", addr, resultado)

    except ConexionSinDatos:
        log.info("%s abrió y cerró la conexión sin enviar datos (sonda de conectividad)", addr)
    except (ConnectionResetError, struct.error, OSError) as e:
        log.warning("Error con %s: %s", addr, e)
    finally:
        conn.close()


def _crear_asistente():
    """El asistente es opcional: si no está configurado, o falla al cargar (p. ej. el
    modelo no existe), el servidor y el panel siguen sin él."""
    log_asistente = logging.getLogger("asistente")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assistant"))
        from servicio import crear_servicio_desde_entorno
        servicio = crear_servicio_desde_entorno(estado)
    except Exception:
        log_asistente.exception("No se pudo iniciar el asistente; el panel sigue sin él")
        return None
    if servicio is None:
        log_asistente.info("Asistente desactivado (ver ASISTENTE_MODO / ASISTENTE_MODELO)")
    else:
        log_asistente.info("Asistente activo (modo %s)", servicio.modo)
    return servicio


def iniciar_servidor(puerto: int = TCP_PUERTO_DEFECTO):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(("0.0.0.0", puerto))
    servidor.listen(1)  # flujo estrictamente secuencial: un objeto a la vez
    log.info("Escuchando en el puerto %d", puerto)

    while True:
        conn, addr = servidor.accept()
        hilo = threading.Thread(target=_manejar_cliente, args=(conn, addr), daemon=True)
        hilo.start()


def _inactividad_turno_s() -> float:
    """Inactividad que cierra un turno: PC_TURNO_MIN (minutos) o el valor por defecto."""
    crudo = os.environ.get("PC_TURNO_MIN", "").strip()
    if not crudo:
        return INACTIVIDAD_TURNO_S
    try:
        minutos = float(crudo)
        if minutos <= 0:
            raise ValueError
        return minutos * 60
    except ValueError:
        log.warning("PC_TURNO_MIN=%r no es un número de minutos positivo; se usa el valor por defecto", crudo)
        return INACTIVIDAD_TURNO_S


def _al_terminar(signum, frame):
    # kill -TERM se trata como Ctrl+C: apagado ordenado (anuncia mDNS apagado y deja el
    # evento "sistema_detenido" en el registro).
    raise KeyboardInterrupt


if __name__ == "__main__":
    configurar_logging()
    try:
        auditoria = Auditoria(ruta_db_desde_entorno())
    except ErrorRegistro as e:
        # Un sistema que debe dejar constancia de lo que ocurre no arranca sin su registro.
        log.critical("%s. Para una demo sin persistencia usa PC_DB=:memory:", e)
        sys.exit(1)
    estado.conectar_auditoria(auditoria)
    estado.registrar_arranque(base=auditoria.ruta, puerto_lotes=TCP_PUERTO_DEFECTO,
                              puerto_consultas=TCP_PUERTO_DECISION_DEFECTO, puerto_panel=PANEL_PUERTO)
    log.info("Registro de eventos en %s", auditoria.ruta)
    try:
        usuarios = GestorUsuarios(auditoria, inactividad_s=_inactividad_turno_s())
    except ErrorRegistro as e:
        log.critical("No se pudo preparar la tabla de usuarios: %s", e)
        sys.exit(1)
    try:
        inventario = Inventario(auditoria)
    except ErrorRegistro as e:
        log.critical("No se pudo preparar el inventario: %s", e)
        sys.exit(1)
    estado.conectar_inventario(inventario)
    if not usuarios.hay_administrador():
        log.warning("NO HAY ADMINISTRADOR: nadie podrá iniciar turno ni operar el panel. "
                    "Créalo con:  bash pc/scripts/usuarios.sh crear-admin")
    signal.signal(signal.SIGTERM, _al_terminar)
    asistente = _crear_asistente()
    try:
        iniciar_panel(estado, asistente=asistente, auditoria=auditoria, usuarios=usuarios,
                      inventario=inventario)
        logging.getLogger("panel").info("Panel de control en http://%s:%d/", PANEL_HOST, PANEL_PUERTO)
    except OSError as e:
        # El panel no debe impedir que el transporte funcione.
        logging.getLogger("panel").error("No se pudo iniciar el panel (%s); el servidor sigue sin panel", e)
    try:
        iniciar_servidor_decision(estado, TCP_PUERTO_DECISION_DEFECTO)
        logging.getLogger("decision").info("Consultas de decisión en el puerto %d", TCP_PUERTO_DECISION_DEFECTO)
    except OSError as e:
        logging.getLogger("decision").error(
            "No se pudo abrir el puerto de decisión (%s); la Orange Pi no podrá "
            "consultar la decisión del regente", e)
    zc, info = registrar_servicio_mdns(puerto=TCP_PUERTO_DEFECTO)
    try:
        ip_anunciada = info.parsed_addresses()[0]
    except Exception:  # solo informativo para el registro
        ip_anunciada = None
    estado.registrar_evento("servicio_anunciado", nombre=info.name, ip=ip_anunciada,
                            puerto=TCP_PUERTO_DEFECTO)
    try:
        iniciar_servidor(puerto=TCP_PUERTO_DEFECTO)
    except KeyboardInterrupt:
        log.info("Apagando...")
    finally:
        detener_servicio_mdns(zc, info)
        estado.registrar_evento("sistema_detenido")
        auditoria.cerrar()
