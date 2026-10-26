"""Pruebas del enganche del inventario con el estado del panel y de su API. Correr desde pc/:
    python3 -m pytest tests/
"""
import itertools
import logging
import sys
import types
from datetime import date
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import usuarios as modulo_usuarios  # noqa: E402
from auditoria import Auditoria  # noqa: E402
from cliente_http import Cliente  # noqa: E402
from estado_panel import RESPONSABLE_SIN_SESION, EstadoPanel  # noqa: E402
from inventario import Inventario  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from usuarios import ROL_ADMINISTRADOR, GestorUsuarios  # noqa: E402

HOY = date(2026, 10, 26)
ERR = "ERROR_REVISION_MANUAL"
CLAVE = "clave-larga-123"


@pytest.fixture(autouse=True)
def hash_rapido(monkeypatch):
    monkeypatch.setattr(modulo_usuarios, "ITERACIONES_HASH", 1_000)


def eventos(aud, tipo=None):
    todos = list(reversed(aud.listar(limite=500)["eventos"]))
    return [e for e in todos if tipo is None or e["tipo"] == tipo]


# ---------------------------------------------------------------------------
# El estado crea las cajas y fija sus destinos
# ---------------------------------------------------------------------------
@pytest.fixture
def mundo():
    aud = Auditoria(":memory:")
    reloj = itertools.count(1_000_000, 1000)
    inv = Inventario(aud, hoy=lambda: HOY, ahora_ms=lambda: next(reloj))
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    estado.conectar_inventario(inv)
    estado.registrar_arranque(base=":memory:")
    yield types.SimpleNamespace(aud=aud, inv=inv, estado=estado)
    aud.cerrar()


def test_cada_lote_recibido_crea_su_caja_y_el_evento_dice_cual(mundo):
    lote = mundo.estado.registrar_lote("192.168.0.5", [b"a"] * 5, "TIPO_B")
    caja = mundo.inv.listar()["cajas"][0]
    assert (caja["captura_id"], caja["arranque"], caja["resultado"], caja["destino"]) == (lote, 1, "TIPO_B", "TIPO_B")
    assert caja["origen"] == "192.168.0.5" and caja["n_imagenes"] == 5
    ev = eventos(mundo.aud, "lote_recibido")[0]
    assert ev["detalle"]["caja_id"] == caja["id"] and ev["detalle"]["arranque"] == 1 and ev["detalle"]["lote_id"] == lote


def test_la_caja_queda_a_nombre_de_quien_tiene_el_recorrido(mundo):
    e = mundo.estado
    e.registrar_lote("x", [b"a"] * 5, "TIPO_A")                                   # sin recorrido en marcha
    e.ordenar_inicio(None, responsable="Ana Pérez")
    e.consultar_orden_ciclo()
    e.confirmar_entrega_orden()
    e.registrar_lote("x", [b"a"] * 5, "TIPO_B")
    por_captura = {c["captura_id"]: c["turno_de"] for c in mundo.inv.listar()["cajas"]}
    assert por_captura == {1: None, 2: "Ana Pérez"}


def test_un_lote_vacio_tambien_es_una_caja_pendiente_de_decision(mundo):
    mundo.estado.registrar_lote("x", [], ERR)
    c = mundo.inv.listar()["cajas"][0]
    assert (c["n_imagenes"], c["resultado"], c["destino"]) == (0, ERR, None)


def test_la_decision_del_regente_fija_el_destino_de_la_caja(mundo):
    e = mundo.estado
    lote = e.registrar_lote("x", [], ERR)
    alarma = e.registrar_alarma("fallo_captura", lote)
    e.abrir_decision(alarma)
    assert e.resolver_alarma(alarma, "DESCARTE", responsable="Ana Pérez") == "resuelta"
    c = mundo.inv.listar()["cajas"][0]
    assert (c["destino"], c["destino_por"], c["resultado"]) == ("DESCARTE", "Ana Pérez", ERR)
    ev = eventos(mundo.aud, "alarma_resuelta")[0]
    assert ev["responsable"] == "Ana Pérez" and ev["detalle"]["caja_id"] == c["id"]
    assert ev["detalle"]["alarma_id"] == alarma and ev["detalle"]["destino"] == "DESCARTE"


