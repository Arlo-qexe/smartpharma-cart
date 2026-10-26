"""Pruebas del registro de eventos (auditoria.py), de su conexión con el estado del panel
(quién hizo qué) y de sus endpoints. Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import logging
import sqlite3
import sys
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import estado_panel  # noqa: E402
from auditoria import (  # noqa: E402
    HASH_INICIAL,
    Auditoria,
    ErrorRegistro,
    ruta_db_desde_entorno,
)
from estado_panel import (  # noqa: E402
    RESPONSABLE_SIN_SESION,
    RESPONSABLE_SISTEMA,
    EstadoPanel,
)
from panel_web import iniciar_panel  # noqa: E402
from protocol_constants import VIGENCIA_ORDEN_CICLO_S  # noqa: E402


@pytest.fixture
def aud():
    a = Auditoria(":memory:")
    yield a
    a.cerrar()


@pytest.fixture
def archivo(tmp_path):
    return tmp_path / "datos" / "registro.db"


def tipos_de(a):
    return [e["tipo"] for e in reversed(a.listar(limite=500)["eventos"])]


# ---------------------------------------------------------------------------
# Registro y cadena de hashes
# ---------------------------------------------------------------------------
def test_ids_consecutivos_y_orden_del_mas_reciente_al_mas_antiguo(aud):
    ids = [aud.registrar("lote_recibido", "sistema", {"lote_id": i}) for i in range(1, 4)]
    assert ids == [1, 2, 3]
    lista = aud.listar()["eventos"]
    assert [e["id"] for e in lista] == [3, 2, 1]
    assert lista[0]["detalle"] == {"lote_id": 3} and lista[0]["responsable"] == "sistema"
    assert aud.total() == 3


def test_cada_evento_se_encadena_con_el_anterior(aud):
    aud.registrar("a", "sistema")
    aud.registrar("b", "ana", {"x": 1})
    filas = aud._con.execute("SELECT hash_prev, hash FROM eventos ORDER BY id").fetchall()
    assert filas[0][0] == HASH_INICIAL
    assert filas[1][0] == filas[0][1]
    v = aud.verificar()
    assert v["ok"] and v["total"] == 2 and v["ultimo_hash"] == filas[1][1]


def test_registro_vacio_es_integro(aud):
    assert aud.verificar() == {"ok": True, "total": 0, "ultimo_id": 0, "ultimo_hash": HASH_INICIAL,
                               "primer_invalido": None, "motivo": None}


@pytest.mark.parametrize("tipo, responsable, detalle", [
    ("", "sistema", None), ("  ", "sistema", None), (None, "sistema", None),
    ("x", "", None), ("x", "   ", None), ("x", None, None), ("x", "sistema", [1, 2]),
])
def test_evento_invalido_se_rechaza(aud, tipo, responsable, detalle):
    with pytest.raises(ErrorRegistro):
        aud.registrar(tipo, responsable, detalle)
    assert aud.total() == 0


def test_detalle_con_tildes_y_caracteres_raros(aud):
    aud.registrar("x", "Ana María", {"motivo": "revisión — ñandú", "n": 3})
    assert aud.listar()["eventos"][0]["detalle"] == {"motivo": "revisión — ñandú", "n": 3}
    assert aud.verificar()["ok"]


# ---------------------------------------------------------------------------
# Solo escritura y detección de alteraciones
# ---------------------------------------------------------------------------
def test_no_se_puede_editar_ni_borrar_un_evento(aud):
    aud.registrar("a", "sistema", {"k": 1})
    with pytest.raises(sqlite3.Error, match="solo escritura"):
        aud._con.execute("UPDATE eventos SET responsable = 'otro' WHERE id = 1")
    with pytest.raises(sqlite3.Error, match="solo escritura"):
        aud._con.execute("DELETE FROM eventos WHERE id = 1")
    assert aud.listar()["eventos"][0]["responsable"] == "sistema" and aud.total() == 1


def _manipular(ruta, *sentencias):
    """Alteración 'a mano' del archivo, saltándose los triggers (lo que haría alguien con acceso al disco)."""
    con = sqlite3.connect(ruta)
    con.execute("DROP TRIGGER eventos_sin_update")
    con.execute("DROP TRIGGER eventos_sin_delete")
    for s in sentencias:
        con.execute(s)
    con.commit()
    con.close()


@pytest.fixture
def con_cinco(archivo):
    a = Auditoria(archivo)
    for i in range(1, 6):
        a.registrar("lote_recibido", "sistema", {"lote_id": i})
    a.cerrar()
    return archivo


def test_detecta_un_evento_modificado(con_cinco):
    _manipular(con_cinco, "UPDATE eventos SET responsable = 'otro' WHERE id = 3")
    a = Auditoria(con_cinco)
    v = a.verificar()
    assert (v["ok"], v["primer_invalido"], v["motivo"]) == (False, 3, "evento_modificado")
    assert v["total"] == 2          # los dos anteriores sí estaban bien


def test_detecta_un_evento_borrado_en_medio(con_cinco):
    _manipular(con_cinco, "DELETE FROM eventos WHERE id = 3")
    v = Auditoria(con_cinco).verificar()
    assert (v["ok"], v["primer_invalido"], v["motivo"]) == (False, 4, "falta_un_evento")


def test_detecta_el_primer_evento_borrado(con_cinco):
    _manipular(con_cinco, "DELETE FROM eventos WHERE id = 1")
    v = Auditoria(con_cinco).verificar()
    assert (v["ok"], v["primer_invalido"], v["motivo"]) == (False, 2, "falta_un_evento")


def test_detecta_la_cadena_rota(con_cinco):
    _manipular(con_cinco, f"UPDATE eventos SET hash_prev = '{'a' * 64}' WHERE id = 4")
    v = Auditoria(con_cinco).verificar()
    assert (v["ok"], v["primer_invalido"], v["motivo"]) == (False, 4, "cadena_rota")


def test_recortar_el_final_no_rompe_la_cadena_pero_cambia_el_ultimo_sello(con_cinco):
    """Límite documentado: por eso el panel muestra el último sello, para conservarlo aparte."""
    antes = Auditoria(con_cinco).verificar()
    _manipular(con_cinco, "DELETE FROM eventos WHERE id > 3")
    despues = Auditoria(con_cinco).verificar()
    assert despues["ok"] and despues["total"] == 3
    assert despues["ultimo_hash"] != antes["ultimo_hash"]


def test_persistencia_tras_reabrir_y_la_cadena_continua(archivo):
    a = Auditoria(archivo)
    a.registrar("sistema_iniciado", "sistema")
    a.registrar("lote_recibido", "sistema", {"lote_id": 1})
    a.cerrar()
    b = Auditoria(archivo)                      # "reinicio de la PC"
    assert b.total() == 2
    assert b.registrar("sistema_iniciado", "sistema") == 3
    assert b.verificar()["ok"] and b.verificar()["total"] == 3


def test_escrituras_concurrentes_no_rompen_la_cadena(aud):
    def escribir(n):
        for i in range(25):
            aud.registrar("lote_recibido", f"hilo{n}", {"i": i})
    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(8)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    v = aud.verificar()
    assert v["ok"] and v["total"] == 200 and v["ultimo_id"] == 200


# ---------------------------------------------------------------------------
# Lectura con filtros
# ---------------------------------------------------------------------------
@pytest.fixture
def variado(aud):
    aud.registrar("alarma_abierta", "sistema", {"alarma_id": 1, "motivo": "fallo_captura"}, ts_ms=1_000)
    aud.registrar("alarma_resuelta", "Ana Pérez", {"alarma_id": 1, "destino": "DESCARTE"}, ts_ms=2_000)
    aud.registrar("lote_recibido", "sistema", {"origen": "192.168.0.5", "resultado": "TIPO_A"}, ts_ms=3_000)
    aud.registrar("alarma_resuelta", "Luis", {"alarma_id": 2, "destino": "TIPO_B"}, ts_ms=4_000)
    return aud


def test_filtro_por_tipo(variado):
    assert [e["id"] for e in variado.listar(tipo="alarma_resuelta")["eventos"]] == [4, 2]
    assert variado.tipos() == ["alarma_abierta", "alarma_resuelta", "lote_recibido"]


def test_busqueda_de_texto_en_responsable_y_detalle(variado):
    assert [e["id"] for e in variado.listar(texto="ana")["eventos"]] == [2]          # sin distinguir mayúsculas
    assert [e["id"] for e in variado.listar(texto="DESCARTE")["eventos"]] == [2]
    assert [e["id"] for e in variado.listar(texto="192.168.0.5")["eventos"]] == [3]
    assert variado.listar(texto="no existe")["eventos"] == []


def test_la_busqueda_trata_porcentaje_y_guion_bajo_como_texto(variado):
    variado.registrar("x", "sistema", {"nota": "100% listo_ya"}, ts_ms=5_000)
    assert [e["id"] for e in variado.listar(texto="100%")["eventos"]] == [5]
    assert [e["id"] for e in variado.listar(texto="listo_ya")["eventos"]] == [5]
    assert variado.listar(texto="%")["eventos"][0]["id"] == 5    # un % suelto no es comodín
    assert len(variado.listar(texto="%")["eventos"]) == 1


def test_rango_de_fechas(variado):
    assert [e["id"] for e in variado.listar(desde_ms=2_000, hasta_ms=3_000)["eventos"]] == [3, 2]
    assert [e["id"] for e in variado.listar(desde_ms=3_500)["eventos"]] == [4]


def test_paginacion_hacia_atras_y_eventos_nuevos(aud):
    for i in range(10):
        aud.registrar("x", "sistema", {"i": i})
    p1 = aud.listar(limite=4)
    assert [e["id"] for e in p1["eventos"]] == [10, 9, 8, 7] and p1["hay_mas"]
    p2 = aud.listar(limite=4, antes_id=7)
    assert [e["id"] for e in p2["eventos"]] == [6, 5, 4, 3] and p2["hay_mas"]
    p3 = aud.listar(limite=4, antes_id=3)
    assert [e["id"] for e in p3["eventos"]] == [2, 1] and not p3["hay_mas"]
    aud.registrar("x", "sistema")
    assert [e["id"] for e in aud.listar(despues_id=10)["eventos"]] == [11]


def test_el_limite_se_acota(aud):
    for _ in range(3):
        aud.registrar("x", "sistema")
    assert len(aud.listar(limite=0)["eventos"]) == 1
    assert len(aud.listar(limite=10_000)["eventos"]) == 3


def test_ruta_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("PC_DB", "/tmp/otro.db")
    assert ruta_db_desde_entorno() == "/tmp/otro.db"
    monkeypatch.setenv("PC_DB", "  ")
    assert ruta_db_desde_entorno().endswith("data/smartpharma.db")
    monkeypatch.delenv("PC_DB")
    assert ruta_db_desde_entorno().endswith("data/smartpharma.db")


def test_no_arranca_con_una_ruta_imposible(tmp_path):
    archivo = tmp_path / "es_un_archivo"
    archivo.write_text("x")
    with pytest.raises((ErrorRegistro, OSError)):
        Auditoria(archivo / "no_puede_ser_carpeta" / "r.db")


# ---------------------------------------------------------------------------
# El estado del panel registra quién hizo qué
# ---------------------------------------------------------------------------
@pytest.fixture
def estado(aud):
    e = EstadoPanel()
    e.conectar_auditoria(aud)
    e.registrar_arranque(base=":memory:")
    return e


def eventos(aud, tipo=None):
    return [e for e in reversed(aud.listar(limite=500)["eventos"]) if tipo is None or e["tipo"] == tipo]


def test_el_arranque_va_primero_y_los_demas_eventos_lo_llevan(estado, aud):
    lote = estado.registrar_lote("192.168.0.5", [b"a"] * 5, "TIPO_A")
    estado.registrar_alarma("fallo_captura", lote)
    todos = eventos(aud)
    assert todos[0]["tipo"] == "sistema_iniciado" and "arranque" not in todos[0]["detalle"]
    assert all(e["detalle"]["arranque"] == todos[0]["id"] for e in todos[1:])


def test_lote_y_alarma_quedan_registrados(estado, aud):
    lote = estado.registrar_lote("192.168.0.5", [b"a"] * 5, "ERROR_REVISION_MANUAL")
    alarma = estado.registrar_alarma("sin_consenso_ocr", lote)
    l, a = eventos(aud, "lote_recibido")[0], eventos(aud, "alarma_abierta")[0]
    assert l["responsable"] == RESPONSABLE_SISTEMA
    assert l["detalle"] == {"lote_id": lote, "origen": "192.168.0.5", "n_imagenes": 5,
                            "resultado": "ERROR_REVISION_MANUAL", "arranque": 1}
    assert a["detalle"]["alarma_id"] == alarma and a["detalle"]["motivo"] == "sin_consenso_ocr"


def test_la_decision_del_regente_dice_cual_alarma_que_destino_y_de_quien(estado, aud):
    lote = estado.registrar_lote("192.168.0.5", [], "ERROR_REVISION_MANUAL")
    alarma = estado.registrar_alarma("fallo_captura", lote)
    estado.abrir_decision(alarma)
    assert estado.resolver_alarma(alarma, "DESCARTE", responsable="Ana Pérez") == "resuelta"
    ev = eventos(aud, "alarma_resuelta")
    assert len(ev) == 1
    assert ev[0]["responsable"] == "Ana Pérez"
    assert ev[0]["detalle"] == {"alarma_id": alarma, "motivo": "fallo_captura", "lote_id": lote,
                                "destino": "DESCARTE", "decision_aplicada": True, "arranque": 1}


def test_sin_responsable_explicito_queda_como_operador_sin_sesion(estado, aud):
    alarma = estado.registrar_alarma("fallo_captura", 1)
    estado.abrir_decision(alarma)
    estado.resolver_alarma(alarma, "TIPO_B")
    assert eventos(aud, "alarma_resuelta")[0]["responsable"] == RESPONSABLE_SIN_SESION


def test_resolver_dos_veces_no_duplica_el_evento(estado, aud):
    alarma = estado.registrar_alarma("fallo_captura", 1)
    estado.abrir_decision(alarma)
    estado.resolver_alarma(alarma, "TIPO_A", responsable="Ana")
    estado.resolver_alarma(alarma, "TIPO_C", responsable="Luis")     # ya estaba resuelta
    assert len(eventos(aud, "alarma_resuelta")) == 1
    assert len(eventos(aud, "alarma_cerrada")) == 0


def test_una_alarma_antigua_se_cierra_sin_decision_y_queda_anotado(estado, aud):
    vieja = estado.registrar_alarma("fallo_captura", 1)
    estado.abrir_decision(vieja)
    nueva = estado.registrar_alarma("sin_consenso_ocr", 2)
    estado.abrir_decision(nueva)                  # la Orange Pi ya siguió: la vieja ya nadie la espera
    assert estado.resolver_alarma(vieja, None, responsable="Luis") == "cerrada"
    ev = eventos(aud, "alarma_cerrada")[0]
    assert ev["responsable"] == "Luis"
    assert ev["detalle"]["decision_aplicada"] is False and ev["detalle"]["destino"] is None


def test_destino_invalido_no_deja_evento(estado, aud):
    alarma = estado.registrar_alarma("fallo_captura", 1)
    estado.abrir_decision(alarma)
    assert estado.resolver_alarma(alarma, "mal destino!", responsable="Ana") == "destino_invalido"
    assert eventos(aud, "alarma_resuelta") == []


def test_la_entrega_del_destino_a_la_orange_pi_se_anota_una_sola_vez(estado, aud):
    alarma = estado.registrar_alarma("fallo_captura", 1)
    estado.abrir_decision(alarma)
    estado.consultar_decision("192.168.0.5")                   # pendiente: nada que anotar
    estado.resolver_alarma(alarma, "DESCARTE", responsable="Ana")
    for _ in range(3):                                         # la Orange Pi consulta cada 2 s
        estado.consultar_decision("192.168.0.5")
    ev = eventos(aud, "decision_entregada")
    assert len(ev) == 1
    assert ev[0]["detalle"]["destino"] == "DESCARTE" and ev[0]["detalle"]["ip_orangepi"] == "192.168.0.5"


def test_ciclo_completo_por_cantidad_con_sus_responsables(estado, aud):
    estado.ordenar_inicio(2, responsable="Ana")
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    estado.consultar_estado_ciclo()
    estado.consultar_estado_ciclo()
    estado.consultar_estado_ciclo()                            # tercera: se alcanzó la cantidad
    assert [(e["tipo"], e["responsable"]) for e in eventos(aud)[1:]] == [
        ("ciclo_inicio_ordenado", "Ana"),
        ("ciclo_iniciado", "Ana"),                             # el ciclo corre bajo quien lo ordenó
        ("ciclo_detenido", RESPONSABLE_SISTEMA),
    ]
    fin = eventos(aud, "ciclo_detenido")[0]["detalle"]
    assert fin["motivo"] == "cantidad_alcanzada" and fin["cajas_iniciadas"] == 2
    assert fin["iniciado_por"] == "Ana" and fin["cantidad"] == 2


def test_detencion_por_el_regente_queda_a_su_nombre(estado, aud):
    estado.ordenar_inicio(None, responsable="Ana")
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    estado.consultar_estado_ciclo()
    assert estado.ordenar_detencion(responsable="Luis") == "ordenada"
    estado.consultar_estado_ciclo()
    pedido = eventos(aud, "ciclo_detencion_ordenada")[0]
    fin = eventos(aud, "ciclo_detenido")[0]
    assert pedido["responsable"] == "Luis" and fin["responsable"] == "Luis"
    assert fin["detalle"]["motivo"] == "detenido_por_regente" and fin["detalle"]["iniciado_por"] == "Ana"


def test_cancelar_la_orden_antes_de_que_se_recoja(estado, aud):
    estado.ordenar_inicio(5, responsable="Ana")
    assert estado.ordenar_detencion(responsable="Luis") == "orden_cancelada"
    ev = eventos(aud, "ciclo_orden_cancelada")[0]
    assert ev["responsable"] == "Luis" and ev["detalle"]["ordenada_por"] == "Ana"


def test_orden_que_vence_queda_anotada(estado, aud, monkeypatch):
    ahora = [1_000_000.0]
    monkeypatch.setattr(estado_panel, "time", types.SimpleNamespace(time=lambda: ahora[0]))
    estado.ordenar_inicio(3, responsable="Ana")
    ahora[0] += VIGENCIA_ORDEN_CICLO_S + 1
    estado.consultar_orden_ciclo()
    ev = eventos(aud, "ciclo_orden_vencida")[0]
    assert ev["responsable"] == RESPONSABLE_SISTEMA and ev["detalle"]["ordenada_por"] == "Ana"
    estado.consultar_orden_ciclo()
    assert len(eventos(aud, "ciclo_orden_vencida")) == 1       # solo se anota al vencer


def test_si_la_orange_pi_reinicia_en_pleno_ciclo_queda_anotado(estado, aud):
    estado.ordenar_inicio(None, responsable="Ana")
    estado.consultar_orden_ciclo()
    estado.confirmar_entrega_orden()
    estado.consultar_estado_ciclo()
    estado.consultar_orden_ciclo()                             # vuelve a pedir orden: se reinició
    ev = eventos(aud, "ciclo_interrumpido")[0]
    assert ev["detalle"]["iniciado_por"] == "Ana" and ev["detalle"]["cajas_iniciadas"] == 1


def test_un_fallo_al_registrar_no_rompe_el_flujo(caplog):
    class Roto:
        def registrar(self, *a, **k):
            raise ErrorRegistro("disco lleno")
    e = EstadoPanel()
    e.conectar_auditoria(Roto())
    with caplog.at_level(logging.ERROR, logger="auditoria"):
        lote = e.registrar_lote("x", [b"a"], "TIPO_A")          # la Orange Pi no puede quedar esperando
        alarma = e.registrar_alarma("fallo_captura", lote)
        e.abrir_decision(alarma)
        assert e.resolver_alarma(alarma, "TIPO_A", responsable="Ana") == "resuelta"
    assert lote == 1 and "no se pudo registrar el evento lote_recibido" in caplog.text


def test_sin_registro_conectado_todo_funciona_igual():
    e = EstadoPanel()
    assert e.registrar_lote("x", [], "TIPO_A") == 1
    assert e.registrar_arranque() is None


# ---------------------------------------------------------------------------
# Endpoints del panel
# ---------------------------------------------------------------------------
@pytest.fixture
def panel(aud):
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    estado.registrar_arranque(base=":memory:")
    servidor = iniciar_panel(estado, "127.0.0.1", 0, auditoria=aud)
    yield estado, aud, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def get(base, ruta):
    try:
        with urllib.request.urlopen(base + ruta, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def post(base, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else b""
    req = urllib.request.Request(base + ruta, data=datos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_api_log_lista_con_filtros_y_paginas(panel):
    estado, aud, base = panel
    for i in range(5):
        estado.registrar_lote("192.168.0.5", [b"a"], "TIPO_A")
    estado.registrar_alarma("fallo_captura", 1)
    status, d = get(base, "/api/log?limite=3")
    assert status == 200 and len(d["eventos"]) == 3 and d["hay_mas"] and d["total"] == 7
    assert set(d["tipos"]) == {"sistema_iniciado", "lote_recibido", "alarma_abierta"}
    assert get(base, "/api/log?tipo=alarma_abierta")[1]["eventos"][0]["detalle"]["motivo"] == "fallo_captura"
    assert len(get(base, "/api/log?q=192.168.0.5&limite=50")[1]["eventos"]) == 5
    nuevos = get(base, f"/api/log?despues={d['eventos'][0]['id']}")[1]["eventos"]
    assert nuevos == []


@pytest.mark.parametrize("consulta", ["limite=abc", "antes=x", "desde=1.5", "hasta=ma%C3%B1ana"])
def test_api_log_parametro_invalido_responde_400(panel, consulta):
    assert get(panel[2], "/api/log?" + consulta) == (400, {"error": "parametro_invalido"})


def test_api_log_verificar(panel):
    estado, aud, base = panel
    estado.registrar_lote("x", [], "TIPO_A")
    status, v = get(base, "/api/log/verificar")
    assert status == 200 and v["ok"] and v["total"] == 2


def test_api_log_sin_registro_responde_503():
    servidor = iniciar_panel(EstadoPanel(), "127.0.0.1", 0)
    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    try:
        assert get(base, "/api/log") == (503, {"error": "registro_no_disponible"})
        assert get(base, "/api/log/verificar") == (503, {"error": "registro_no_disponible"})
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_el_log_no_ofrece_ninguna_forma_de_editarlo_por_http(panel):
    _, aud, base = panel
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        req = urllib.request.Request(base + "/api/log", data=b"{}", method=metodo)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=3)
        assert e.value.code in (404, 501)
    assert aud.total() == 1


def test_las_acciones_del_panel_quedan_con_el_responsable(panel):
    estado, aud, base = panel
    lote = estado.registrar_lote("192.168.0.5", [], "ERROR_REVISION_MANUAL")
    alarma = estado.registrar_alarma("fallo_captura", lote)
    estado.abrir_decision(alarma)
    assert post(base, f"/api/alarmas/{alarma}/resolver", {"destino": "DESCARTE"})[0] == 200
    assert post(base, "/api/ciclo/iniciar", {"cantidad": 4})[0] == 200
    por_tipo = {e["tipo"]: e for e in eventos(aud)}
    assert por_tipo["alarma_resuelta"]["responsable"] == RESPONSABLE_SIN_SESION
    assert por_tipo["alarma_resuelta"]["detalle"]["destino"] == "DESCARTE"
    assert por_tipo["ciclo_inicio_ordenado"]["responsable"] == RESPONSABLE_SIN_SESION
    assert por_tipo["ciclo_inicio_ordenado"]["detalle"]["cantidad"] == 4


def test_la_pagina_trae_la_pestana_del_registro(panel):
    html = urllib.request.urlopen(panel[2] + "/").read().decode("utf-8")
    for id_ in ("tabla-registro", "registro-integridad", "reg-tipo", "reg-texto", "reg-aplicar", "reg-mas"):
        assert f'id="{id_}"' in html
    assert 'data-vista="registro"' in html
