"""Pruebas del inventario (inventario.py): altas, destino, edición, eliminación lógica, filtros,
orden, paginación y agrupación. Correr desde pc/:
    python3 -m pytest tests/
"""
import itertools
import sys
from datetime import date
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import auditoria as modulo_auditoria  # noqa: E402
from auditoria import Auditoria, ErrorRegistro  # noqa: E402
from inventario import (  # noqa: E402
    CAMPOS_EDITABLES,
    DESTINO_PENDIENTE,
    LIMITE_MAXIMO,
    ORDENES,
    ErrorInventario,
    Inventario,
    normalizar,
)

HOY = date(2026, 10, 26)
ERR = "ERROR_REVISION_MANUAL"


@pytest.fixture
def aud():
    a = Auditoria(":memory:")
    yield a
    a.cerrar()


@pytest.fixture
def inv(aud):
    reloj = itertools.count(1_000_000, 1000)
    return Inventario(aud, hoy=lambda: HOY, ahora_ms=lambda: next(reloj))


def caja(inv, n, resultado="TIPO_B", turno_de="Ana Pérez", arranque=1, **campos):
    """Crea una caja y, si se pide, le completa datos a mano."""
    cid = inv.registrar_caja(arranque=arranque, captura_id=n, origen="192.168.0.5", n_imagenes=5,
                             resultado=resultado, turno_de=turno_de,
                             evento=("lote_recibido", "sistema", {"lote_id": n, "arranque": arranque}))
    if campos:
        inv.editar("Ana Pérez", cid, campos)
    return cid


def eventos(aud, tipo=None):
    todos = list(reversed(aud.listar(limite=500)["eventos"]))
    return [e for e in todos if tipo is None or e["tipo"] == tipo]


def codigo(fn, *a, **k) -> str:
    with pytest.raises(ErrorInventario) as e:
        fn(*a, **k)
    return e.value.codigo


# ---------------------------------------------------------------------------
# Altas automáticas
# ---------------------------------------------------------------------------
def test_una_clasificacion_automatica_fija_el_destino_al_instante(inv, aud):
    cid = caja(inv, 1, "TIPO_B")
    c = inv.obtener(cid)
    assert (c["resultado"], c["destino"], c["destino_por"]) == ("TIPO_B", "TIPO_B", "sistema")
    assert c["turno_de"] == "Ana Pérez" and c["captura_id"] == 1 and c["n_imagenes"] == 5
    assert not c["editada"] and not c["eliminada"] and c["datos_origen"] is None
    ev = eventos(aud, "lote_recibido")[0]
    assert ev["detalle"]["caja_id"] == cid and ev["responsable"] == "sistema"


def test_una_revision_manual_deja_el_destino_pendiente(inv):
    c = inv.obtener(caja(inv, 1, ERR))
    assert (c["resultado"], c["destino"], c["destino_por"]) == (ERR, None, None)


def test_la_caja_y_su_evento_se_guardan_juntos_o_ninguno(inv, aud, monkeypatch):
    def roto(*a, **k):
        raise ErrorRegistro("disco lleno")
    monkeypatch.setattr(modulo_auditoria, "_insertar_evento", roto)
    with pytest.raises(ErrorRegistro):
        caja(inv, 1)
    assert inv.listar()["total"] == 0


def test_el_regente_decide_el_destino_de_una_revision_manual(inv, aud):
    cid = caja(inv, 7, ERR)
    r = inv.definir_destino(arranque=1, captura_id=7, destino="DESCARTE", responsable="Ana Pérez",
                            evento=("alarma_resuelta", "Ana Pérez", {"alarma_id": 3, "destino": "DESCARTE"}))
    assert r == cid
    c = inv.obtener(cid)
    assert (c["destino"], c["destino_por"], c["resultado"]) == ("DESCARTE", "Ana Pérez", ERR)
    assert eventos(aud, "alarma_resuelta")[0]["detalle"]["caja_id"] == cid
    assert [x["id"] for x in inv.listar(q="descarte")["cajas"]] == [cid]       # la búsqueda lo ve