def test_cerrar_una_alarma_antigua_no_toca_ninguna_caja(mundo):
    e = mundo.estado
    lote = e.registrar_lote("x", [], ERR)
    vieja = e.registrar_alarma("fallo_captura", lote)
    e.abrir_decision(vieja)
    e.abrir_decision(e.registrar_alarma("sin_consenso_ocr", e.registrar_lote("x", [], ERR)))
    assert e.resolver_alarma(vieja, None, responsable="Luis") == "cerrada"
    assert [c["destino"] for c in mundo.inv.listar()["cajas"]] == [None, None]
    assert eventos(mundo.aud, "alarma_cerrada")[0]["detalle"].get("caja_id") is None


def test_si_el_inventario_falla_el_flujo_sigue_y_el_evento_queda(mundo, monkeypatch, caplog):
    def roto(**k):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(mundo.inv, "registrar_caja", roto)
    with caplog.at_level(logging.ERROR, logger="auditoria"):
        lote = mundo.estado.registrar_lote("x", [b"a"] * 5, "TIPO_A")      # la Orange Pi no puede quedar esperando
    assert lote == 1 and mundo.inv.listar()["total"] == 0
    assert "no se pudo registrar la caja del lote 1" in caplog.text
    assert "caja_id" not in eventos(mundo.aud, "lote_recibido")[0]["detalle"]


def test_si_falla_fijar_el_destino_la_decision_igual_se_registra(mundo, monkeypatch):
    e = mundo.estado
    lote = e.registrar_lote("x", [], ERR)
    alarma = e.registrar_alarma("fallo_captura", lote)
    e.abrir_decision(alarma)
    def roto(**k):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(mundo.inv, "definir_destino", roto)
    assert e.resolver_alarma(alarma, "TIPO_C", responsable="Ana") == "resuelta"
    assert e.consultar_decision() == {"estado": "resuelta", "destino": "TIPO_C"}       # la Orange Pi lo recibe
    assert eventos(mundo.aud, "alarma_resuelta")[0]["responsable"] == "Ana"
    assert mundo.inv.listar()["cajas"][0]["destino"] is None                           # la caja quedó sin actualizar


def test_el_snapshot_dice_en_que_arranque_estamos(mundo):
    assert mundo.estado.snapshot()["arranque"] == 1
    assert EstadoPanel().snapshot()["arranque"] is None


def test_sin_inventario_conectado_todo_sigue_como_antes():
    aud = Auditoria(":memory:")
    e = EstadoPanel()
    e.conectar_auditoria(aud)
    assert e.registrar_lote("x", [b"a"], "TIPO_A") == 1
    assert eventos(aud, "lote_recibido")[0]["detalle"].get("caja_id") is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@pytest.fixture
def api():
    aud = Auditoria(":memory:")
    reloj = itertools.count(1_000_000, 1000)
    inv = Inventario(aud, hoy=lambda: HOY, ahora_ms=lambda: next(reloj))
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    estado.conectar_inventario(inv)
    estado.registrar_arranque(base=":memory:")
    g = GestorUsuarios(aud, inactividad_s=600)
    g.crear_usuario("consola local", "Root Admin", ROL_ADMINISTRADOR, CLAVE)
    servidor = iniciar_panel(estado, "127.0.0.1", 0, auditoria=aud, usuarios=g, inventario=inv)
    yield types.SimpleNamespace(aud=aud, inv=inv, estado=estado, g=g, base=f"http://127.0.0.1:{servidor.server_address[1]}")
    servidor.shutdown()
    servidor.server_close()


