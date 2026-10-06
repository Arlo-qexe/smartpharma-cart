"""Pruebas de la Fase 3: el asistente en el panel web (servicio + endpoints HTTP +
LLM de prueba), todo sin modelo real. Correr desde pc/:  python3 -m pytest tests/
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "assistant"))

import assistant  # noqa: E402
from assistant import Assistant, command  # noqa: E402
from asistente_api import AsistenteOcupado, PropuestaInexistente, TextoInvalido  # noqa: E402
from comandos_panel import registrar_comandos_panel  # noqa: E402
from estado_panel import EstadoPanel  # noqa: E402
from llm_falso import LLMFalso, cmd, reply  # noqa: E402
from llm_prueba import AYUDA, LLMDePrueba  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from servicio import ServicioAsistente, crear_servicio_desde_entorno  # noqa: E402


@pytest.fixture
def registro(monkeypatch):
    vacio = {}
    monkeypatch.setattr(assistant, "REGISTRY", vacio)
    return vacio


@pytest.fixture
def montar(registro):
    """Fábrica: crea servicio + panel con el LLM dado y lo apaga al terminar."""
    servidores = []

    def _montar(llm, con_asistente=True):
        estado = EstadoPanel()
        registrar_comandos_panel(estado)

        class Args(BaseModel):
            caja: str

        ejecutadas = []

        @command("repetir", "Repite una caja.", params=Args, needs_confirm=True)
        def repetir(caja):
            ejecutadas.append(caja)
            return f"repetida {caja}"

        servicio = ServicioAsistente(Assistant(llm=llm), estado, "prueba") if con_asistente else None
        servidor = iniciar_panel(estado, "127.0.0.1", 0, asistente=servicio)
        servidores.append(servidor)
        base = f"http://127.0.0.1:{servidor.server_address[1]}"
        return base, servicio, estado, ejecutadas

    yield _montar
    for s in servidores:
        s.shutdown()
        s.server_close()


def post(base, ruta, cuerpo=None, headers=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(base + ruta, data=datos, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(base, ruta):
    with urllib.request.urlopen(base + ruta, timeout=5) as r:
        return r.status, json.loads(r.read())


# ---------------------------------------------------------------------------
# LLM de prueba (no es un modelo: reglas fijas)
# ---------------------------------------------------------------------------
def esquema(*nombres):
    opciones = [{"properties": {"action": {"const": "reply"}, "text": {}}}]
    opciones += [{"properties": {"action": {"const": "command"}, "name": {"const": n}, "args": {}}}
                 for n in nombres]
    return {"schema": {"oneOf": opciones}}


def decide(texto, *nombres):
    salida = LLMDePrueba().create_chat_completion([{"role": "user", "content": texto}], esquema(*nombres))
    return json.loads(salida["choices"][0]["message"]["content"])


TODOS = ("estado_sistema", "ultima_clasificacion", "listar_alarmas")


def test_llm_de_prueba_reconoce_las_palabras_clave():
    assert decide("¿Hay alguna ALARMA?", *TODOS)["name"] == "listar_alarmas"
    assert decide("¿Hay alguna ALARMA?", *TODOS)["args"] == {"solo_activas": True}
    assert decide("muéstrame todas las alarmas", *TODOS)["args"] == {"solo_activas": False}
    assert decide("las últimas 3 clasificaciones", *TODOS)["args"] == {"cantidad": 3}
    assert decide("¿cuál fue el último resultado?", *TODOS)["args"] == {"cantidad": 1}
    assert decide("¿cómo va el sistema?", *TODOS)["name"] == "estado_sistema"


def test_llm_de_prueba_solo_ofrece_comandos_del_schema_y_da_ayuda_si_no_entiende():
    assert decide("¿hay alarmas?", "estado_sistema") == {"action": "reply", "text": AYUDA}
    assert decide("cuéntame un chiste", *TODOS) == {"action": "reply", "text": AYUDA}


def test_llm_de_prueba_devuelve_el_resultado_de_la_herramienta():
    r = decide("[resultado de herramienta: estado_sistema] Todo en calma.", *TODOS)
    assert r == {"action": "reply", "text": "Todo en calma."}
    assert "No pude" in decide("[error] comando desconocido", *TODOS)["text"]


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------
def servicio_con(registro, llm):
    estado = EstadoPanel()
    registrar_comandos_panel(estado)
    return ServicioAsistente(Assistant(llm=llm), estado, "prueba"), estado


def test_servicio_valida_el_texto(registro):
    servicio, _ = servicio_con(registro, LLMFalso())
    for malo in (None, "", "   ", 123, "x" * 1001):
        with pytest.raises(TextoInvalido):
            servicio.mensaje(malo)


def test_servicio_confirmar_con_id_viejo_o_ajeno_no_ejecuta_nada(registro):
    ejecutadas = []

    @command("repetir", "Repite.", needs_confirm=True)
    def repetir():
        ejecutadas.append(1)

    servicio, _ = servicio_con(registro, LLMFalso(cmd("repetir"), reply("ok")))
    propuesta = servicio.mensaje("repite")
    assert propuesta["tipo"] == "propuesta"
    with pytest.raises(PropuestaInexistente):
        servicio.confirmar(propuesta["id"] + 1)          # id ajeno
    assert ejecutadas == []
    assert servicio.confirmar(propuesta["id"])["tipo"] == "respuesta"
    assert ejecutadas == [1]
    with pytest.raises(PropuestaInexistente):
        servicio.confirmar(propuesta["id"])              # ya se usó: no se ejecuta dos veces
    assert ejecutadas == [1]


def test_servicio_escribir_otra_cosa_cancela_la_propuesta_pendiente(registro):
    ejecutadas = []

    @command("repetir", "Repite.", needs_confirm=True)
    def repetir():
        ejecutadas.append(1)

    servicio, _ = servicio_con(registro, LLMFalso(cmd("repetir"), reply("otra cosa")))
    propuesta = servicio.mensaje("repite")
    assert servicio.mensaje("mejor dime la hora")["tipo"] == "respuesta"
    with pytest.raises(PropuestaInexistente):
        servicio.confirmar(propuesta["id"])
    assert ejecutadas == []


def test_servicio_reiniciar_borra_la_conversacion(registro):
    servicio, _ = servicio_con(registro, LLMFalso(reply("hola")))
    servicio.mensaje("hola")
    assert servicio.bot.history
    servicio.reiniciar()
    assert servicio.bot.history == []


class LLMBloqueante:
    """Se queda esperando dentro de la llamada hasta que la prueba lo libere."""

    def __init__(self):
        self.entro = threading.Event()
        self.liberar = threading.Event()

    def create_chat_completion(self, messages, response_format, temperature, max_tokens):
        self.entro.set()
        assert self.liberar.wait(5)
        return {"choices": [{"message": {"content": json.dumps(reply("listo"))}}]}


def test_servicio_atiende_de_a_una_solicitud(registro):
    llm = LLMBloqueante()
    servicio, _ = servicio_con(registro, llm)
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(servicio.mensaje("primera")))
    hilo.start()
    assert llm.entro.wait(5)
    with pytest.raises(AsistenteOcupado):
        servicio.mensaje("segunda")
    llm.liberar.set()
    hilo.join(5)
    assert resultado == {"tipo": "respuesta", "texto": "listo"}
    llm.liberar.clear()
    llm.entro.clear()


def test_crear_servicio_desde_entorno(registro, monkeypatch):
    estado = EstadoPanel()
    monkeypatch.delenv("ASISTENTE_MODELO", raising=False)
    monkeypatch.delenv("ASISTENTE_MODO", raising=False)
    assert crear_servicio_desde_entorno(estado) is None            # desactivado por defecto

    monkeypatch.setenv("ASISTENTE_MODO", "prueba")
    servicio = crear_servicio_desde_entorno(estado)
    assert servicio.modo == "prueba"
    assert servicio.mensaje("¿cómo va el sistema?")["texto"].startswith("Aún no se ha recibido")
    assert set(assistant.REGISTRY) == {"estado_sistema", "ultima_clasificacion", "listar_alarmas"}


# ---------------------------------------------------------------------------
# Endpoints HTTP
# ---------------------------------------------------------------------------
def test_sin_asistente_el_panel_funciona_y_las_rutas_responden_503(montar):
    base, _, _, _ = montar(LLMFalso(), con_asistente=False)
    assert get(base, "/api/asistente/estado")[1]["modo"] == "desactivado"
    assert "ASISTENTE_MODO" in get(base, "/api/asistente/estado")[1]["ayuda"]
    assert get(base, "/api/estado")[0] == 200                      # el resto del panel intacto
    assert post(base, "/api/asistente/mensaje", {"texto": "hola"}) == (503, {"error": "asistente_no_disponible"})


def test_estado_del_asistente(montar):
    base, _, _, _ = montar(LLMFalso())
    assert get(base, "/api/asistente/estado")[1] == {"disponible": True, "modo": "prueba"}


def test_mensaje_de_solo_lectura_por_http(montar):
    base, _, estado, _ = montar(LLMFalso(cmd("estado_sistema"), reply("Todo en calma.")))
    estado.registrar_lote("10.0.0.5", [b"a"] * 5, "TIPO_A")
    assert post(base, "/api/asistente/mensaje", {"texto": "¿cómo va?"}) == (
        200, {"tipo": "respuesta", "texto": "Todo en calma."})


def test_accion_propuesta_confirmada_por_http(montar):
    base, _, _, ejecutadas = montar(LLMFalso(cmd("repetir", caja="C-9"), reply("Hecho.")))
    codigo, propuesta = post(base, "/api/asistente/mensaje", {"texto": "repite C-9"})
    assert codigo == 200 and propuesta["tipo"] == "propuesta" and "C-9" in propuesta["resumen"]
    assert ejecutadas == []                                        # nada hasta confirmar
    assert post(base, "/api/asistente/confirmar", {"id": propuesta["id"]}) == (
        200, {"tipo": "respuesta", "texto": "Hecho."})
    assert ejecutadas == ["C-9"]


def test_accion_cancelada_por_http(montar):
    base, _, _, ejecutadas = montar(LLMFalso(cmd("repetir", caja="C-9")))
    _, propuesta = post(base, "/api/asistente/mensaje", {"texto": "repite C-9"})
    codigo, r = post(base, "/api/asistente/cancelar", {"id": propuesta["id"]})
    assert (codigo, r["texto"]) == (200, "Cancelado.") and ejecutadas == []


def test_errores_http(montar):
    base, _, _, _ = montar(LLMFalso())
    assert post(base, "/api/asistente/mensaje", {"texto": ""})[1] == {"error": "texto_invalido"}
    assert post(base, "/api/asistente/mensaje", {"texto": "x" * 1001})[0] == 400
    assert post(base, "/api/asistente/mensaje")[0] == 400                 # sin cuerpo
    assert post(base, "/api/asistente/confirmar", {"id": "uno"})[1] == {"error": "id_invalido"}
    assert post(base, "/api/asistente/confirmar", {"id": True})[0] == 400  # un bool no es un id
    assert post(base, "/api/asistente/confirmar", {"id": 99})[1] == {"error": "propuesta_inexistente"}
    assert post(base, "/api/asistente/inventada", {})[0] == 404
    assert post(base, "/api/asistente/estado", {})[0] == 404               # estado es solo GET


def test_post_del_asistente_con_origen_ajeno_se_rechaza(montar):
    base, servicio, _, _ = montar(LLMFalso(reply("no debería llegar")))
    codigo, _ = post(base, "/api/asistente/mensaje", {"texto": "hola"},
                     {"Origin": "http://malicioso.example"})
    assert codigo == 403
    assert servicio.bot.history == []                                      # ni siquiera llegó al asistente


def test_error_interno_del_modelo_no_tumba_el_panel(montar):
    class LLMRoto:
        def create_chat_completion(self, *a, **k):
            raise RuntimeError("el modelo falló")

    base, _, _, _ = montar(LLMRoto())
    assert post(base, "/api/asistente/mensaje", {"texto": "hola"}) == (500, {"error": "error_interno"})
    assert get(base, "/api/estado")[0] == 200                              # el panel sigue vivo


def test_http_asistente_ocupado_responde_409(montar):
    llm = LLMBloqueante()
    base, _, _, _ = montar(llm)
    primera = {}
    hilo = threading.Thread(
        target=lambda: primera.update(r=post(base, "/api/asistente/mensaje", {"texto": "uno"})))
    hilo.start()
    assert llm.entro.wait(5)
    assert post(base, "/api/asistente/mensaje", {"texto": "dos"}) == (409, {"error": "asistente_ocupado"})
    llm.liberar.set()
    hilo.join(5)
    assert primera["r"][0] == 200


def test_reiniciar_por_http(montar):
    base, servicio, _, _ = montar(LLMFalso(reply("hola")))
    post(base, "/api/asistente/mensaje", {"texto": "hola"})
    assert post(base, "/api/asistente/reiniciar", {}) == (200, {"ok": True})
    assert servicio.bot.history == []


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------
def test_la_pagina_trae_el_asistente_en_el_lateral_de_inicio_y_el_js_no_usa_innerhtml():
    html = (RAIZ / "src" / "panel_static" / "index.html").read_text(encoding="utf-8")
    js = (RAIZ / "src" / "panel_static" / "panel.js").read_text(encoding="utf-8")
    # El asistente vive en un lateral de la pestaña Inicio, no en una pestaña propia.
    assert 'data-vista="asistente"' not in html and 'id="vista-asistente"' not in html
    inicio = html[html.index('id="vista-inicio"'):html.index('id="vista-inventario"')]
    assert 'id="asistente"' in inicio and 'id="chat-form"' in inicio
    assert "/api/asistente/" in js
    # Todo el texto del chat (que viene del modelo) se pinta con textContent:
    for peligroso in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"):
        assert peligroso not in js