def test_decidir_una_captura_que_no_tiene_caja_igual_deja_el_evento(inv, aud):
    cid = caja(inv, 1, ERR)                          # existe la captura 1 del arranque 1
    for captura, arranque in ((99, 1), (1, 2), (None, 1)):    # otra captura, otro arranque, sin lote
        r = inv.definir_destino(arranque=arranque, captura_id=captura, destino="TIPO_A", responsable="Ana",
                                evento=("alarma_resuelta", "Ana", {"alarma_id": 1}))
        assert r is None
    assert len(eventos(aud, "alarma_resuelta")) == 3
    assert all("caja_id" not in e["detalle"] for e in eventos(aud, "alarma_resuelta"))
    assert inv.obtener(cid)["destino"] is None       # y la caja existente no se tocó


def test_la_misma_captura_de_otro_arranque_no_se_confunde(inv):
    a = caja(inv, 1, ERR, arranque=1)
    b = caja(inv, 1, ERR, arranque=2)
    inv.definir_destino(arranque=2, captura_id=1, destino="TIPO_C", responsable="Ana", evento=("x", "Ana", {}))
    assert inv.obtener(a)["destino"] is None and inv.obtener(b)["destino"] == "TIPO_C"


# ---------------------------------------------------------------------------
# Edición
# ---------------------------------------------------------------------------
def test_editar_guarda_el_antes_y_el_despues_en_el_registro(inv, aud):
    cid = caja(inv, 1)
    c = inv.editar("Luis Gómez", cid, {"medicamento": "Acetaminofén", "gramaje": "500 mg",
                                         "numero_lote": "AB1234", "vencimiento": "2027-08-31", "notas": "caja golpeada"})
    assert (c["medicamento"], c["gramaje"], c["numero_lote"], c["vencimiento"]) == \
        ("Acetaminofén", "500 mg", "AB1234", "2027-08-31")
    assert c["editada"] and c["datos_origen"] == "manual" and c["dias_para_vencer"] == 309
    ev = eventos(aud, "caja_editada")[0]
    assert ev["responsable"] == "Luis Gómez" and ev["detalle"]["caja_id"] == cid
    assert ev["detalle"]["cambios"]["medicamento"] == [None, "Acetaminofén"]
    inv.editar("Luis Gómez", cid, {"gramaje": "650 mg"})
    assert eventos(aud, "caja_editada")[1]["detalle"]["cambios"] == {"gramaje": ["500 mg", "650 mg"]}


def test_editar_sin_cambios_no_escribe_ni_marca(inv, aud):
    cid = caja(inv, 1, medicamento="Ibuprofeno")
    antes = len(eventos(aud, "caja_editada"))
    inv.editar("Ana", cid, {"medicamento": "Ibuprofeno"})
    inv.editar("Ana", cid, {"medicamento": "  Ibuprofeno  "})           # los espacios no cuentan como cambio
    assert len(eventos(aud, "caja_editada")) == antes


def test_vaciar_un_campo_lo_borra(inv):
    cid = caja(inv, 1, medicamento="Ibuprofeno", notas="x")
    c = inv.editar("Ana", cid, {"medicamento": "", "notas": None})
    assert c["medicamento"] is None and c["notas"] is None


def test_los_espacios_raros_se_normalizan_salvo_en_las_notas(inv):
    cid = caja(inv, 1)
    c = inv.editar("Ana", cid, {"medicamento": "  Acetaminofén \t 500 \n", "notas": "  línea 1\nlínea 2  "})
    assert c["medicamento"] == "Acetaminofén 500" and c["notas"] == "línea 1\nlínea 2"


@pytest.mark.parametrize("campo", ["id", "resultado", "destino", "destino_por", "eliminada", "turno_de", "creada_ms",
                                   "busqueda", "datos_origen", "inventado"])
def test_los_campos_que_no_son_del_producto_no_se_editan(inv, campo):
    cid = caja(inv, 1)
    assert codigo(inv.editar, "Ana", cid, {campo: "x"}) == "campo_no_editable"
    assert inv.obtener(cid)["resultado"] == "TIPO_B"