def poblar(api):
    for n, (res, med, lote, venc) in enumerate([
        ("TIPO_A", "Acetaminofén", "AB1234", "2026-09-30"), ("TIPO_B", "Ibuprofeno", "IB-77", "2026-11-15"),
        (ERR, "Amoxicilina", "AM9", "2027-03-01"), ("TIPO_A", None, None, None)], start=1):
        api.estado.registrar_lote("192.168.0.5", [b"a"] * 5, res)
        cambios = {k: v for k, v in dict(medicamento=med, numero_lote=lote, vencimiento=venc).items() if v}
        if cambios:
            api.inv.editar("Root Admin", n, cambios)


def con_sesion(api, codigo=None):
    c = Cliente(api.base)
    if codigo is None:
        assert c.post("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": CLAVE})[0] == 200
    else:
        assert c.post("/api/sesion/iniciar", {"codigo": codigo})[0] == 200
    return c


def auxiliar(api):
    return api.g.crear_usuario("Root Admin", "Ana Pérez", "auxiliar")[1]


def test_mirar_el_inventario_es_libre_y_trae_lo_necesario(api):
    poblar(api)
    estado, d = Cliente(api.base).get("/api/inventario")
    assert estado == 200 and d["total"] == 4 and len(d["cajas"]) == 4 and d["limite"] == 50 and d["desplazamiento"] == 0
    assert d["destinos"] == ["TIPO_A", "TIPO_B"]
    c = d["cajas"][0]
    assert {"id", "creada_ms", "captura_id", "arranque", "resultado", "destino", "turno_de", "medicamento",
            "gramaje", "numero_lote", "vencimiento", "notas", "editada", "eliminada", "dias_para_vencer"} <= set(c)
    assert "busqueda" not in c                       # el índice interno no sale


@pytest.mark.parametrize("consulta, esperados", [
    ("q=acetaminofen", {1}), ("q=ib-77", {2}), ("destino=TIPO_A", {1, 4}), ("destino=pendiente", {3}),
    ("revision=1", {3}), ("venc=vencidas", {1}), ("venc=180", {2, 3}), ("venc=sin", {4}),
    ("mostrar=eliminadas", set()), ("mostrar=todas", {1, 2, 3, 4}), ("q=acetaminofen&destino=TIPO_B", set()),
])
def test_filtros_por_http(api, consulta, esperados):
    poblar(api)
    estado, d = Cliente(api.base).get("/api/inventario?" + consulta)
    assert estado == 200 and {c["id"] for c in d["cajas"]} == esperados and d["total"] == len(esperados)


def test_orden_y_paginacion_por_http(api):
    poblar(api)
    c = Cliente(api.base)
    assert [x["id"] for x in c.get("/api/inventario?orden=id&dir=asc")[1]["cajas"]] == [1, 2, 3, 4]
    assert [x["id"] for x in c.get("/api/inventario?orden=vencimiento&dir=asc")[1]["cajas"]] == [1, 2, 3, 4]
    assert [x["id"] for x in c.get("/api/inventario?orden=vencimiento&dir=desc")[1]["cajas"]] == [3, 2, 1, 4]
    p = c.get("/api/inventario?orden=id&dir=asc&limite=2&desplazamiento=2")[1]
    assert [x["id"] for x in p["cajas"]] == [3, 4] and p["total"] == 4 and p["limite"] == 2


@pytest.mark.parametrize("consulta", [
    "orden=inventado", "orden=id%3BDROP%20TABLE%20cajas", "dir=ascendente", "mostrar=todo", "venc=15", "limite=mucho",
    "desde=ayer", "hasta=1.5", "desplazamiento=x",
])
def test_parametros_invalidos_responden_400(api, consulta):
    poblar(api)
    estado, cuerpo = Cliente(api.base).get("/api/inventario?" + consulta)
    assert estado == 400 and cuerpo["error"] == "parametro_invalido"
    assert api.inv.listar()["total"] == 4


