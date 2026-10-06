"""Pruebas del panel web (panel_web.py + estado_panel.py), sin red real
más allá de loopback. Correr desde pc/:  python3 -m pytest tests/
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from estado_panel import EstadoPanel, MAX_LOTES_CON_FOTOS  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402


@pytest.fixture
def panel():
    estado = EstadoPanel()
    servidor = iniciar_panel(estado, "127.0.0.1", 0)
    yield estado, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.headers, r.read()


def _post(url, cuerpo=None, headers=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method="POST", headers=headers or {})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read())


def _codigo(url, metodo="GET", headers=None, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo, headers=headers or {})
    try:
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as e:
        return e.code
    return 200


def test_pagina_y_estaticos(panel):
    _, base = panel
    status, headers, cuerpo = _get(base + "/")
    assert status == 200 and b"SmartPharma Cart" in cuerpo
    assert b'id="visor"' in cuerpo  # visor de fotos ampliadas
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert _get(base + "/static/panel.js")[0] == 200
    assert _get(base + "/static/panel.css")[0] == 200
    assert _codigo(base + "/static/../server.py") == 404


def test_estado_vacio_y_config(panel):
    _, base = panel
    datos = json.loads(_get(base + "/api/estado")[2])
    assert datos["lotes"] == [] and datos["alarmas"] == []
    assert datos["alarma_activa"] is False
    assert any(c["nombre"] == "Puerto TCP (consulta de decisión)" for c in datos["config"])


def test_lote_fotos_y_alarma(panel):
    estado, base = panel
    imagenes = [bytes([i]) * 10 for i in range(5)]
    lote_id = estado.registrar_lote("10.0.0.5", imagenes, "ERROR_REVISION_MANUAL")
    alarma_id = estado.registrar_alarma("sin_consenso_ocr", lote_id)

    datos = json.loads(_get(base + "/api/estado")[2])
    assert datos["lotes"][0]["n_imagenes"] == 5
    assert datos["lotes"][0]["fotos_disponibles"] is True
    assert datos["alarma_activa"] is True
    assert "imagenes" not in datos["lotes"][0]

    status, headers, cuerpo = _get(f"{base}/foto/{lote_id}/2")
    assert status == 200 and cuerpo == imagenes[2]
    assert headers["Content-Type"] == "image/jpeg"
    assert headers["Cache-Control"] == "no-store"
    assert _codigo(f"{base}/foto/{lote_id}/5") == 404
    assert _codigo(f"{base}/foto/999/0") == 404

    # Alarma que la Orange Pi no espera: solo se cierra, sin decisión.
    assert _post(f"{base}/api/alarmas/{alarma_id}/resolver")[1] == {
        "ok": True, "decision_aplicada": False}
    datos = json.loads(_get(base + "/api/estado")[2])
    assert datos["alarma_activa"] is False
    assert datos["alarmas"][0]["activa"] is False


def test_resolver_alarma_con_decision_pendiente(panel):
    estado, base = panel
    alarma_id = estado.registrar_alarma("fallo_captura")
    estado.abrir_decision(alarma_id)
    url = f"{base}/api/alarmas/{alarma_id}/resolver"

    assert _codigo(url, "POST") == 400                              # falta el destino
    assert _codigo(url, "POST", cuerpo={"destino": "tipo a!"}) == 400
    assert estado.consultar_decision() == {"estado": "pendiente"}

    assert _post(url, {"destino": "TIPO_B"})[1] == {"ok": True, "decision_aplicada": True}
    assert estado.consultar_decision() == {"estado": "resuelta", "destino": "TIPO_B"}
    datos = json.loads(_get(base + "/api/estado")[2])
    assert datos["alarmas"][0]["destino"] == "TIPO_B"
    assert datos["alarmas"][0]["espera_decision"] is False


def test_resolver_alarma_inexistente(panel):
    _, base = panel
    assert _codigo(base + "/api/alarmas/42/resolver", "POST") == 404


def test_post_con_origen_ajeno_se_rechaza(panel):
    estado, base = panel
    alarma_id = estado.registrar_alarma("fallo_captura")
    url = f"{base}/api/alarmas/{alarma_id}/resolver"
    assert _codigo(url, "POST", {"Origin": "http://malicioso.example"}) == 403
    assert estado.snapshot()["alarma_activa"] is True
    host = base.removeprefix("http://")
    assert _post(url, headers={"Origin": base})[0] == 200
    assert estado.snapshot()["alarma_activa"] is False
    assert host  # el Origin legítimo coincide con el Host


def test_solo_se_conservan_fotos_de_los_ultimos_lotes():
    estado = EstadoPanel()
    ids = [estado.registrar_lote("x", [b"a"], "TIPO_A") for _ in range(MAX_LOTES_CON_FOTOS + 2)]
    assert estado.foto(ids[0], 0) is None
    assert estado.foto(ids[-1], 0) == b"a"
    viejo = next(l for l in estado.snapshot()["lotes"] if l["id"] == ids[0])
    assert viejo["fotos_disponibles"] is False