@pytest.mark.parametrize("campo, valor", [
    ("medicamento", 5), ("medicamento", ["a"]), ("medicamento", "x" * 121), ("gramaje", "x" * 41),
    ("numero_lote", "x" * 41), ("notas", "x" * 501), ("medicamento", "con\x00nulo"), ("notas", "bell\x07"),
    ("vencimiento", "2026-13-01"), ("vencimiento", "31/12/2026"), ("vencimiento", "2026-2-3"),
    ("vencimiento", "1800-01-01"), ("vencimiento", "2300-01-01"), ("vencimiento", "mañana"), ("vencimiento", 20270801),
])
def test_valores_invalidos(inv, campo, valor):
    cid = caja(inv, 1)
    assert codigo(inv.editar, "Ana", cid, {campo: valor}) == "valor_invalido"
    assert inv.obtener(cid)[campo] is None


@pytest.mark.parametrize("cambios", [None, {}, [], "x"])
def test_editar_sin_nada_que_cambiar(inv, cambios):
    cid = caja(inv, 1)
    assert codigo(inv.editar, "Ana", cid, cambios) == "sin_cambios"


def test_editar_una_caja_inexistente_o_eliminada(inv):
    assert codigo(inv.editar, "Ana", 999, {"notas": "x"}) == "caja_inexistente"
    cid = caja(inv, 1)
    inv.eliminar("Ana", cid)
    assert codigo(inv.editar, "Ana", cid, {"notas": "x"}) == "caja_eliminada"


def test_una_edicion_sin_su_evento_no_ocurre(inv, monkeypatch):
    cid = caja(inv, 1, medicamento="Original")
    antes = inv.obtener(cid)
    def roto(*a, **k):
        raise ErrorRegistro("disco lleno")
    monkeypatch.setattr(modulo_auditoria, "_insertar_evento", roto)
    with pytest.raises(ErrorRegistro):
        inv.editar("Ana", cid, {"medicamento": "Cambiado", "notas": "otra cosa"})
    monkeypatch.undo()
    assert inv.obtener(cid) == antes                 # ni el medicamento ni las notas cambiaron


def test_la_busqueda_sigue_a_la_edicion(inv):
    cid = caja(inv, 1, medicamento="Ibuprofeno")
    inv.editar("Ana", cid, {"medicamento": "Naproxeno"})
    assert [c["id"] for c in inv.listar(q="naproxeno")["cajas"]] == [cid]
    assert inv.listar(q="ibuprofeno")["total"] == 0


# ---------------------------------------------------------------------------
# Eliminación lógica
# ---------------------------------------------------------------------------
def test_eliminar_oculta_la_caja_pero_la_conserva_y_deja_constancia(inv, aud):
    cid = caja(inv, 1, medicamento="Ibuprofeno", numero_lote="AB1234")
    c = inv.eliminar("Luis Gómez", cid, "duplicada por el sondeo")
    assert c["eliminada"] and c["eliminada_por"] == "Luis Gómez" and c["eliminada_motivo"] == "duplicada por el sondeo"
    assert inv.listar()["total"] == 0 and inv.listar(mostrar="eliminadas")["total"] == 1
    assert inv.listar(mostrar="todas")["total"] == 1
    ev = eventos(aud, "caja_eliminada")[0]
    assert ev["responsable"] == "Luis Gómez"
    assert ev["detalle"] == {"caja_id": cid, "medicamento": "Ibuprofeno", "numero_lote": "AB1234",
                             "captura_id": 1, "motivo": "duplicada por el sondeo"}
    assert codigo(inv.eliminar, "Ana", cid) == "ya_eliminada"


def test_restaurar(inv, aud):
    cid = caja(inv, 1)
    assert codigo(inv.restaurar, "Ana", cid) == "no_eliminada"
    inv.eliminar("Ana", cid, "error")
    c = inv.restaurar("Luis Gómez", cid)
    assert not c["eliminada"] and c["eliminada_por"] is None and c["eliminada_motivo"] is None
    assert inv.listar()["total"] == 1
    assert eventos(aud, "caja_restaurada")[0]["responsable"] == "Luis Gómez"
    assert codigo(inv.restaurar, "Ana", 999) == "caja_inexistente"


@pytest.mark.parametrize("motivo", [5, "x" * 201, "con\x00nulo"])
def test_motivo_invalido(inv, motivo):
    cid = caja(inv, 1)
    assert codigo(inv.eliminar, "Ana", cid, motivo) == "valor_invalido"
    assert not inv.obtener(cid)["eliminada"]


