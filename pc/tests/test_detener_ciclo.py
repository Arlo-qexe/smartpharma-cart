"""Pruebas de continuar/detener el ciclo entre objetos y de la cantidad de cajas
(informe 4.8): máquina de estados, consulta TCP, botones del panel e indicador de la
Orange Pi. Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import sys
import threading
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
    DETENCION_ORDENADA,
    DETENCION_YA_ORDENADA,
    INICIO_CANTIDAD_INVALIDA,
    INICIO_ORDENADO,
    ORDEN_CANCELADA,
    SIN_CICLO,
    VENTANA_CONEXION_ORANGEPI_S,
    EstadoPanel,
)
from mock_orangepi_client import consultar_estado_ciclo, consultar_orden_ciclo  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from servidor_decision import crear_servidor_decision  # noqa: E402

CONTINUAR = {"ciclo": "continuar"}
DETENER = {"ciclo": "detener"}


def iniciar_ciclo(estado, cantidad=None):
    """El regente inicia y la Orange Pi recoge la orden."""
    assert estado.ordenar_inicio(cantidad) == INICIO_ORDENADO
    assert estado.consultar_orden_ciclo() == {"orden": "iniciar"}
    estado.confirmar_entrega_orden()


@pytest.fixture
def reloj(monkeypatch):
    ahora = [1_000_000.0]
    monkeypatch.setattr(estado_panel, "time", types.SimpleNamespace(time=lambda: ahora[0]))
    return ahora


# ---------------------------------------------------------------------------
# Máquina de estados
# ---------------------------------------------------------------------------
def test_sin_ciclo_la_pc_responde_detener():
    """Una PC recién reiniciada no sabe de ningún ciclo: nada sigue sin decisión humana."""
    assert EstadoPanel().consultar_estado_ciclo() == DETENER


def test_sin_cantidad_continua_hasta_que_el_regente_detiene():
    estado = EstadoPanel()
    iniciar_ciclo(estado)
    assert [estado.consultar_estado_ciclo() for _ in range(25)] == [CONTINUAR] * 25
    assert estado.snapshot()["ciclo"]["iniciadas"] == 25
    assert estado.ordenar_detencion() == DETENCION_ORDENADA
    assert estado.snapshot()["ciclo"]["estado"] == "deteniendo"
    assert estado.consultar_estado_ciclo() == DETENER
    snap = estado.snapshot()
    assert snap["ciclo"]["estado"] == "inactivo"
    assert snap["ciclo"]["motivo_fin"] == "detenido_por_regente"
    assert snap["orden_ciclo"]["estado"] == "sin_orden"     # el botón "Iniciar" vuelve a estar libre


def test_con_cantidad_se_detiene_al_alcanzarla():
    estado = EstadoPanel()
    iniciar_ciclo(estado, cantidad=3)
    assert [estado.consultar_estado_ciclo() for _ in range(3)] == [CONTINUAR] * 3
    assert estado.consultar_estado_ciclo() == DETENER          # la cuarta caja no entra
    snap = estado.snapshot()["ciclo"]
    assert snap["motivo_fin"] == "cantidad_alcanzada" and snap["iniciadas"] == 3
    assert estado.consultar_estado_ciclo() == DETENER          # y sigue detenido


def test_detener_es_idempotente_y_sin_ciclo_no_hay_nada_que_detener():
    estado = EstadoPanel()
    assert estado.ordenar_detencion() == SIN_CICLO
    iniciar_ciclo(estado)
    assert estado.ordenar_detencion() == DETENCION_ORDENADA
    assert estado.ordenar_detencion() == DETENCION_YA_ORDENADA


def test_tras_detener_hace_falta_una_orden_nueva_y_el_conteo_empieza_de_cero():
    estado = EstadoPanel()
    iniciar_ciclo(estado, cantidad=2)
    estado.consultar_estado_ciclo()
    estado.ordenar_detencion()
    assert estado.consultar_estado_ciclo() == DETENER
    # La Orange Pi vuelve a esperar la orden: sin decisión humana no arranca.
    assert estado.consultar_orden_ciclo() == {"orden": "esperando"}
    assert estado.consultar_estado_ciclo() == DETENER
    iniciar_ciclo(estado, cantidad=5)
    snap = estado.snapshot()["ciclo"]
    assert snap["estado"] == "activo" and snap["cantidad"] == 5 and snap["iniciadas"] == 0
    assert snap["motivo_fin"] is None


def test_cancelar_la_orden_antes_de_que_la_orange_pi_la_recoja():
    estado = EstadoPanel()
    estado.ordenar_inicio(4)
    assert estado.ordenar_detencion() == ORDEN_CANCELADA
    assert estado.consultar_orden_ciclo() == {"orden": "esperando"}
    assert estado.consultar_estado_ciclo() == DETENER


def test_si_la_orange_pi_reinicia_en_pleno_ciclo_la_pc_lo_nota():
    estado = EstadoPanel()
    iniciar_ciclo(estado)
    estado.consultar_estado_ciclo()
    assert estado.consultar_orden_ciclo() == {"orden": "esperando"}   # volvió a pedir orden
    assert estado.snapshot()["ciclo"]["estado"] == "inactivo"
    assert estado.consultar_estado_ciclo() == DETENER


@pytest.mark.parametrize("cantidad", [0, -1, 10_000, 2.5, "3", True])
def test_cantidad_invalida_se_rechaza_sin_ordenar(cantidad):
    estado = EstadoPanel()
    assert estado.ordenar_inicio(cantidad) == INICIO_CANTIDAD_INVALIDA
    assert estado.snapshot()["orden_ciclo"]["estado"] == "sin_orden"


# ---------------------------------------------------------------------------
# Indicador de conexión con la Orange Pi
# ---------------------------------------------------------------------------
def test_indicador_de_la_orange_pi(reloj):
    estado = EstadoPanel()
    assert estado.snapshot()["orangepi"] == {"ip": None, "ts_ultimo_contacto": None,
                                              "conectada": False}
    estado.registrar_contacto("192.168.20.72")
    op = estado.snapshot()["orangepi"]
    assert op["ip"] == "192.168.20.72" and op["conectada"] is True
    reloj[0] += VENTANA_CONEXION_ORANGEPI_S - 1
    assert estado.snapshot()["orangepi"]["conectada"] is True
    reloj[0] += 2
    assert estado.snapshot()["orangepi"]["conectada"] is False
    estado.registrar_contacto("192.168.20.72")
    assert estado.snapshot()["orangepi"]["conectada"] is True


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


def test_consulta_tcp_continuar_y_detener_por_cantidad(canal):
    estado, puerto = canal
    assert consultar_estado_ciclo(puerto=puerto) == DETENER           # sin ciclo
    estado.ordenar_inicio(2)
    assert consultar_orden_ciclo(puerto=puerto) == {"orden": "iniciar"}
    assert [consultar_estado_ciclo(puerto=puerto) for _ in range(3)] == [CONTINUAR, CONTINUAR, DETENER]


def test_consulta_tcp_detener_pedido_por_el_regente(canal):
    estado, puerto = canal
    estado.ordenar_inicio()
    consultar_orden_ciclo(puerto=puerto)
    assert consultar_estado_ciclo(puerto=puerto) == CONTINUAR
    estado.ordenar_detencion()
    assert consultar_estado_ciclo(puerto=puerto) == DETENER


def test_las_consultas_validas_cuentan_como_contacto_de_la_orange_pi(canal):
    estado, puerto = canal
    assert estado.snapshot()["orangepi"]["ip"] is None
    consultar_estado_ciclo(puerto=puerto)
    assert estado.snapshot()["orangepi"]["ip"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# Botones del panel
# ---------------------------------------------------------------------------
@pytest.fixture
def panel():
    estado = EstadoPanel()
    servidor = iniciar_panel(estado, "127.0.0.1", 0)
    yield estado, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def post(base, ruta, cuerpo=None, headers=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else b""
    req = urllib.request.Request(base + ruta, data=datos, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_iniciar_con_cantidad_y_detener_desde_el_panel(panel):
    estado, base = panel
    assert post(base, "/api/ciclo/iniciar", {"cantidad": 3}) == (200, {"ok": True, "resultado": "ordenada"})
    assert estado.consultar_orden_ciclo() == {"orden": "iniciar"}
    estado.confirmar_entrega_orden()
    assert estado.consultar_estado_ciclo() == CONTINUAR
    datos = json.loads(urllib.request.urlopen(base + "/api/estado").read())
    assert datos["ciclo"]["estado"] == "activo" and datos["ciclo"]["cantidad"] == 3
    assert datos["ciclo"]["iniciadas"] == 1
    assert post(base, "/api/ciclo/detener") == (200, {"ok": True, "resultado": "ordenada"})
    assert estado.consultar_estado_ciclo() == DETENER


def test_iniciar_sin_cuerpo_no_limita_las_cajas(panel):
    estado, base = panel
    assert post(base, "/api/ciclo/iniciar")[0] == 200
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    assert estado.snapshot()["ciclo"]["cantidad"] is None


@pytest.mark.parametrize("cantidad", [0, -4, 10_000, "tres", 1.5])
def test_cantidad_invalida_responde_400(panel, cantidad):
    estado, base = panel
    assert post(base, "/api/ciclo/iniciar", {"cantidad": cantidad}) == (400, {"error": "cantidad_invalida"})
    assert estado.snapshot()["orden_ciclo"]["estado"] == "sin_orden"


def test_detener_sin_ciclo_responde_409(panel):
    _, base = panel
    assert post(base, "/api/ciclo/detener") == (409, {"error": "sin_ciclo"})


def test_detener_con_origen_ajeno_se_rechaza(panel):
    _, base = panel
    assert post(base, "/api/ciclo/detener", headers={"Origin": "http://otro.sitio"})[0] == 403


def test_la_pagina_trae_los_controles_nuevos(panel):
    _, base = panel
    html = urllib.request.urlopen(base + "/").read().decode("utf-8")
    for id_ in ("boton-detener", "cantidad-cajas", "orangepi-texto"):
        assert f'id="{id_}"' in html