def test_la_busqueda_no_ejecuta_sql(api):
    poblar(api)
    c = Cliente(api.base)
    for ataque in ("%27%3B%20DROP%20TABLE%20cajas%3B%20--", "%27%20OR%20%271%27%3D%271"):
        assert c.get("/api/inventario?q=" + ataque)[1]["total"] == 0
    assert c.get("/api/inventario")[1]["total"] == 4


def test_vista_agrupada_por_http(api):
    poblar(api)
    estado, d = Cliente(api.base).get("/api/inventario/agrupado?venc=180")
    assert estado == 200 and d["total"] == 2
    assert [g["medicamento"] for g in d["grupos"]] == ["Ibuprofeno", "Amoxicilina"] and d["grupos"][0]["cajas"] == 1
    assert Cliente(api.base).get("/api/inventario/agrupado?orden=x")[0] == 200       # el orden no aplica aquí


def test_sin_inventario_responde_503():
    servidor = iniciar_panel(EstadoPanel(), "127.0.0.1", 0)
    try:
        c = Cliente(f"http://127.0.0.1:{servidor.server_address[1]}")
        assert c.get("/api/inventario") == (503, {"error": "inventario_no_disponible"})
        assert c.get("/api/inventario/agrupado")[0] == 503
        assert c.post("/api/inventario/1/editar", {"cambios": {"notas": "x"}})[0] == 503
    finally:
        servidor.shutdown()
        servidor.server_close()


# ---- cambios: exigen turno -------------------------------------------------------
@pytest.mark.parametrize("ruta, cuerpo", [
    ("/api/inventario/1/editar", {"cambios": {"notas": "x"}}),
    ("/api/inventario/1/eliminar", {"motivo": "x"}),
    ("/api/inventario/1/restaurar", None),
])
def test_sin_turno_no_se_toca_el_inventario(api, ruta, cuerpo):
    poblar(api)
    antes = api.inv.obtener(1)
    assert Cliente(api.base).post(ruta, cuerpo) == (401, {"error": "sesion_requerida"})
    assert api.inv.obtener(1) == antes
    den = eventos(api.aud, "accion_denegada")[-1]
    assert den["detalle"]["accion"] in ("editar_caja", "eliminar_caja", "restaurar_caja")


def test_un_auxiliar_edita_y_queda_a_su_nombre(api):
    poblar(api)
    c = con_sesion(api, auxiliar(api))
    estado, d = c.post("/api/inventario/4/editar", {"cambios": {"medicamento": "Naproxeno", "gramaje": "250 mg",
                                                              "numero_lote": "NP-1", "vencimiento": "2027-01-31"}})
    assert estado == 200 and d["caja"]["medicamento"] == "Naproxeno" and d["caja"]["editada"] is True
    assert d["caja"]["dias_para_vencer"] == 97 and d["caja"]["datos_origen"] == "manual"
    ev = eventos(api.aud, "caja_editada")[-1]
    assert ev["responsable"] == "Ana Pérez" and ev["detalle"]["cambios"]["medicamento"] == [None, "Naproxeno"]
    assert Cliente(api.base).get("/api/inventario?q=naproxeno")[1]["total"] == 1


def test_notas_largas_con_tildes_caben(api):
    poblar(api)
    c = con_sesion(api)
    notas = "ñ" * 500                                                                # ~1 KB en UTF-8, más que el límite normal
    assert c.post("/api/inventario/1/editar", {"cambios": {"notas": notas}})[0] == 200
    assert c.post("/api/inventario/1/editar", {"cambios": {"notas": "ñ" * 501}})[1] == {"error": "valor_invalido", "campo": "notas"}


