"""Pruebas del canal de decisión del regente (opción A de
docs/propuesta_canal_regente.md): servidor_decision.py + estado_panel.py,
y el flujo completo con server.py. Correr desde pc/:  python3 -m pytest tests/
"""
import json
import logging
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import server  # noqa: E402
from estado_panel import EstadoPanel  # noqa: E402
from mock_orangepi_client import (  # noqa: E402
    consultar_decision,
    enviar_lote_de_prueba,
    enviar_lote_vacio,
)
from protocol_constants import TAMANO_MAX_MENSAJE_JSON_BYTES  # noqa: E402
from servidor_decision import crear_servidor_decision  # noqa: E402


@pytest.fixture
def canal():
    estado = EstadoPanel()
    servidor = crear_servidor_decision(estado, 0, "127.0.0.1")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield estado, servidor.server_address[1]
    servidor.shutdown()
    servidor.server_close()


def _consulta_cruda(puerto, payload: bytes, longitud=None):
    with socket.create_connection(("127.0.0.1", puerto), timeout=3) as s:
        n = len(payload) if longitud is None else longitud
        s.sendall(struct.pack("!I", n) + payload)
        (largo,) = struct.unpack("!I", s.recv(4))
        return json.loads(s.recv(largo))


def test_sin_decision_responde_ninguna(canal):
    _, puerto = canal
    assert consultar_decision(puerto=puerto) == {"estado": "ninguna"}


def test_flujo_pendiente_y_resuelta(canal):
    estado, puerto = canal
    alarma = estado.registrar_alarma("sin_consenso_ocr")
    estado.abrir_decision(alarma)
    assert consultar_decision(puerto=puerto) == {"estado": "pendiente"}

    assert estado.resolver_alarma(alarma, "TIPO_C") == "resuelta"
    esperado = {"estado": "resuelta", "destino": "TIPO_C"}
    assert consultar_decision(puerto=puerto) == esperado
    # No se consume al leerla: si la respuesta se pierde, la Orange Pi repite.
    assert consultar_decision(puerto=puerto) == esperado


def test_descarte_es_un_destino_valido(canal):
    estado, puerto = canal
    alarma = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(alarma)
    assert estado.resolver_alarma(alarma, "DESCARTE") == "resuelta"
    assert consultar_decision(puerto=puerto)["destino"] == "DESCARTE"


def test_destino_invalido_no_resuelve(canal):
    estado, puerto = canal
    alarma = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(alarma)
    for malo in (None, "", "tipo_a", "TIPO A", "X" * 33, "TIPO-A"):
        assert estado.resolver_alarma(alarma, malo) == "destino_invalido"
    assert consultar_decision(puerto=puerto) == {"estado": "pendiente"}


def test_nuevo_lote_reemplaza_la_decision_anterior(canal):
    estado, puerto = canal
    vieja = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(vieja)
    nueva = estado.registrar_alarma("sin_consenso_ocr")
    estado.abrir_decision(nueva)
    # La alarma antigua ya no la espera la Orange Pi: solo se cierra.
    assert estado.resolver_alarma(vieja, "TIPO_A") == "cerrada"
    assert consultar_decision(puerto=puerto) == {"estado": "pendiente"}
    # Un lote sin error cierra la decisión.
    estado.cerrar_decision()
    assert consultar_decision(puerto=puerto) == {"estado": "ninguna"}


def test_consultas_invalidas(canal):
    _, puerto = canal
    err = {"error": "consulta_invalida"}
    assert _consulta_cruda(puerto, b'{"consulta": "otra_cosa"}') == err
    assert _consulta_cruda(puerto, b"no es json") == err
    assert _consulta_cruda(puerto, b"[1, 2]") == err
    # Longitud declarada mayor al tope: se rechaza sin leer el cuerpo.
    assert _consulta_cruda(puerto, b"", TAMANO_MAX_MENSAJE_JSON_BYTES + 1) == err


