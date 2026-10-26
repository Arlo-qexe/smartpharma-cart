"""Pruebas de la Parte D: conexión del módulo de IA (ocr_interface.py), datos leídos en el inventario y
flujo completo por TCP. La prueba con el modelo real es opcional (PC_PRUEBAS_IA=1). Correr desde pc/:
    python3 -m pytest tests/
"""
import itertools
import io
import os
import socket
import sqlite3
import sys
import threading
import time
import types
import zipfile
from datetime import date
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import ocr_interface as oi  # noqa: E402
import server  # noqa: E402
from auditoria import Auditoria  # noqa: E402
from estado_panel import EstadoPanel  # noqa: E402
from inventario import Inventario  # noqa: E402
from mock_orangepi_client import enviar_lote_de_prueba  # noqa: E402
from ocr_interface import (  # noqa: E402
    ReconocimientoNoDisponible,
    ResultadoReconocimiento,
    modo_reconocimiento,
    preparar,
    procesar_lote_ocr,
    reconocer_lote,
)

ERR = "ERROR_REVISION_MANUAL"
HOY = date(2026, 10, 26)


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for v in ("PC_RECONOCIMIENTO", "IA_MODELO", "YOLO_AUTOINSTALL", "YOLO_OFFLINE", "YOLO_VERBOSE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(oi, "_ia", None)


def resultado_ia(**cambios):
    """Un ResultadoCaja de la IA (el tipo real del módulo), para simular lo que devolvería."""
    if str(oi.CARPETA_IA) not in sys.path:
        sys.path.append(str(oi.CARPETA_IA))
    from smartpharma_ia.tipos import ResultadoCaja
    base = dict(clasificacion="TIPO_B", nombre="Acetaminofén", gramaje="500 mg", lote="AB1234",
                vencimiento=date(2027, 8, 31), confianza=0.9731, dias_para_vencer=309)
    base.update(cambios)
    return ResultadoCaja(**base)


class IAFalsa:
    """Lo que haría el adaptador de la IA, sin cargar ningún modelo."""

    def __init__(self, respuesta=None, error=None, demora=0.0):
        self.respuesta, self.error, self.demora = respuesta or resultado_ia(), error, demora
        self.llamadas = []
        self.simultaneas = 0
        self.max_simultaneas = 0
        self._lock = threading.Lock()

    def procesar_lote_detallado(self, imagenes):
        with self._lock:
            self.simultaneas += 1
            self.max_simultaneas = max(self.max_simultaneas, self.simultaneas)
        try:
            self.llamadas.append(list(imagenes))
            time.sleep(self.demora)
            if self.error:
                raise self.error
            return self.respuesta
        finally:
            with self._lock:
                self.simultaneas -= 1


@pytest.fixture
def ia(monkeypatch):
    falsa = IAFalsa()
    monkeypatch.setenv("PC_RECONOCIMIENTO", "ia")
    monkeypatch.setattr(oi, "_adaptador", lambda: falsa)
    return falsa


# ---------------------------------------------------------------------------
# Modo
# ---------------------------------------------------------------------------
def test_por_defecto_es_el_placeholder():
    assert modo_reconocimiento() == "placeholder"


@pytest.mark.parametrize("valor, esperado", [("ia", "ia"), ("IA", "ia"), (" ia ", "ia"), ("Placeholder", "placeholder"), ("", "placeholder")])
def test_modos_validos(monkeypatch, valor, esperado):
    monkeypatch.setenv("PC_RECONOCIMIENTO", valor)
    assert modo_reconocimiento() == esperado


@pytest.mark.parametrize("valor", ["magia", "true", "1", "openai"])
def test_un_modo_desconocido_es_un_error_y_no_se_cae_al_placeholder(monkeypatch, valor):
    monkeypatch.setenv("PC_RECONOCIMIENTO", valor)
    with pytest.raises(ReconocimientoNoDisponible, match="no es válido"):
        modo_reconocimiento()


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------
def test_el_placeholder_clasifica_al_azar_y_no_lee_datos():
    vistos = {reconocer_lote([b"x"] * 5).clasificacion for _ in range(200)}
    assert vistos == set(oi.TIPOS_PLACEHOLDER)
    r = reconocer_lote([b"x"] * 5)
    assert (r.origen, r.tiene_datos, r.medicamento, r.numero_lote, r.vencimiento, r.confianza) == ("placeholder", False, None, None, None, None)


def test_un_lote_vacio_nunca_se_reconoce(monkeypatch, ia):
    for modo in ("placeholder", "ia"):
        monkeypatch.setenv("PC_RECONOCIMIENTO", modo)
        r = reconocer_lote([])
        assert (r.clasificacion, r.motivo, r.origen) == (ERR, "lote_vacio", modo)
    assert ia.llamadas == []


def test_el_contrato_vigente_sigue_devolviendo_solo_el_destino(ia):
    assert procesar_lote_ocr([b"x"] * 5) == "TIPO_B"
    assert procesar_lote_ocr([]) == ERR


# ---------------------------------------------------------------------------
# Modo IA
# ---------------------------------------------------------------------------
def test_el_resultado_de_la_ia_se_convierte_al_formato_de_la_pc(ia):
    r = reconocer_lote([b"x"] * 5)
    assert (r.clasificacion, r.medicamento, r.gramaje, r.numero_lote) == ("TIPO_B", "Acetaminofén", "500 mg", "AB1234")
    assert (r.vencimiento, r.confianza, r.origen, r.motivo) == ("2027-08-31", 0.9731, "ia", None)
    assert r.segundos is not None and r.segundos >= 0 and r.tiene_datos
    assert ia.llamadas == [[b"x"] * 5]


def test_una_revision_manual_conserva_su_motivo_y_no_inventa_confianza(ia):
    ia.respuesta = resultado_ia(clasificacion=ERR, motivo="lote:ausente;vencimiento:ausente", nombre=None, gramaje=None,
                                lote=None, vencimiento=None, confianza=0.8, dias_para_vencer=None)
    r = reconocer_lote([b"x"] * 5)
    assert (r.clasificacion, r.motivo, r.confianza, r.tiene_datos) == (ERR, "lote:ausente;vencimiento:ausente", None, False)


def test_si_la_ia_falla_el_lote_va_a_revision_manual_sin_lanzar_nada(ia, caplog):
    ia.error = RuntimeError("el motor se cayó")
    r = reconocer_lote([b"x"] * 5)
    assert (r.clasificacion, r.motivo, r.origen) == (ERR, "fallo_reconocimiento", "ia")
    assert "fallo inesperado en el reconocimiento" in caplog.text


def test_un_solo_lote_a_la_vez(ia):
    ia.demora = 0.15
    hilos = [threading.Thread(target=reconocer_lote, args=([b"x"] * 5,)) for _ in range(4)]
    inicio = time.monotonic()
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert ia.max_simultaneas == 1 and len(ia.llamadas) == 4 and time.monotonic() - inicio >= 0.55


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
def test_preparar_en_placeholder_no_carga_nada(monkeypatch):
    monkeypatch.setattr(oi, "_adaptador", lambda: pytest.fail("no debe cargar el módulo de IA"))
    assert "placeholder" in preparar()


def test_preparar_en_ia_hace_una_lectura_de_prueba_con_un_jpeg_valido(monkeypatch, ia, tmp_path):
    pesos = tmp_path / "best.onnx"
    pesos.write_bytes(b"x")
    monkeypatch.setenv("IA_MODELO", str(pesos))
    descripcion = preparar()
    assert descripcion.startswith("ia (best.onnx)")
    assert len(ia.llamadas) == 1 and len(ia.llamadas[0]) == 1
    from PIL import Image
    assert Image.open(io.BytesIO(ia.llamadas[0][0])).size == (640, 480)


def test_sin_los_pesos_por_defecto_no_arranca_y_dice_que_hacer(monkeypatch, ia, tmp_path):
    monkeypatch.setattr(oi, "MODELO_DEFECTO", tmp_path / "no_existe.onnx")
    with pytest.raises(ReconocimientoNoDisponible, match="faltan los pesos"):
        preparar()


def test_pesos_indicados_que_no_existen(monkeypatch, ia, tmp_path):
    monkeypatch.setenv("IA_MODELO", str(tmp_path / "otro.onnx"))
    with pytest.raises(ReconocimientoNoDisponible, match="no existe"):
        preparar()


def test_ia_modelo_vacio_pide_leer_la_foto_completa_sin_detector(monkeypatch, ia):
    monkeypatch.setenv("IA_MODELO", "")
    descripcion = preparar()
    assert "sin detector" in descripcion and "IA_MODELO" not in os.environ


def test_si_el_modulo_no_arranca_se_dice_por_que(monkeypatch, tmp_path):
    pesos = tmp_path / "best.onnx"
    pesos.write_bytes(b"x")
    monkeypatch.setenv("PC_RECONOCIMIENTO", "ia")
    monkeypatch.setenv("IA_MODELO", str(pesos))
    monkeypatch.setattr(oi, "_adaptador", lambda: IAFalsa(error=ImportError("No module named 'ultralytics'")))
    with pytest.raises(ReconocimientoNoDisponible, match="no arrancó.*ultralytics"):
        preparar()


def test_si_falta_la_carpeta_de_ia(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "CARPETA_IA", tmp_path / "no_hay_ia")
    with pytest.raises(ReconocimientoNoDisponible, match="no existe la carpeta"):
        oi._adaptador()


def test_el_detector_no_instala_paquetes_ni_envia_nada_a_internet(monkeypatch, tmp_path):
    """Ultralytics, por defecto, instala lo que le falta y manda eventos de uso: la PC no lo permite."""
    paquete = tmp_path / "smartpharma_ia"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    (paquete / "adaptador_pc.py").write_text("def procesar_lote_detallado(i): return None\n")
    monkeypatch.setattr(oi, "CARPETA_IA", tmp_path)
    antes = {k: v for k, v in sys.modules.items() if k.startswith("smartpharma_ia")}
    for k in antes:
        del sys.modules[k]
    try:
        oi._adaptador()
        assert (os.environ["YOLO_AUTOINSTALL"], os.environ["YOLO_OFFLINE"], os.environ["YOLO_VERBOSE"]) == ("False", "True", "False")
    finally:
        for k in [k for k in sys.modules if k.startswith("smartpharma_ia")]:
            del sys.modules[k]
        sys.modules.update(antes)
        sys.path[:] = [p for p in sys.path if p != str(tmp_path)]
        for v in ("YOLO_AUTOINSTALL", "YOLO_OFFLINE", "YOLO_VERBOSE"):
            os.environ.pop(v, None)


def test_lo_que_el_usuario_defina_de_ultralytics_se_respeta(monkeypatch, tmp_path):
    paquete = tmp_path / "smartpharma_ia"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    (paquete / "adaptador_pc.py").write_text("")
    monkeypatch.setattr(oi, "CARPETA_IA", tmp_path)
    monkeypatch.setenv("YOLO_OFFLINE", "False")
    antes = {k: v for k, v in sys.modules.items() if k.startswith("smartpharma_ia")}
    for k in antes:
        del sys.modules[k]
    try:
        oi._adaptador()
        assert os.environ["YOLO_OFFLINE"] == "False"
    finally:
        for k in [k for k in sys.modules if k.startswith("smartpharma_ia")]:
            del sys.modules[k]
        sys.modules.update(antes)
        sys.path[:] = [p for p in sys.path if p != str(tmp_path)]
        for v in ("YOLO_AUTOINSTALL", "YOLO_VERBOSE"):
            os.environ.pop(v, None)


# ---------------------------------------------------------------------------
# Inventario: datos leídos
# ---------------------------------------------------------------------------
@pytest.fixture
def inv():
    aud = Auditoria(":memory:")
    reloj = itertools.count(1_000_000, 1000)
    yield Inventario(aud, hoy=lambda: HOY, ahora_ms=lambda: next(reloj)), aud
    aud.cerrar()


def alta(i, n, datos, resultado="TIPO_B"):
    return i.registrar_caja(arranque=1, captura_id=n, origen="192.168.0.5", n_imagenes=5, resultado=resultado,
                            datos=datos, evento=("lote_recibido", "sistema", {"lote_id": n}))


def test_la_caja_nace_con_lo_que_leyo_la_ia(inv):
    i, aud = inv
    cid = alta(i, 1, dict(medicamento="Acetaminofén", gramaje="500 mg", numero_lote="AB1234",
                          vencimiento="2027-08-31", confianza=0.9731, motivo=None, segundos=4.2))
    c = i.obtener(cid)
    assert (c["medicamento"], c["gramaje"], c["numero_lote"], c["vencimiento"]) == ("Acetaminofén", "500 mg", "AB1234", "2027-08-31")
    assert (c["datos_origen"], c["confianza"], c["motivo_ocr"], c["editada"]) == ("ocr", 0.9731, None, False)
    assert c["dias_para_vencer"] == 309
    assert [x["id"] for x in i.listar(q="acetaminofen ab1234")["cajas"]] == [cid]


def test_una_correccion_manual_cambia_el_origen_y_deja_el_antes_y_el_despues(inv):
    i, aud = inv
    cid = alta(i, 1, dict(numero_lote="02009", vencimiento="2028-08-31", confianza=0.89))
    c = i.editar("Ana", cid, {"numero_lote": "F02009A"})
    assert (c["numero_lote"], c["datos_origen"], c["editada"], c["confianza"]) == ("F02009A", "manual", True, 0.89)
    ev = [e for e in aud.listar()["eventos"] if e["tipo"] == "caja_editada"][0]
    assert ev["detalle"]["cambios"] == {"numero_lote": ["02009", "F02009A"]}


def test_una_revision_manual_guarda_el_motivo_y_se_puede_buscar(inv):
    i, _ = inv
    cid = alta(i, 1, dict(motivo="lote:ausente;vencimiento:ausente", confianza=None), resultado=ERR)
    c = i.obtener(cid)
    assert (c["datos_origen"], c["destino"], c["motivo_ocr"]) == (None, None, "lote:ausente;vencimiento:ausente")
    assert [x["id"] for x in i.listar(q="ausente")["cajas"]] == [cid]


def test_el_texto_sucio_del_ocr_se_limpia_y_se_recorta_en_vez_de_rechazarse(inv):
    i, _ = inv
    cid = alta(i, 1, dict(medicamento="  Aceta\x00min\nofén\t " + "x" * 300, gramaje="y" * 100, numero_lote="AB\x07 12", vencimiento="2027-08-31"))
    c = i.obtener(cid)
    assert c["medicamento"].startswith("Aceta min ofén xxx") and len(c["medicamento"]) == 120
    assert len(c["gramaje"]) == 40 and c["numero_lote"] == "AB 12"


@pytest.mark.parametrize("fecha", ["31/08/2027", "2027-13-01", "2027-8-1", "1800-01-01", "2300-01-01", "mañana", 20270831, None, ""])
def test_una_fecha_imposible_no_se_guarda(inv, fecha):
    i, _ = inv
    c = i.obtener(alta(i, 1, dict(numero_lote="AB1234", vencimiento=fecha)))
    assert c["vencimiento"] is None and c["numero_lote"] == "AB1234"


@pytest.mark.parametrize("confianza", [-0.1, 1.5, "alta", True, None, float("nan")])
def test_una_confianza_fuera_de_rango_no_se_guarda(inv, confianza):
    i, _ = inv
    assert i.obtener(alta(i, 1, dict(numero_lote="AB1234", confianza=confianza)))["confianza"] is None


@pytest.mark.parametrize("datos", [None, {}, {"medicamento": None, "numero_lote": ""}, {"vencimiento": "basura"}])
def test_sin_datos_utiles_la_caja_queda_vacia_y_sin_origen(inv, datos):
    i, _ = inv
    c = i.obtener(alta(i, 1, datos))
    assert (c["medicamento"], c["numero_lote"], c["vencimiento"], c["datos_origen"]) == (None, None, None, None)


def test_una_tabla_creada_antes_de_la_parte_d_se_migra_sin_perder_filas(tmp_path):
    import inventario as modulo
    # La definición ANTERIOR de la tabla: la actual sin las dos columnas que añadió la parte D.
    import re
    antigua = re.sub(r",\s*confianza\s+REAL,\s*motivo_ocr\s+TEXT", "", modulo._ESQUEMA)
    assert "confianza" not in antigua and antigua != modulo._ESQUEMA
    ruta = tmp_path / "vieja.db"
    Auditoria(ruta).cerrar()                                       # crea la tabla de eventos
    con = sqlite3.connect(ruta)
    con.executescript(antigua + "INSERT INTO cajas (creada_ms, n_imagenes, resultado, destino, busqueda) "
                                "VALUES (5, 5, 'TIPO_A', 'TIPO_A', 'vieja');")
    assert {f[1] for f in con.execute("PRAGMA table_info(cajas)")} >= {"id", "busqueda"}
    assert not {"confianza", "motivo_ocr"} & {f[1] for f in con.execute("PRAGMA table_info(cajas)")}
    con.commit()
    con.close()
    b = Auditoria(ruta)
    i = Inventario(b, hoy=lambda: HOY)                            # migra
    vieja = i.obtener(1)
    assert vieja["resultado"] == "TIPO_A" and vieja["confianza"] is None and vieja["motivo_ocr"] is None
    assert alta(i, 7, dict(numero_lote="AB1234", confianza=0.9)) == 2
    assert i.obtener(2)["confianza"] == 0.9
    Inventario(b)                                                 # volver a abrir no falla ni duplica columnas
    columnas = [f[1] for f in b.leer("PRAGMA table_info(cajas)")]
    assert columnas.count("confianza") == 1 and columnas.count("motivo_ocr") == 1


# ---------------------------------------------------------------------------
# Estado: la lectura llega a la caja, al evento y a la alarma
# ---------------------------------------------------------------------------
@pytest.fixture
def estado_con_inventario(inv):
    i, aud = inv
    e = EstadoPanel()
    e.conectar_auditoria(aud)
    e.conectar_inventario(i)
    e.registrar_arranque(base=":memory:")
    return e, i, aud


def test_el_lote_reconocido_por_la_ia_llena_la_caja_y_queda_en_el_registro(estado_con_inventario):
    e, i, aud = estado_con_inventario
    datos = ResultadoReconocimiento("TIPO_B", medicamento="Acetaminofén", gramaje="500 mg", numero_lote="AB1234",
                                    vencimiento="2027-08-31", confianza=0.97, origen="ia", segundos=4.2)
    e.registrar_lote("192.168.0.5", [b"a"] * 5, "TIPO_B", datos=datos)
    c = i.listar()["cajas"][0]
    assert (c["medicamento"], c["numero_lote"], c["vencimiento"], c["datos_origen"]) == ("Acetaminofén", "AB1234", "2027-08-31", "ocr")
    ev = [x for x in aud.listar()["eventos"] if x["tipo"] == "lote_recibido"][0]
    assert ev["detalle"]["lectura"] == {"medicamento": "Acetaminofén", "gramaje": "500 mg", "numero_lote": "AB1234",
                                        "vencimiento": "2027-08-31", "confianza": 0.97, "segundos": 4.2}
    assert ev["detalle"]["caja_id"] == c["id"]


def test_el_placeholder_no_llena_ningun_dato(estado_con_inventario):
    e, i, aud = estado_con_inventario
    e.registrar_lote("x", [b"a"] * 5, "TIPO_A", datos=ResultadoReconocimiento("TIPO_A"))
    e.registrar_lote("x", [b"a"] * 5, "TIPO_A")
    assert all(c["datos_origen"] is None and c["numero_lote"] is None for c in i.listar()["cajas"])
    assert all("lectura" not in x["detalle"] for x in aud.listar()["eventos"] if x["tipo"] == "lote_recibido")


def test_el_motivo_de_la_ia_llega_a_la_caja_y_a_la_alarma(estado_con_inventario):
    e, i, aud = estado_con_inventario
    datos = ResultadoReconocimiento(ERR, motivo="lote:ausente", origen="ia")
    lote = e.registrar_lote("x", [b"a"] * 5, ERR, datos=datos)
    alarma = e.registrar_alarma("sin_consenso_ocr", lote, "lote:ausente")
    assert i.listar()["cajas"][0]["motivo_ocr"] == "lote:ausente"
    assert [a["detalle"] for a in e.snapshot()["alarmas"]] == ["lote:ausente"]
    assert [x for x in aud.listar()["eventos"] if x["tipo"] == "alarma_abierta"][0]["detalle"]["detalle"] == "lote:ausente"
    e.abrir_decision(alarma)
    e.resolver_alarma(alarma, "DESCARTE", responsable="Ana")
    c = i.listar()["cajas"][0]
    assert (c["destino"], c["destino_por"], c["motivo_ocr"]) == ("DESCARTE", "Ana", "lote:ausente")


def test_una_alarma_sin_detalle_sigue_igual(estado_con_inventario):
    e, _, aud = estado_con_inventario
    e.registrar_alarma("fallo_captura", 1)
    assert e.snapshot()["alarmas"][0]["detalle"] is None
    assert "detalle" not in [x for x in aud.listar()["eventos"] if x["tipo"] == "alarma_abierta"][0]["detalle"]


# ---------------------------------------------------------------------------
# De punta a punta por TCP (la Orange Pi envía, la PC reconoce, el inventario se llena)
# ---------------------------------------------------------------------------
@pytest.fixture
def servidor_con_ia(monkeypatch):
    aud = Auditoria(":memory:")
    inv = Inventario(aud, hoy=lambda: HOY)
    server.estado.conectar_auditoria(aud)
    server.estado.conectar_inventario(inv)
    server.estado.registrar_arranque(base=":memory:")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    threading.Thread(target=server.iniciar_servidor, args=(puerto,), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", puerto), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield types.SimpleNamespace(puerto=puerto, inv=inv, aud=aud)
    server.estado._auditoria = server.estado._inventario = server.estado._arranque = None
    aud.cerrar()


def test_un_lote_enviado_por_tcp_llena_el_inventario_con_lo_que_lee_la_ia(servidor_con_ia, monkeypatch, capsys):
    monkeypatch.setattr(server, "reconocer_lote", lambda imagenes: ResultadoReconocimiento(
        "TIPO_C", medicamento="Amoxicilina", gramaje="500mg", numero_lote="60925", vencimiento="2028-10-31",
        confianza=0.95, origen="ia", segundos=3.0))
    enviar_lote_de_prueba(puerto=servidor_con_ia.puerto, num_imagenes=5, tamano_bytes=100)
    assert "TIPO_C" in capsys.readouterr().out                                  # la Orange Pi recibe el destino
    c = servidor_con_ia.inv.listar()["cajas"][0]
    assert (c["numero_lote"], c["vencimiento"], c["destino"], c["datos_origen"]) == ("60925", "2028-10-31", "TIPO_C", "ocr")


def test_una_revision_manual_por_tcp_abre_la_alarma_con_el_motivo(servidor_con_ia, monkeypatch, capsys):
    monkeypatch.setattr(server, "reconocer_lote", lambda imagenes: ResultadoReconocimiento(
        ERR, motivo="lote:ausente;vencimiento:ausente", origen="ia", segundos=5.0))
    enviar_lote_de_prueba(puerto=servidor_con_ia.puerto, num_imagenes=5, tamano_bytes=100)
    assert ERR in capsys.readouterr().out
    c = servidor_con_ia.inv.listar()["cajas"][0]
    assert (c["destino"], c["motivo_ocr"]) == (None, "lote:ausente;vencimiento:ausente")
    ultima = server.estado.snapshot()["alarmas"][0]
    assert ultima["motivo"] == "sin_consenso_ocr" and ultima["detalle"] == "lote:ausente;vencimiento:ausente"


def test_un_lote_vacio_por_tcp_sigue_siendo_fallo_de_captura(servidor_con_ia, monkeypatch, capsys):
    monkeypatch.setattr(server, "reconocer_lote", lambda imagenes: pytest.fail("un lote vacío no se reconoce"))
    from mock_orangepi_client import enviar_lote_vacio
    enviar_lote_vacio(puerto=servidor_con_ia.puerto)
    assert ERR in capsys.readouterr().out
    assert server.estado.snapshot()["alarmas"][0]["motivo"] == "fallo_captura"


# ---------------------------------------------------------------------------
# Con el modelo real (opcional: PC_PRUEBAS_IA=1; necesita los pesos y el conjunto de fotos locales)
# ---------------------------------------------------------------------------
ZIP_FOTOS = oi.CARPETA_IA / "dataset_ia" / "roboflow" / "roboflow_v1.zip"


@pytest.mark.skipif(os.environ.get("PC_PRUEBAS_IA") != "1", reason="prueba con el modelo real: PC_PRUEBAS_IA=1")
@pytest.mark.skipif(not (oi.MODELO_DEFECTO.is_file() and ZIP_FOTOS.is_file()), reason="faltan los pesos o las fotos locales")
def test_con_el_modelo_real_se_lee_una_caja_de_verdad(monkeypatch):
    monkeypatch.setenv("PC_RECONOCIMIENTO", "ia")
    z = zipfile.ZipFile(ZIP_FOTOS)
    caras = sorted(n for n in z.namelist() if "/caja013_cara" in n and n.endswith(".jpg"))
    assert len(caras) == 5
    preparar()
    r = reconocer_lote([z.read(n) for n in caras])
    assert r.origen == "ia" and r.clasificacion != ERR
    assert (r.numero_lote, r.vencimiento) == ("A50939", "2027-09-30")
    assert r.confianza and r.confianza > 0.9 and r.segundos < 8
