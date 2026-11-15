"""Telemetría de la ESP32-S3 y de la Orange Pi (PC-24, informe 4.9 y 5.6): historial en RAM, mensaje
`telemetria` del puerto 5001 y endpoint del panel. Correr desde pc/:  python3 -m pytest tests/"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

from estado_panel import EstadoPanel  # noqa: E402
from mock_orangepi_client import consultar_orden_ciclo, enviar_telemetria, telemetria_de_ejemplo  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from servidor_decision import crear_servidor_decision  # noqa: E402
from telemetria import MAX_MUESTRAS, HistorialTelemetria  # noqa: E402


class Reloj:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def historial(reloj):
    return HistorialTelemetria(reloj=reloj)


def test_sin_datos_el_snapshot_lo_dice(historial):
    s = historial.snapshot()
    assert s["hay_datos"] is False and s["orange_pi"] is None and s["esp32"] is None and s["series"] == {}


def test_guarda_la_ultima_lectura_de_cada_fuente(historial):
    assert historial.registrar("192.168.0.5", telemetria_de_ejemplo(3)) is True
    s = historial.snapshot()
    assert s["hay_datos"] and s["ip"] == "192.168.0.5" and s["recibidos"] == 1
    assert s["orange_pi"]["fase"] == "esperando_orden" and s["orange_pi"]["cpu_pct"] == 15.0
    assert s["esp32"]["enc"] == [1230, -300, 0, 55, 900] and s["esp32"]["pwm"] == [0] * 11
    assert s["esp32"]["estado"] == "reposo" and s["esp32"]["alarma"] is False


def test_un_sensor_caido_va_como_null_y_el_mensaje_sigue_siendo_valido(historial):
    m = telemetria_de_ejemplo()
    m["esp32"]["giro_dps"] = [None, None, None]
    m["esp32"]["corriente_ma"] = [None, 95]
    m["esp32"]["temp_c"] = None
    m["orange_pi"]["wifi_dbm"] = None
    assert historial.registrar("ip", m)
    s = historial.snapshot()
    assert s["esp32"]["giro_dps"] == [None, None, None] and s["esp32"]["corriente_ma"] == [None, 95]
    assert "esp_corriente_1" not in s["series"] and s["series"]["esp_corriente_2"]      # hueco, no un cero


def test_sin_lectura_de_la_esp32_todavia(historial):
    m = telemetria_de_ejemplo()
    m["esp32"], m["esp32_edad_s"] = None, None
    assert historial.registrar("ip", m)
    s = historial.snapshot()
    assert s["orange_pi"] and s["esp32"] is None and s["esp32_vieja"] is True


@pytest.mark.parametrize("m", [
    {},                                                                  # falta orange_pi
    {"orange_pi": "texto", "esp32": None, "esp32_edad_s": None},
    {"orange_pi": {}, "esp32": [1, 2], "esp32_edad_s": None},
    {"orange_pi": {}, "esp32": None, "esp32_edad_s": "mucho"},
])
def test_mensajes_fuera_del_contrato_se_rechazan(historial, m):
    assert historial.registrar("ip", m) is False
    assert historial.snapshot()["hay_datos"] is False


def test_valores_con_otro_tipo_no_se_muestran_como_si_fueran_buenos(historial):
    m = telemetria_de_ejemplo()
    m["orange_pi"]["cpu_pct"] = "alto"
    m["orange_pi"]["fase"] = "volando"
    m["esp32"]["enc"] = [1, 2]                  # largo equivocado
    m["esp32"]["alarma"] = "si"
    assert historial.registrar("ip", m)
    s = historial.snapshot()
    assert s["orange_pi"]["cpu_pct"] is None and s["orange_pi"]["fase"] is None
    assert s["esp32"]["enc"] is None and s["esp32"]["alarma"] is None


def test_datos_viejos_si_la_orange_pi_deja_de_enviar(historial, reloj):
    historial.registrar("ip", telemetria_de_ejemplo())
    reloj.t += 6
    assert historial.snapshot()["viejos"] is False
    reloj.t += 20                                # pasó mucho más que tres envíos de 5 s
    s = historial.snapshot()
    assert s["viejos"] is True and s["edad_s"] == 26.0


def test_la_lectura_de_la_esp32_envejece_aunque_la_orange_pi_siga_enviando(historial, reloj):
    m = telemetria_de_ejemplo()
    m["esp32_edad_s"] = 30.0                     # la ESP32-S3 dejó de responder: la Orange Pi repite la última
    historial.registrar("ip", m)
    s = historial.snapshot()
    assert s["viejos"] is False and s["esp32_vieja"] is True and s["esp32_edad_s"] == 30.0


def test_el_historial_se_acota_por_tiempo_y_por_cantidad(reloj):
    h = HistorialTelemetria(ventana_s=60, reloj=reloj)
    for i in range(30):
        h.registrar("ip", telemetria_de_ejemplo(i))
        reloj.t += 5
    puntos = h.snapshot()["series"]["opi_cpu_pct"]
    assert 11 <= len(puntos) <= 13               # solo el último minuto
    h2 = HistorialTelemetria(ventana_s=10**9, reloj=reloj)
    for i in range(MAX_MUESTRAS + 50):
        h2.registrar("ip", telemetria_de_ejemplo(i))
    assert len(h2.snapshot()["series"]["opi_cpu_pct"]) == MAX_MUESTRAS


# --- por el puerto de consultas, como lo hace la Orange Pi -----------------------------------

@pytest.fixture
def canal():
    estado = EstadoPanel()
    servidor = crear_servidor_decision(estado, 0, "127.0.0.1")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield estado, servidor.server_address[1]
    servidor.shutdown()
    servidor.server_close()


def test_la_pc_responde_ok_y_guarda_la_telemetria(canal):
    estado, puerto = canal
    assert enviar_telemetria(puerto=puerto) == {"ok": True}
    assert estado.telemetria.snapshot()["hay_datos"] is True


def test_la_telemetria_cuenta_como_contacto_de_la_orange_pi(canal):
    estado, puerto = canal
    assert estado.snapshot()["orangepi"]["conectada"] is False
    enviar_telemetria(puerto=puerto)
    assert estado.snapshot()["orangepi"]["conectada"] is True


def test_un_mensaje_invalido_recibe_consulta_invalida_y_no_se_guarda(canal):
    estado, puerto = canal
    assert enviar_telemetria({"esp32": None}, puerto=puerto) == {"error": "consulta_invalida"}
    assert estado.telemetria.snapshot()["hay_datos"] is False


def test_la_telemetria_no_toca_el_ciclo_ni_la_orden_de_inicio(canal):
    estado, puerto = canal
    antes = estado.snapshot()["ciclo"]
    for t in range(5):
        enviar_telemetria(telemetria_de_ejemplo(t), puerto=puerto)
    assert estado.snapshot()["ciclo"] == antes
    assert consultar_orden_ciclo(puerto=puerto) == {"orden": "esperando"}


def test_la_telemetria_no_llega_al_registro_de_eventos():
    from auditoria import Auditoria
    aud = Auditoria(":memory:")
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    servidor = crear_servidor_decision(estado, 0, "127.0.0.1")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        antes = aud.total()
        for t in range(3):
            enviar_telemetria(telemetria_de_ejemplo(t), puerto=servidor.server_address[1])
        assert aud.total() == antes
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_endpoint_del_panel_entrega_el_snapshot(canal):
    estado, puerto = canal
    servidor = iniciar_panel(estado, "127.0.0.1", 0)
    try:
        url = f"http://127.0.0.1:{servidor.server_address[1]}/api/telemetria"
        assert json.load(urllib.request.urlopen(url))["hay_datos"] is False
        enviar_telemetria(puerto=puerto)
        d = json.load(urllib.request.urlopen(url))
        assert d["hay_datos"] and d["esp32"]["corriente_ma"] == [120, 95] and "opi_cpu_pct" in d["series"]
    finally:
        servidor.shutdown()
        servidor.server_close()