# ---- flujo completo con server.py: lote con error -> decisión -> regente -> Orange Pi
@pytest.fixture(scope="module")
def puertos():
    def libre():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    p_lotes = libre()
    threading.Thread(target=server.iniciar_servidor, args=(p_lotes,), daemon=True).start()
    dec = crear_servidor_decision(server.estado, 0, "127.0.0.1")
    threading.Thread(target=dec.serve_forever, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", p_lotes), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield p_lotes, dec.server_address[1]
    dec.shutdown()
    dec.server_close()


def test_lote_vacio_abre_decision_y_el_regente_la_resuelve(puertos):
    p_lotes, p_dec = puertos
    enviar_lote_vacio(puerto=p_lotes)

    # La primera consulta de la Orange Pi ya encuentra la decisión pendiente.
    assert consultar_decision(puerto=p_dec) == {"estado": "pendiente"}
    alarma = server.estado.snapshot()["alarmas"][0]
    assert alarma["espera_decision"] is True

    assert server.estado.resolver_alarma(alarma["id"], "TIPO_A") == "resuelta"
    assert consultar_decision(puerto=p_dec) == {"estado": "resuelta", "destino": "TIPO_A"}

    # El siguiente lote (sin error) cierra la decisión.
    enviar_lote_de_prueba(puerto=p_lotes, tamano_bytes=100)
    assert consultar_decision(puerto=p_dec) == {"estado": "ninguna"}


def test_framing_invalido_tambien_abre_decision(puertos):
    p_lotes, p_dec = puertos
    with socket.create_connection(("127.0.0.1", p_lotes), timeout=3) as s:
        s.sendall(struct.pack("!I", 99))  # más imágenes que MAX_IMAGENES_POR_LOTE
        (largo,) = struct.unpack("!I", s.recv(4))
        assert json.loads(s.recv(largo))["clasificacion"] == "ERROR_REVISION_MANUAL"
    assert consultar_decision(puerto=p_dec) == {"estado": "pendiente"}


# ---- PC-18: registro de las consultas de decisión (logging)
def _esperar_log(caplog, texto, cantidad=1, intentos=60):
    """Los servidores registran desde su propio hilo, después de responder."""
    for _ in range(intentos):
        if caplog.text.count(texto) >= cantidad:
            break
        time.sleep(0.05)
    return caplog.text


def test_registra_solo_los_cambios_de_estado_entregado(canal, caplog):
    caplog.set_level(logging.INFO)
    estado, puerto = canal
    alarma = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(alarma)

    for _ in range(3):  # la Orange Pi consulta cada 2 s: no debe haber ruido
        consultar_decision(puerto=puerto)
    salida = _esperar_log(caplog, "recibió: pendiente")
    assert salida.count("recibió: pendiente") == 1

    estado.resolver_alarma(alarma, "DESCARTE")
    consultar_decision(puerto=puerto)
    consultar_decision(puerto=puerto)
    salida = _esperar_log(caplog, "recibió: resuelta (destino DESCARTE)")
    assert salida.count("recibió: resuelta (destino DESCARTE)") == 1
    assert salida.count("recibió: pendiente") == 1


def test_conexion_sin_datos_no_se_reporta_como_error(canal, puertos, caplog):
    caplog.set_level(logging.INFO)
    _, p_dec = canal
    p_lotes, _ = puertos
    for puerto in (p_dec, p_lotes):
        socket.create_connection(("127.0.0.1", puerto), timeout=3).close()
    salida = _esperar_log(caplog, "sin enviar datos", cantidad=2)
    assert salida.count("sin enviar datos") == 2
    sondas = [r for r in caplog.records if "sin enviar datos" in r.getMessage()]
    assert {r.name for r in sondas} == {"decision", "server"}
    assert all(r.levelno == logging.INFO for r in sondas)  # no WARNING ni ERROR


def test_alarma_se_registra_como_warning(puertos, caplog):
    caplog.set_level(logging.INFO)
    p_lotes, _ = puertos
    enviar_lote_vacio(puerto=p_lotes)
    _esperar_log(caplog, "Disparada")
    alarmas = [r for r in caplog.records if r.name == "alarma"]
    assert alarmas and alarmas[-1].levelno == logging.WARNING
    assert "fallo_captura" in alarmas[-1].getMessage()