def test_eliminar_sin_motivo_es_valido(inv):
    assert inv.eliminar("Ana", caja(inv, 1))["eliminada_motivo"] is None
    assert codigo(inv.eliminar, "Ana", 999) == "caja_inexistente"


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
@pytest.fixture
def surtido(inv):
    return {
        "a": caja(inv, 1, "TIPO_A", medicamento="Acetaminofén", gramaje="500 mg", numero_lote="AB1234", vencimiento="2026-09-30"),
        "b": caja(inv, 2, "TIPO_B", medicamento="Ibuprofeno", gramaje="400 mg", numero_lote="IB-77", vencimiento="2026-11-15"),
        "c": caja(inv, 3, "TIPO_C", medicamento="Acetaminofén", gramaje="500 mg", numero_lote="AB1234", vencimiento="2026-09-30"),
        "d": caja(inv, 4, ERR, turno_de="Luis Gómez", medicamento="Amoxicilina", numero_lote="AM9", vencimiento="2027-03-01"),
        "e": caja(inv, 5, ERR, turno_de="Luis Gómez"),
    }


def ids(r):
    return [c["id"] for c in r["cajas"]]


def test_busqueda_sin_distinguir_mayusculas_ni_tildes(inv, surtido):
    s = surtido
    assert sorted(ids(inv.listar(q="acetaminofen"))) == sorted([s["a"], s["c"]])
    assert sorted(ids(inv.listar(q="ACETAMINOFÉN"))) == sorted([s["a"], s["c"]])
    assert ids(inv.listar(q="gomez")) == [s["e"], s["d"]]                      # el auxiliar también se busca
    assert ids(inv.listar(q="ib-77")) == [s["b"]]


def test_varias_palabras_deben_aparecer_todas(inv, surtido):
    s = surtido
    assert ids(inv.listar(q="acetaminofen ab1234 500")) == [s["c"], s["a"]]
    assert inv.listar(q="acetaminofen ib-77")["total"] == 0


def test_porcentaje_y_guion_bajo_se_buscan_como_texto(inv, surtido):
    inv.editar("Ana", surtido["b"], {"notas": "100% listo_ya"})
    assert ids(inv.listar(q="100%")) == [surtido["b"]]
    assert ids(inv.listar(q="listo_ya")) == [surtido["b"]]
    assert inv.listar(q="%")["total"] == 1 and inv.listar(q="_")["total"] >= 1


def test_filtro_por_clasificacion(inv, surtido):
    s = surtido
    assert ids(inv.listar(destino="TIPO_A")) == [s["a"]]
    assert ids(inv.listar(destino=DESTINO_PENDIENTE)) == [s["e"], s["d"]]
    assert ids(inv.listar(revision=True)) == [s["e"], s["d"]]
    inv.definir_destino(arranque=1, captura_id=4, destino="DESCARTE", responsable="Ana", evento=("x", "Ana", {}))
    assert ids(inv.listar(destino="DESCARTE")) == [s["d"]]
    assert ids(inv.listar(destino=DESTINO_PENDIENTE)) == [s["e"]]
    assert ids(inv.listar(revision=True)) == [s["e"], s["d"]]                  # sigue constando que pasó por revisión
    assert inv.destinos() == ["DESCARTE", "TIPO_A", "TIPO_B", "TIPO_C"]


def test_filtro_por_vencimiento(inv, surtido):
    s = surtido                                      # hoy = 2026-10-26
    assert sorted(ids(inv.listar(venc="vencidas"))) == sorted([s["a"], s["c"]])
    assert ids(inv.listar(venc="30")) == [s["b"]]                              # 15/11: dentro de 30 días
    assert ids(inv.listar(venc="90")) == [s["b"]]
    assert sorted(ids(inv.listar(venc="180"))) == sorted([s["b"], s["d"]])      # 01/03/2027 = 126 días
    assert ids(inv.listar(venc="sin")) == [s["e"]]


def test_vence_hoy_no_esta_vencida(inv):
    cid = caja(inv, 1, vencimiento="2026-10-26")
    assert inv.listar(venc="vencidas")["total"] == 0 and ids(inv.listar(venc="30")) == [cid]
    assert inv.obtener(cid)["dias_para_vencer"] == 0


