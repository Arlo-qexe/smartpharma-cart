"""Pruebas de la orden de inicio del ciclo de auditoría (consulta S-06, opción 1;
informe 4.7): máquina de estados, consulta TCP, botón del panel. Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import logging
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import estado_panel  # noqa: E402
from estado_panel import (  # noqa: E402
    INICIO_ORDENADO,
    INICIO_YA_INICIADO,
    INICIO_YA_ORDENADO,
    EstadoPanel,
)
from mock_orangepi_client import consultar_decision, consultar_orden_ciclo  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from protocol_constants import VIGENCIA_ORDEN_CICLO_S  # noqa: E402
from servidor_decision import crear_servidor_decision  # noqa: E402

ESPERANDO = {"orden": "esperando"}
INICIAR = {"orden": "iniciar"}


@pytest.fixture
def reloj(monkeypatch):
    """Tiempo controlado para probar el vencimiento de la orden."""
    ahora = [1_000_000.0]
    monkeypatch.setattr(estado_panel, "time", types.SimpleNamespace(time=lambda: ahora[0]))
    return ahora


# ---------------------------------------------------------------------------
# Máquina de estados
# ---------------------------------------------------------------------------
def test_sin_orden_la_orange_pi_espera():
    estado = EstadoPanel()
    assert estado.consultar_orden_ciclo() == ESPERANDO
    assert estado.snapshot()["orden_ciclo"]["estado"] == "sin_orden"


def test_orden_se_entrega_una_vez_y_queda_iniciada():
    estado = EstadoPanel()
    assert estado.ordenar_inicio() == INICIO_ORDENADO
    assert estado.consultar_orden_ciclo() == INICIAR
    estado.confirmar_entrega_orden()
    snap = estado.snapshot()["orden_ciclo"]
    assert snap["estado"] == "iniciada" and snap["ts_entregada"] is not None


def test_si_la_orange_pi_vuelve_a_preguntar_tras_iniciar_se_entiende_que_reinicio():
    """Ningún reinicio arranca el ciclo sin una decisión humana: se necesita otra orden."""
    estado = EstadoPanel()
    estado.ordenar_inicio()
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    assert estado.consultar_orden_ciclo() == ESPERANDO          # preguntó otra vez: reinició
    assert estado.snapshot()["orden_ciclo"]["estado"] == "sin_orden"
    assert estado.consultar_orden_ciclo() == ESPERANDO          # y sigue esperando
    assert estado.ordenar_inicio() == INICIO_ORDENADO           # el regente puede dar otra


def test_ordenar_dos_veces_es_idempotente_y_no_se_puede_ordenar_si_ya_inicio():
    estado = EstadoPanel()
    assert estado.ordenar_inicio() == INICIO_ORDENADO
    assert estado.ordenar_inicio() == INICIO_YA_ORDENADO
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    assert estado.ordenar_inicio() == INICIO_YA_INICIADO


def test_si_el_envio_falla_la_orden_sigue_vigente():
    """La entrega se confirma aparte, tras enviar: sin confirmar, la siguiente
    consulta vuelve a recibir `iniciar`."""
    estado = EstadoPanel()
    estado.ordenar_inicio()
    assert estado.consultar_orden_ciclo() == INICIAR            # (el envío falló: no se confirma)
    assert estado.consultar_orden_ciclo() == INICIAR


def test_una_orden_que_nadie_recoge_vence(reloj):
    estado = EstadoPanel()
    estado.ordenar_inicio()
    reloj[0] += VIGENCIA_ORDEN_CICLO_S - 1
    assert estado.snapshot()["orden_ciclo"]["restante_s"] == 1
    reloj[0] += 1
    assert estado.snapshot()["orden_ciclo"]["estado"] == "sin_orden"
    assert estado.consultar_orden_ciclo() == ESPERANDO          # no arranca un ciclo olvidado
    assert estado.ordenar_inicio() == INICIO_ORDENADO


def test_el_snapshot_informa_la_ultima_consulta(reloj):
    estado = EstadoPanel()
    assert estado.snapshot()["orden_ciclo"]["ts_ultima_consulta"] is None
    estado.consultar_orden_ciclo()
    assert estado.snapshot()["orden_ciclo"]["ts_ultima_consulta"] == reloj[0]


# ---------------------------------------------------------------------------
# Consulta TCP (servidor_decision.py)
# ---------------------------------------------------------------------------
@pytest.fixture
def canal():
    estado = EstadoPanel()
    servidor = crear_servidor_decision(estado, 0, "127.0.0.1")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield estado, servidor.server_address[1]
    servidor.shutdown()
    servidor.server_close()


def esperar_log(caplog, texto, cantidad=1, intentos=60):
    for _ in range(intentos):
        if caplog.text.count(texto) >= cantidad:
            break
        time.sleep(0.05)
    return caplog.text


def test_consulta_tcp_esperando_y_luego_iniciar_una_sola_vez(canal):
    estado, puerto = canal
    assert consultar_orden_ciclo(puerto=puerto) == ESPERANDO
    estado.ordenar_inicio()
    assert consultar_orden_ciclo(puerto=puerto) == INICIAR
    for _ in range(40):                              # la entrega se confirma tras enviar
        if estado.snapshot()["orden_ciclo"]["estado"] == "iniciada":
            break
        time.sleep(0.05)
    assert estado.snapshot()["orden_ciclo"]["estado"] == "iniciada"
    assert consultar_orden_ciclo(puerto=puerto) == ESPERANDO     # un segundo `iniciar` no existe


def test_la_orden_y_la_decision_conviven_en_el_mismo_puerto(canal):
    estado, puerto = canal
    estado.ordenar_inicio()
    alarma = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(alarma)
    assert consultar_decision(puerto=puerto) == {"estado": "pendiente"}
    assert consultar_orden_ciclo(puerto=puerto) == INICIAR
    assert consultar_decision(puerto=puerto) == {"estado": "pendiente"}   # la decisión no se alteró


def test_consulta_desconocida_sigue_siendo_invalida(canal):
    import socket
    import struct
    _, puerto = canal
    cuerpo = json.dumps({"consulta": "orden_inventada"}).encode()
    with socket.create_connection(("127.0.0.1", puerto), timeout=3) as s:
        s.sendall(struct.pack("!I", len(cuerpo)) + cuerpo)
        (n,) = struct.unpack("!I", s.recv(4))
        assert json.loads(s.recv(n)) == {"error": "consulta_invalida"}


def test_registra_solo_los_cambios_de_la_orden(canal, caplog):
    caplog.set_level(logging.INFO)
    estado, puerto = canal
    for _ in range(3):                               # la Orange Pi consulta cada 2 s
        consultar_orden_ciclo(puerto=puerto)
    salida = esperar_log(caplog, "recibió la orden del ciclo: esperando")
    assert salida.count("recibió la orden del ciclo: esperando") == 1

    estado.ordenar_inicio()
    consultar_orden_ciclo(puerto=puerto)
    salida = esperar_log(caplog, "recibió la orden del ciclo: iniciar")
    assert salida.count("recibió la orden del ciclo: iniciar") == 1


# ---------------------------------------------------------------------------
# Botón del panel (POST /api/ciclo/iniciar)
# ---------------------------------------------------------------------------
@pytest.fixture
def panel():
    estado = EstadoPanel()
    servidor = iniciar_panel(estado, "127.0.0.1", 0)
    yield estado, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def post(base, ruta, headers=None):
    req = urllib.request.Request(base + ruta, data=b"", method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_boton_ordena_y_el_panel_lo_refleja(panel):
    estado, base = panel
    assert post(base, "/api/ciclo/iniciar") == (200, {"ok": True, "resultado": "ordenada"})
    assert post(base, "/api/ciclo/iniciar") == (200, {"ok": True, "resultado": "ya_ordenada"})
    datos = json.loads(urllib.request.urlopen(base + "/api/estado").read())
    assert datos["orden_ciclo"]["estado"] == "ordenada" and datos["orden_ciclo"]["restante_s"] > 0
    assert estado.consultar_orden_ciclo() == INICIAR


def test_boton_tras_iniciar_responde_409(panel):
    estado, base = panel
    post(base, "/api/ciclo/iniciar")
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    assert post(base, "/api/ciclo/iniciar") == (409, {"error": "ya_iniciada"})


def test_boton_con_origen_ajeno_se_rechaza(panel):
    estado, base = panel
    assert post(base, "/api/ciclo/iniciar", {"Origin": "http://malicioso.example"})[0] == 403
    assert estado.consultar_orden_ciclo() == ESPERANDO          # no se ordenó nada


def test_la_pagina_trae_el_boton_y_la_configuracion_muestra_la_vigencia(panel):
    _, base = panel
    html = urllib.request.urlopen(base + "/").read().decode()
    js = urllib.request.urlopen(base + "/static/panel.js").read().decode()
    assert 'id="boton-recorrido"' in html and 'id="recorrido-estado"' in html
    assert "/api/ciclo/iniciar" in js
    config = json.loads(urllib.request.urlopen(base + "/api/estado").read())["config"]
    assert any(c["nombre"] == "Vigencia de la orden de inicio del ciclo" for c in config)
