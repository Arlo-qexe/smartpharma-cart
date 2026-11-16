"""Pruebas del desplazamiento del carrito a un punto (informe 4.10): máquina de estados, consultas TCP,
botón «Llevar a punto» del panel y registro de eventos. Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import sys
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
from auditoria import Auditoria  # noqa: E402
from estado_panel import (  # noqa: E402
    DESPL_CICLO_EN_CURSO,
    DESPL_ORDEN_OK,
    DESPL_PUNTO_INVALIDO,
    DESPL_YA_ORDENADO,
    EstadoPanel,
)
from mock_orangepi_client import (  # noqa: E402
    consultar_orden_ciclo,
    consultar_orden_desplazamiento,
    enviar_resultado_desplazamiento,
)
from panel_web import iniciar_panel  # noqa: E402
from protocol_constants import (  # noqa: E402
    PUNTOS_DESPLAZAMIENTO,
    TIMEOUT_DESPLAZAMIENTO_S,
    VIGENCIA_ORDEN_DESPLAZAMIENTO_S,
)
from servidor_decision import crear_servidor_decision  # noqa: E402

NINGUNO = {"desplazamiento": "ninguno"}
P = PUNTOS_DESPLAZAMIENTO[0]
IR = {"desplazamiento": "ir", "punto": P}


@pytest.fixture
def reloj(monkeypatch):
    ahora = [1_000_000.0]
    monkeypatch.setattr(estado_panel, "time", types.SimpleNamespace(time=lambda: ahora[0]))
    return ahora


def en_camino(estado, punto=P):
    assert estado.ordenar_desplazamiento(punto) == DESPL_ORDEN_OK
    assert estado.consultar_orden_desplazamiento() == {"desplazamiento": "ir", "punto": punto}
    estado.confirmar_entrega_desplazamiento()



# ---------------------------------------------------------------------------
# Máquina de estados
# ---------------------------------------------------------------------------
def test_sin_orden_no_hay_nada_que_hacer():
    estado = EstadoPanel()
    assert estado.consultar_orden_desplazamiento() == NINGUNO
    assert estado.snapshot()["desplazamiento"]["estado"] == "sin_orden"
    assert estado.snapshot()["desplazamiento"]["puntos"] == list(PUNTOS_DESPLAZAMIENTO)


def test_la_orden_se_entrega_una_vez_y_queda_en_camino():
    estado = EstadoPanel()
    assert estado.ordenar_desplazamiento(P, responsable="Ana") == DESPL_ORDEN_OK
    assert estado.snapshot()["desplazamiento"]["estado"] == "ordenado"
    assert estado.consultar_orden_desplazamiento() == IR
    assert estado.consultar_orden_desplazamiento() == IR          # sin confirmar la entrega, sigue vigente
    estado.confirmar_entrega_desplazamiento()
    snap = estado.snapshot()["desplazamiento"]
    assert snap["estado"] == "en_camino" and snap["punto"] == P and snap["ordenado_por"] == "Ana"


def test_llegada_cierra_el_desplazamiento():
    estado = EstadoPanel()
    en_camino(estado)
    assert estado.registrar_resultado_desplazamiento(P, "llegada") is True
    snap = estado.snapshot()["desplazamiento"]
    assert snap["estado"] == "llegada" and snap["ts_fin"] is not None
    assert estado.consultar_orden_desplazamiento() == NINGUNO
    assert estado.ordenar_desplazamiento(P) == DESPL_ORDEN_OK     # se puede pedir otro


def test_fallo_se_muestra_y_no_se_reintenta_solo():
    estado = EstadoPanel()
    en_camino(estado)
    assert estado.registrar_resultado_desplazamiento(P, "fallo") is True
    assert estado.snapshot()["desplazamiento"]["estado"] == "fallo"
    assert estado.consultar_orden_desplazamiento() == NINGUNO


def test_si_la_orange_pi_pregunta_en_camino_se_da_por_fallido():
    """Mientras se desplaza no consulta: si lo hace, reinició."""
    estado = EstadoPanel()
    en_camino(estado)
    assert estado.consultar_orden_desplazamiento() == NINGUNO
    assert estado.snapshot()["desplazamiento"]["estado"] == "fallo"


@pytest.mark.parametrize("punto", [0, 99, -1, "1", None, 1.0, True])
def test_punto_invalido_se_rechaza(punto):
    estado = EstadoPanel()
    assert estado.ordenar_desplazamiento(punto) == DESPL_PUNTO_INVALIDO
    assert estado.consultar_orden_desplazamiento() == NINGUNO


def test_ordenar_dos_veces_es_un_conflicto():
    estado = EstadoPanel()
    estado.ordenar_desplazamiento(P)
    assert estado.ordenar_desplazamiento(P) == DESPL_YA_ORDENADO
    estado.consultar_orden_desplazamiento()
    estado.confirmar_entrega_desplazamiento()
    assert estado.ordenar_desplazamiento(P) == DESPL_YA_ORDENADO   # también en camino


def test_no_se_mueve_el_carrito_con_el_recorrido_en_marcha_o_pendiente():
    estado = EstadoPanel()
    estado.ordenar_inicio()                                        # orden de inicio sin recoger
    assert estado.ordenar_desplazamiento(P) == DESPL_CICLO_EN_CURSO
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()                               # ciclo activo
    assert estado.ordenar_desplazamiento(P) == DESPL_CICLO_EN_CURSO
    estado.ordenar_detencion()                                     # deteniendo
    assert estado.ordenar_desplazamiento(P) == DESPL_CICLO_EN_CURSO
    estado.consultar_estado_ciclo()                                # la Orange Pi recoge `detener`
    assert estado.ordenar_desplazamiento(P) == DESPL_ORDEN_OK


@pytest.mark.parametrize("punto,resultado", [(99, "llegada"), (P, "tal_vez"), ("1", "llegada"), (P, None), (None, None)])
def test_resultado_mal_formado_se_rechaza(punto, resultado):
    estado = EstadoPanel()
    en_camino(estado)
    assert estado.registrar_resultado_desplazamiento(punto, resultado) is False
    assert estado.snapshot()["desplazamiento"]["estado"] == "en_camino"   # no se altera


def test_resultado_inesperado_se_acepta_sin_tocar_el_estado():
    """Un resultado sin orden en curso (p. ej. la PC reinició) no es un error del mensaje."""
    estado = EstadoPanel()
    assert estado.registrar_resultado_desplazamiento(P, "llegada") is True
    assert estado.snapshot()["desplazamiento"]["estado"] == "sin_orden"
    en_camino(estado, PUNTOS_DESPLAZAMIENTO[0])
    otro = PUNTOS_DESPLAZAMIENTO[-1]
    assert otro != P
    assert estado.registrar_resultado_desplazamiento(otro, "llegada") is True   # otro punto: no es el pedido
    assert estado.snapshot()["desplazamiento"]["estado"] == "en_camino"


def test_orden_que_nadie_recoge_vence(reloj):
    estado = EstadoPanel()
    estado.ordenar_desplazamiento(P)
    reloj[0] += VIGENCIA_ORDEN_DESPLAZAMIENTO_S - 1
    assert estado.snapshot()["desplazamiento"]["restante_s"] == 1
    reloj[0] += 2
    assert estado.consultar_orden_desplazamiento() == NINGUNO
    assert estado.snapshot()["desplazamiento"]["estado"] == "sin_orden"


def test_desplazamiento_sin_resultado_se_da_por_perdido(reloj):
    """Si la Orange Pi se apaga en camino, el botón no queda bloqueado para siempre."""
    estado = EstadoPanel()
    en_camino(estado)
    reloj[0] += TIMEOUT_DESPLAZAMIENTO_S + 1
    assert estado.snapshot()["desplazamiento"]["estado"] == "en_camino"   # aún hay margen
    reloj[0] += estado_panel.MARGEN_RESULTADO_DESPLAZAMIENTO_S
    assert estado.snapshot()["desplazamiento"]["estado"] == "fallo"
    assert estado.ordenar_desplazamiento(P) == DESPL_ORDEN_OK


# ---------------------------------------------------------------------------
# Registro de eventos
# ---------------------------------------------------------------------------
def test_el_registro_anota_quien_ordeno_y_como_termino():
    estado = EstadoPanel()
    aud = Auditoria(":memory:")
    estado.conectar_auditoria(aud)
    estado.ordenar_desplazamiento(P, responsable="Ana")
    estado.consultar_orden_desplazamiento()
    estado.confirmar_entrega_desplazamiento()
    estado.registrar_resultado_desplazamiento(P, "llegada")
    por_tipo = {e["tipo"]: e for e in aud.listar(limite=50)["eventos"]}
    assert por_tipo["desplazamiento_ordenado"]["responsable"] == "Ana"
    assert por_tipo["desplazamiento_ordenado"]["detalle"]["punto"] == P
    assert por_tipo["desplazamiento_iniciado"]["responsable"] == "Ana"
    assert por_tipo["desplazamiento_llegada"]["responsable"] == "Ana"
    assert por_tipo["desplazamiento_llegada"]["detalle"]["punto"] == P
    assert aud.verificar()["ok"] is True


# ---------------------------------------------------------------------------
# Consultas TCP (puerto 5001)
# ---------------------------------------------------------------------------
@pytest.fixture
def canal():
    estado = EstadoPanel()
    servidor = crear_servidor_decision(estado, 0, "127.0.0.1")
    import threading
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield estado, servidor.server_address[1]
    servidor.shutdown()
    servidor.server_close()


def esperar_estado(estado, nombre, intentos=60):
    for _ in range(intentos):
        if estado.snapshot()["desplazamiento"]["estado"] == nombre:
            return
        time.sleep(0.05)
    assert estado.snapshot()["desplazamiento"]["estado"] == nombre


def test_flujo_completo_por_tcp(canal):
    estado, puerto = canal
    assert consultar_orden_desplazamiento(puerto=puerto) == NINGUNO
    estado.ordenar_desplazamiento(P)
    assert consultar_orden_desplazamiento(puerto=puerto) == IR
    esperar_estado(estado, "en_camino")                            # la entrega se confirma tras enviar
    assert enviar_resultado_desplazamiento(P, "llegada", puerto=puerto) == {"ok": True}
    assert estado.snapshot()["desplazamiento"]["estado"] == "llegada"


def test_resultado_invalido_por_tcp_responde_consulta_invalida(canal):
    estado, puerto = canal
    en_camino(estado)
    assert enviar_resultado_desplazamiento(P, "tal_vez", puerto=puerto) == {"error": "consulta_invalida"}
    assert enviar_resultado_desplazamiento(99, "llegada", puerto=puerto) == {"error": "consulta_invalida"}
    assert estado.snapshot()["desplazamiento"]["estado"] == "en_camino"


def test_las_consultas_cuentan_como_contacto_de_la_orange_pi(canal):
    estado, puerto = canal
    assert estado.snapshot()["orangepi"]["ts_ultimo_contacto"] is None
    consultar_orden_desplazamiento(puerto=puerto)
    assert estado.snapshot()["orangepi"]["conectada"] is True


def test_convive_con_la_orden_de_inicio_en_el_mismo_puerto(canal):
    estado, puerto = canal
    estado.ordenar_desplazamiento(P)
    assert consultar_orden_ciclo(puerto=puerto) == {"orden": "esperando"}   # no se mezclan
    assert consultar_orden_desplazamiento(puerto=puerto) == IR


# ---------------------------------------------------------------------------
# Botón del panel (POST /api/desplazamiento/ordenar)
# ---------------------------------------------------------------------------
@pytest.fixture
def panel():
    estado = EstadoPanel()
    servidor = iniciar_panel(estado, "127.0.0.1", 0)
    yield estado, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def post(base, ruta, cuerpo=None, headers=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else b""
    req = urllib.request.Request(base + ruta, data=datos, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_boton_ordena_y_el_panel_lo_refleja(panel):
    estado, base = panel
    assert post(base, "/api/desplazamiento/ordenar", {"punto": P}) == (200, {"ok": True, "resultado": "ordenado"})
    datos = json.loads(urllib.request.urlopen(base + "/api/estado").read())
    assert datos["desplazamiento"]["estado"] == "ordenado" and datos["desplazamiento"]["punto"] == P
    assert datos["desplazamiento"]["restante_s"] > 0
    assert estado.consultar_orden_desplazamiento() == IR


def test_boton_conflictos_y_datos_invalidos(panel):
    estado, base = panel
    assert post(base, "/api/desplazamiento/ordenar", {"punto": 99}) == (400, {"error": "punto_invalido"})
    assert post(base, "/api/desplazamiento/ordenar") == (400, {"error": "punto_invalido"})
    assert post(base, "/api/desplazamiento/ordenar", {"punto": P})[0] == 200
    assert post(base, "/api/desplazamiento/ordenar", {"punto": P}) == (409, {"error": "ya_ordenado"})


def test_boton_con_recorrido_en_marcha_responde_409(panel):
    estado, base = panel
    estado.ordenar_inicio()
    assert post(base, "/api/desplazamiento/ordenar", {"punto": P}) == (409, {"error": "ciclo_en_curso"})
    assert estado.consultar_orden_desplazamiento() == NINGUNO      # no se ordenó nada


def test_boton_con_origen_ajeno_se_rechaza(panel):
    estado, base = panel
    assert post(base, "/api/desplazamiento/ordenar", {"punto": P}, {"Origin": "http://malicioso.example"})[0] == 403
    assert estado.consultar_orden_desplazamiento() == NINGUNO


def test_la_pagina_trae_el_boton_y_la_configuracion_muestra_los_tiempos(panel):
    _, base = panel
    html = urllib.request.urlopen(base + "/").read().decode()
    js = urllib.request.urlopen(base + "/static/panel.js").read().decode()
    assert 'id="boton-posicionar"' in html and 'id="punto-desplazamiento"' in html
    assert "/api/desplazamiento/ordenar" in js
    config = json.loads(urllib.request.urlopen(base + "/api/estado").read())["config"]
    assert any(c["nombre"] == "Vigencia de la orden de desplazamiento" for c in config)
