"""Exportación a CSV del inventario y del registro (PC-23, parte E). Correr desde pc/:
    python3 -m pytest tests/test_exportar.py
"""
import csv
import io
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import exportar  # noqa: E402
from test_inventario_panel import api, auxiliar, con_sesion, hash_rapido, poblar  # noqa: E402,F401


def descargar(api, ruta, cliente=None):
    """(estado, cabeceras, texto): el CSV no es JSON, así que no sirve Cliente.get."""
    req = urllib.request.Request(api.base + ruta)
    if cliente is not None and cliente.cookie:
        req.add_header("Cookie", cliente.cookie)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def filas(crudo: bytes):
    assert crudo.startswith(b"\xef\xbb\xbf")                      # BOM: Excel entiende las tildes
    return list(csv.reader(io.StringIO(crudo.decode("utf-8-sig"), newline=""), delimiter=";"))


# ---------------------------------------------------------------------------
# El formato
# ---------------------------------------------------------------------------
def test_las_celdas_que_empiezan_como_formula_se_neutralizan():
    for malo in ("=SUMA(A1)", "+1", "-1", "@x", "\tx"):
        assert exportar._celda(malo) == "'" + malo
    assert exportar._celda("Ibuprofeno") == "Ibuprofeno" and exportar._celda(None) == ""
    assert exportar._celda(True) == "sí" and exportar._celda(0.95) == "0.95"


def test_el_csv_trae_encabezados_en_espanol_y_conserva_tildes():
    crudo = exportar.a_csv((("a", "Acetaminofén"), ("b", "Notas")), [{"a": "1", "b": "=2+2"}])
    assert filas(crudo) == [["Acetaminofén", "Notas"], ["1", "'=2+2"]]


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------
def test_exportar_exige_turno(api):
    poblar(api)
    estado, _, cuerpo = descargar(api, "/api/inventario/exportar.csv")
    assert estado == 401 and b"sesion_requerida" in cuerpo
    assert descargar(api, "/api/log/exportar.csv")[0] == 401
    assert [e for e in api.aud.listar(limite=500)["eventos"] if e["tipo"] == "exportacion"] == []


def test_inventario_exportado_trae_todas_las_cajas_y_respeta_los_filtros(api):
    poblar(api)
    c = con_sesion(api)
    estado, cab, crudo = descargar(api, "/api/inventario/exportar.csv", c)
    assert estado == 200 and cab["Content-Type"].startswith("text/csv")
    assert 'attachment; filename="smartpharma_inventario_' in cab["Content-Disposition"]
    todas = filas(crudo)
    assert len(todas) == 1 + 4 and todas[0][0] == "Caja" and "Medicamento" in todas[0]
    assert {f[todas[0].index("Medicamento")] for f in todas[1:]} == {"Acetaminofén", "Ibuprofeno", "Amoxicilina", ""}
    solo = filas(descargar(api, "/api/inventario/exportar.csv?q=ibuprofeno", c)[2])
    assert len(solo) == 2 and solo[1][todas[0].index("Medicamento")] == "Ibuprofeno"


def test_la_exportacion_no_se_pagina(api):
    for _ in range(60):                                           # más que la página de 50 de la lista
        api.estado.registrar_lote("x", [b"a"] * 5, "TIPO_A")
    c = con_sesion(api)
    assert len(filas(descargar(api, "/api/inventario/exportar.csv?limite=3", c)[2])) == 1 + 60


def test_un_nombre_malicioso_no_se_exporta_como_formula(api):
    api.estado.registrar_lote("x", [b"a"] * 5, "TIPO_A")
    api.inv.editar("Root Admin", 1, {"medicamento": "=HYPERLINK(\"http://x\")"})
    todas = filas(descargar(api, "/api/inventario/exportar.csv", con_sesion(api))[2])
    assert todas[1][todas[0].index("Medicamento")].startswith("'=")


def test_registro_exportado_va_en_orden_de_la_cadena_con_su_sello(api):
    poblar(api)
    c = con_sesion(api)
    estado, _, crudo = descargar(api, "/api/log/exportar.csv", c)
    todas = filas(crudo)
    assert estado == 200 and todas[0] == ["#", "Fecha y hora", "Evento", "Responsable", "Detalle (JSON)", "Sello (hash)"]
    ids = [int(f[0]) for f in todas[1:]]
    assert ids == sorted(ids) and all(len(f[-1]) == 64 for f in todas[1:])
    solo = filas(descargar(api, "/api/log/exportar.csv?tipo=lote_recibido", c)[2])
    assert len(solo) == 1 + 4 and {f[2] for f in solo[1:]} == {"lote_recibido"}


def test_cada_exportacion_queda_en_el_registro_con_quien_y_cuantas_filas(api):
    poblar(api)
    c = con_sesion(api)
    descargar(api, "/api/inventario/exportar.csv?q=ibuprofeno", c)
    descargar(api, "/api/log/exportar.csv?tipo=lote_recibido", c)
    ev = [e for e in reversed(api.aud.listar(limite=500)["eventos"]) if e["tipo"] == "exportacion"]
    assert [(e["responsable"], e["detalle"]["que"], e["detalle"]["filas"]) for e in ev] == [
        ("Root Admin", "inventario", 1), ("Root Admin", "registro", 4)]
    assert ev[0]["detalle"]["filtros"] == {"q": "ibuprofeno", "mostrar": "activas"}
    assert api.aud.verificar()["ok"]


def test_un_auxiliar_tambien_puede_exportar_y_un_parametro_malo_da_400(api):
    poblar(api)
    c = con_sesion(api, auxiliar(api))
    assert descargar(api, "/api/inventario/exportar.csv", c)[0] == 200
    assert descargar(api, "/api/inventario/exportar.csv?desde=abc", c)[0] == 400
    assert descargar(api, "/api/inventario/exportar.csv?orden=x", c)[0] == 400
    assert descargar(api, "/api/log/exportar.csv?hasta=zz", c)[0] == 400