def test_filtro_por_fecha_de_auditoria(inv, surtido):
    s = surtido
    t = {k: inv.obtener(v)["creada_ms"] for k, v in s.items()}
    assert ids(inv.listar(desde_ms=t["c"], hasta_ms=t["d"])) == [s["d"], s["c"]]
    assert ids(inv.listar(desde_ms=t["e"])) == [s["e"]]


def test_los_filtros_se_combinan(inv, surtido):
    s = surtido
    assert ids(inv.listar(q="acetaminofen", venc="vencidas", destino="TIPO_C")) == [s["c"]]
    inv.eliminar("Ana", s["c"])
    assert inv.listar(q="acetaminofen", destino="TIPO_C")["total"] == 0
    assert ids(inv.listar(q="acetaminofen", destino="TIPO_C", mostrar="eliminadas")) == [s["c"]]


@pytest.mark.parametrize("filtros", [{"mostrar": "todo"}, {"venc": "15"}, {"venc": "x; DROP TABLE cajas"}])
def test_filtros_invalidos(inv, filtros):
    assert codigo(inv.listar, **filtros) == "parametro_invalido"


# ---------------------------------------------------------------------------
# Orden y paginación
# ---------------------------------------------------------------------------
def test_por_defecto_lo_mas_reciente_primero(inv, surtido):
    assert ids(inv.listar()) == sorted(surtido.values(), reverse=True)


@pytest.mark.parametrize("orden", list(ORDENES))
@pytest.mark.parametrize("direccion", ["asc", "desc"])
def test_se_puede_ordenar_por_cada_columna(inv, surtido, orden, direccion):
    r = inv.listar(orden=orden, direccion=direccion)
    assert r["total"] == 5 and len(r["cajas"]) == 5


def test_orden_por_vencimiento_deja_los_sin_fecha_siempre_al_final(inv, surtido):
    s = surtido
    asc = ids(inv.listar(orden="vencimiento", direccion="asc"))
    desc = ids(inv.listar(orden="vencimiento", direccion="desc"))
    assert asc[:2] == [s["c"], s["a"]] and asc[-1] == s["e"]
    assert desc[0] == s["d"] and desc[-1] == s["e"]
    fechas = [c["vencimiento"] for c in inv.listar(orden="vencimiento", direccion="asc")["cajas"] if c["vencimiento"]]
    assert fechas == sorted(fechas) and len(fechas) == 4


@pytest.mark.parametrize("orden", ["medicamento", "gramaje", "numero_lote", "turno_de", "destino", "vencimiento"])
@pytest.mark.parametrize("direccion", ["asc", "desc"])
def test_lo_vacio_va_siempre_al_final_en_cualquier_columna_y_sentido(inv, orden, direccion):
    sin_datos = caja(inv, 1, ERR, turno_de=None)                    # destino y turno vacíos, sin datos extraídos
    con_datos = caja(inv, 2, "TIPO_A", turno_de="Ana", medicamento="Ibuprofeno", gramaje="400 mg",
                     numero_lote="IB-1", vencimiento="2027-01-01")
    otra = caja(inv, 3, "TIPO_B", turno_de="Zoe", medicamento="Zinc", gramaje="50 mg",
                numero_lote="ZN-1", vencimiento="2028-01-01")
    r = ids(inv.listar(orden=orden, direccion=direccion))
    assert r[-1] == sin_datos and set(r[:2]) == {con_datos, otra}


def test_orden_por_texto_ignora_mayusculas(inv):
    caja(inv, 1, medicamento="zinc")
    caja(inv, 2, medicamento="Amoxicilina")
    caja(inv, 3, medicamento="bismuto")
    assert [c["medicamento"] for c in inv.listar(orden="medicamento", direccion="asc")["cajas"]] == \
        ["Amoxicilina", "bismuto", "zinc"]


@pytest.mark.parametrize("orden, direccion", [("inventado", "asc"), ("id; DROP TABLE cajas", "asc"),
                                              ("id", "ascendente"), ("id", "asc; DROP TABLE cajas"), ("", "asc"), ("id", "")])