@pytest.mark.parametrize("cambios, estado, error, extra", [
    ({"resultado": "TIPO_A"}, 400, "campo_no_editable", {"campo": "resultado"}),
    ({"destino": "DESCARTE"}, 400, "campo_no_editable", {"campo": "destino"}),
    ({"vencimiento": "31/12/2026"}, 400, "valor_invalido", {"campo": "vencimiento"}),
    ({"medicamento": 5}, 400, "valor_invalido", {"campo": "medicamento"}),
    ({}, 400, "sin_cambios", {}), (None, 400, "sin_cambios", {}), ("x", 400, "sin_cambios", {}),
])
def test_ediciones_invalidas(api, cambios, estado, error, extra):
    poblar(api)
    c = con_sesion(api)
    assert c.post("/api/inventario/1/editar", {"cambios": cambios}) == (estado, {"error": error, **extra})


def test_caja_inexistente_o_eliminada(api):
    poblar(api)
    c = con_sesion(api)
    assert c.post("/api/inventario/999/editar", {"cambios": {"notas": "x"}})[0] == 404
    assert c.post("/api/inventario/999/eliminar", {})[0] == 404
    assert c.post("/api/inventario/999/restaurar")[0] == 404
    c.post("/api/inventario/1/eliminar", {})
    assert c.post("/api/inventario/1/editar", {"cambios": {"notas": "x"}})[1] == {"error": "caja_eliminada"}


def test_eliminar_y_restaurar_por_http(api):
    poblar(api)
    c = con_sesion(api, auxiliar(api))
    estado, d = c.post("/api/inventario/2/eliminar", {"motivo": "duplicada"})
    assert estado == 200 and d["caja"]["eliminada"] and d["caja"]["eliminada_por"] == "Ana Pérez"
    assert c.post("/api/inventario/2/eliminar", {})[1] == {"error": "ya_eliminada"}
    assert 2 not in {x["id"] for x in c.get("/api/inventario")[1]["cajas"]}
    assert [x["id"] for x in c.get("/api/inventario?mostrar=eliminadas")[1]["cajas"]] == [2]
    assert c.post("/api/inventario/2/restaurar")[1]["caja"]["eliminada"] is False
    assert c.post("/api/inventario/2/restaurar")[1] == {"error": "no_eliminada"}
    ev = eventos(api.aud, "caja_eliminada")[-1]
    assert ev["responsable"] == "Ana Pérez" and ev["detalle"]["motivo"] == "duplicada" and ev["detalle"]["medicamento"] == "Ibuprofeno"
    assert eventos(api.aud, "caja_restaurada")[-1]["responsable"] == "Ana Pérez"


def test_el_origen_ajeno_se_rechaza(api):
    poblar(api)
    c = con_sesion(api)
    assert c.post("/api/inventario/1/eliminar", {}, {"Origin": "http://otro.sitio"})[0] == 403
    assert api.inv.obtener(1)["eliminada"] is False


def test_sin_gestor_de_usuarios_se_puede_editar_como_operador_sin_sesion():
    aud = Auditoria(":memory:")
    inv = Inventario(aud, hoy=lambda: HOY)
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    estado.conectar_inventario(inv)
    estado.registrar_lote("x", [b"a"], "TIPO_A")
    servidor = iniciar_panel(estado, "127.0.0.1", 0, auditoria=aud, inventario=inv)
    try:
        c = Cliente(f"http://127.0.0.1:{servidor.server_address[1]}")
        assert c.post("/api/inventario/1/editar", {"cambios": {"notas": "prueba"}})[0] == 200
        assert eventos(aud, "caja_editada")[0]["responsable"] == RESPONSABLE_SIN_SESION
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_el_registro_sigue_integro_y_ningun_dato_de_busqueda_se_filtra(api):
    poblar(api)
    c = con_sesion(api)
    c.post("/api/inventario/1/editar", {"cambios": {"notas": "uno"}})
    c.post("/api/inventario/1/eliminar", {"motivo": "prueba"})
    assert api.aud.verificar()["ok"]
    assert "busqueda" not in str(c.get("/api/inventario?mostrar=todas")[1])