def test_orden_y_direccion_fuera_de_la_lista_blanca(inv, surtido, orden, direccion):
    assert codigo(inv.listar, orden=orden, direccion=direccion) == "parametro_invalido"
    assert inv.listar()["total"] == 5


def test_el_texto_buscado_nunca_se_ejecuta_como_sql(inv, surtido):
    for ataque in ("'; DROP TABLE cajas; --", "' OR '1'='1", "%' UNION SELECT * FROM usuarios --"):
        assert inv.listar(q=ataque)["total"] == 0
    assert inv.listar()["total"] == 5


def test_paginacion(inv):
    for n in range(1, 8):
        caja(inv, n)
    p1 = inv.listar(limite=3)
    assert (p1["total"], len(p1["cajas"]), p1["desplazamiento"]) == (7, 3, 0)
    p2 = inv.listar(limite=3, desplazamiento=3)
    p3 = inv.listar(limite=3, desplazamiento=6)
    assert [c["id"] for c in p1["cajas"] + p2["cajas"] + p3["cajas"]] == [7, 6, 5, 4, 3, 2, 1]
    assert inv.listar(limite=3, desplazamiento=99)["cajas"] == []
    assert inv.listar(limite=0)["limite"] == 1 and inv.listar(limite=10_000)["limite"] == LIMITE_MAXIMO
    assert inv.listar(desplazamiento=-5)["desplazamiento"] == 0
    assert codigo(inv.listar, limite="mucho") == "parametro_invalido"


# ---------------------------------------------------------------------------
# Agrupación
# ---------------------------------------------------------------------------
def test_agrupado_por_medicamento_y_lote_con_lo_mas_proximo_primero(inv, surtido):
    r = inv.agrupar()
    claves = [(g["medicamento"], g["numero_lote"], g["vencimiento"], g["cajas"]) for g in r["grupos"]]
    assert claves == [
        ("Acetaminofén", "AB1234", "2026-09-30", 2),
        ("Ibuprofeno", "IB-77", "2026-11-15", 1),
        ("Amoxicilina", "AM9", "2027-03-01", 1),
        (None, None, None, 1),                                  # la caja sin datos al final
    ]
    assert r["total"] == 4 and r["grupos"][0]["dias_para_vencer"] == -26 and r["grupos"][-1]["dias_para_vencer"] is None


def test_el_agrupado_respeta_los_filtros(inv, surtido):
    assert [g["medicamento"] for g in inv.agrupar(venc="vencidas")["grupos"]] == ["Acetaminofén"]
    assert inv.agrupar(q="nada que ver")["total"] == 0
    inv.eliminar("Ana", surtido["b"])
    assert "Ibuprofeno" not in [g["medicamento"] for g in inv.agrupar()["grupos"]]


def test_agrupado_pagina(inv, surtido):
    r = inv.agrupar(limite=2)
    assert len(r["grupos"]) == 2 and r["total"] == 4
    assert len(inv.agrupar(limite=2, desplazamiento=2)["grupos"]) == 2


# ---------------------------------------------------------------------------
# Persistencia y trazabilidad
# ---------------------------------------------------------------------------
def test_las_cajas_sobreviven_a_un_reinicio(tmp_path):
    ruta = tmp_path / "r.db"
    a = Auditoria(ruta)
    i = Inventario(a, hoy=lambda: HOY)
    cid = caja(i, 1, medicamento="Ibuprofeno")
    a.cerrar()
    b = Auditoria(ruta)
    j = Inventario(b, hoy=lambda: HOY)
    assert j.obtener(cid)["medicamento"] == "Ibuprofeno" and j.listar()["total"] == 1
    assert caja(j, 1, arranque=2) == cid + 1


def test_el_registro_sigue_integro_tras_toda_la_actividad(inv, aud, surtido):
    inv.editar("Ana", surtido["a"], {"notas": "x"})
    inv.eliminar("Ana", surtido["b"], "prueba")
    inv.restaurar("Ana", surtido["b"])
    assert aud.verificar()["ok"]


def test_normalizar():
    assert normalizar("  ACETAMINOFÉN   500  ") == "acetaminofen 500"
    assert normalizar(None) == "none" and normalizar(123) == "123"
    assert set(CAMPOS_EDITABLES) == {"medicamento", "gramaje", "numero_lote", "vencimiento", "notas"}
