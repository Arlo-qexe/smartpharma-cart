"""Pruebas del asistente sin modelo real (Fases 0 y 1): el motor con un LLM falso
y los comandos de solo lectura sobre el estado del panel.
Correr desde pc/:  python3 -m pytest tests/
"""
import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "assistant"))

import assistant  # noqa: E402
from assistant import Assistant, Proposal, Reply, build_schema, command, select_commands  # noqa: E402
from comandos_panel import (  # noqa: E402
    AlarmasArgs,
    UltimasArgs,
    registrar_comandos_panel,
    resumen_estado,
)
from estado_panel import EstadoPanel  # noqa: E402
from llm_falso import LLMFalso, cmd, reply  # noqa: E402


@pytest.fixture
def registro(monkeypatch):
    """Registro de comandos vacío y aislado: no contamina a otras pruebas."""
    vacio = {}
    monkeypatch.setattr(assistant, "REGISTRY", vacio)
    return vacio


# ---------------------------------------------------------------------------
# Fase 0 — el motor, sin modelo real
# ---------------------------------------------------------------------------
def test_sin_modelo_ni_llm_falla_con_un_mensaje_claro():
    with pytest.raises(ValueError):
        Assistant()


def test_respuesta_de_texto_y_prompt_con_estado_y_comandos(registro):
    @command("ping", "Responde pong.", examples=("haz ping",))
    def ping():
        return "pong"

    llm = LLMFalso(reply("Hola"))
    out = Assistant(llm=llm).handle("hola", {"alarmas_activas": 3})
    assert out == Reply("Hola")
    sistema = llm.llamadas[0]["messages"][0]["content"]
    assert "ping()" in sistema and '"alarmas_activas": 3' in sistema


def test_comando_de_solo_lectura_se_ejecuta_y_el_resultado_vuelve_al_modelo(registro):
    @command("ping", "Responde pong.")
    def ping():
        return "pong"

    llm = LLMFalso(cmd("ping"), reply("Resultado: pong"))
    out = Assistant(llm=llm).handle("haz ping")
    assert out == Reply("Resultado: pong")
    segunda = llm.llamadas[1]["messages"]
    assert any("[resultado de herramienta: ping] pong" in m["content"] for m in segunda)


def test_accion_pide_confirmacion_y_no_se_ejecuta_antes(registro):
    ejecutadas = []

    class Args(BaseModel):
        caja: str

    @command("repetir", "Repite una caja.", params=Args, needs_confirm=True)
    def repetir(caja):
        ejecutadas.append(caja)
        return f"ok {caja}"

    llm = LLMFalso(cmd("repetir", caja="C-1"), reply("Listo, repetida."))
    bot = Assistant(llm=llm)
    propuesta = bot.handle("repite C-1")
    assert isinstance(propuesta, Proposal) and propuesta.name == "repetir"
    assert ejecutadas == []                      # nada se ejecuta sin confirmar

    assert bot.confirm(propuesta) == Reply("Listo, repetida.")
    assert ejecutadas == ["C-1"]
    assert any("[resultado de herramienta: repetir] ok C-1" in m["content"]
               for m in llm.llamadas[1]["messages"])


def test_cancelar_no_ejecuta(registro):
    ejecutadas = []

    @command("repetir", "Repite una caja.", needs_confirm=True)
    def repetir():
        ejecutadas.append(1)

    bot = Assistant(llm=LLMFalso(cmd("repetir")))
    propuesta = bot.handle("repite")
    assert bot.cancel(propuesta) == Reply("Cancelado.")
    assert ejecutadas == []


def test_comando_desconocido_y_argumentos_invalidos_no_rompen_el_bucle(registro):
    class Args(BaseModel):
        n: int

    @command("contar", "Cuenta.", params=Args)
    def contar(n):
        return str(n)

    llm = LLMFalso(cmd("inventado"), cmd("contar", n="no es número"), reply("Perdón"))
    bot = Assistant(llm=llm)
    assert bot.handle("cuenta") == Reply("Perdón")
    errores = [m["content"] for m in bot.history if m["content"].startswith("[error]")]
    assert len(errores) == 2


def test_json_invalido_del_modelo_da_respuesta_de_cortesia(registro):
    out = Assistant(llm=LLMFalso("esto no es json")).handle("hola")
    assert "No pude procesar eso" in out.text


def test_si_se_agotan_los_pasos_responde_que_no_pudo(registro):
    llm = LLMFalso(cmd("x"), cmd("x"), cmd("x"))
    out = Assistant(llm=llm, max_steps=3).handle("algo")
    assert "No pude completar eso" in out.text


def test_select_commands_limita_y_prioriza_los_relevantes(registro):
    for i in range(10):
        command(f"cmd_{i}", f"hace la cosa numero{i}")(lambda: None)
    elegidos = select_commands("numero7", max_n=3)
    assert len(elegidos) == 3 and "cmd_7" in {c.name for c in elegidos}


def test_recuperador_de_documentacion_encuentra_la_seccion():
    retrieve = assistant.make_docs_retriever(str(RAIZ / "assistant" / "ASSISTANT_CONTEXT.md"))
    assert any("Reglas de seguridad" in c for c in retrieve("reglas de seguridad"))


# ---------------------------------------------------------------------------
# Fase 1 — comandos de solo lectura sobre el estado real del panel
# ---------------------------------------------------------------------------
@pytest.fixture
def estado_demo(registro):
    estado = EstadoPanel()
    estado.registrar_lote("10.0.0.5", [b"a"] * 5, "TIPO_A")
    vieja = estado.registrar_alarma("sin_consenso_ocr", 1)
    estado.abrir_decision(vieja)
    lote2 = estado.registrar_lote("10.0.0.5", [], "ERROR_REVISION_MANUAL")
    nueva = estado.registrar_alarma("fallo_captura", lote2)
    estado.abrir_decision(nueva)         # la Orange Pi siguió: la vieja queda "antigua"
    registrar_comandos_panel(estado)
    estado.ids = {"vieja": vieja, "nueva": nueva}
    return estado


def ejecutar(nombre, **args):
    return assistant.REGISTRY[nombre].func(**args)


def test_estado_sistema_resume_lo_que_la_pc_sabe(estado_demo):
    texto = ejecutar("estado_sistema")
    assert "Lotes en el historial: 2" in texto
    assert "resultado: Revisión manual" in texto
    assert "Alarmas activas: 2" in texto
    assert "esperando la decisión del regente: 1" in texto
    assert "antiguas sin cerrar: 1" in texto
    assert "La Orange Pi está esperando la decisión del regente." in texto
    assert "No conozco el estado mecánico" in texto       # no inventa lo que no sabe


def test_estado_sistema_sin_datos(registro):
    registrar_comandos_panel(EstadoPanel())
    texto = ejecutar("estado_sistema")
    assert "Aún no se ha recibido ningún lote." in texto
    assert "No hay alarmas activas." in texto
    assert "ninguna decisión en espera" in texto


def test_estado_sistema_con_decision_ya_resuelta(estado_demo):
    estado_demo.resolver_alarma(estado_demo.ids["nueva"], "DESCARTE")
    assert "El regente ya decidió: destino DESCARTE." in ejecutar("estado_sistema")


def test_ultima_clasificacion_no_inventa_fecha_ni_confianza(estado_demo):
    uno = ejecutar("ultima_clasificacion")
    assert "Lote #2" in uno and "Lote #1" not in uno and "Revisión manual" in uno
    dos = ejecutar("ultima_clasificacion", cantidad=2)
    assert "Lote #1" in dos and "TIPO_A" in dos and "5 imágenes" in dos
    assert "aún no están disponibles" in dos


def test_ultima_clasificacion_sin_lotes(registro):
    registrar_comandos_panel(EstadoPanel())
    assert ejecutar("ultima_clasificacion") == "Aún no se ha recibido ningún lote."


def test_parametros_validados_por_pydantic():
    for malo in (0, 11, -1):
        with pytest.raises(ValidationError):
            UltimasArgs(cantidad=malo)
    assert AlarmasArgs().solo_activas is True


def test_listar_alarmas_activas_y_todas(estado_demo):
    activas = ejecutar("listar_alarmas")
    assert activas.count("Alarma #") == 2
    assert "esperando la decisión del regente" in activas and "antigua, sin cerrar" in activas
    assert "Lote vacío (captura fallida o reintento tras perder la conexión)" in activas

    estado_demo.resolver_alarma(estado_demo.ids["vieja"], None)       # se cierra la antigua
    estado_demo.resolver_alarma(estado_demo.ids["nueva"], "TIPO_B")
    assert ejecutar("listar_alarmas") == "No hay alarmas activas."
    todas = ejecutar("listar_alarmas", solo_activas=False)
    assert "resuelta → TIPO_B" in todas and "cerrada" in todas


def test_resumen_estado_para_el_prompt(estado_demo):
    assert resumen_estado(estado_demo) == {
        "lotes_en_historial": 2,
        "ultimo_resultado": "Revisión manual",
        "alarmas_activas": 2,
        "decision_del_regente": "pendiente",
    }


def test_los_comandos_del_panel_son_de_solo_lectura_y_con_parametros_planos(estado_demo):
    assert set(assistant.REGISTRY) == {"estado_sistema", "ultima_clasificacion", "listar_alarmas"}
    for c in assistant.REGISTRY.values():
        assert c.needs_confirm is False                  # nada que mueva el carro ni decida por el regente
    esquema = json.dumps(build_schema(list(assistant.REGISTRY.values())))
    assert "$ref" not in esquema and "$defs" not in esquema   # regla: parámetros planos
    assert len(build_schema(list(assistant.REGISTRY.values()))["oneOf"]) == 4  # reply + 3 comandos


def test_conversacion_completa_con_llm_falso(estado_demo):
    llm = LLMFalso(cmd("estado_sistema"), reply("Hay 2 alarmas activas."))
    out = Assistant(llm=llm).handle("¿hay alguna alarma?", resumen_estado(estado_demo))
    assert out == Reply("Hay 2 alarmas activas.")               # sin pedir confirmación
    assert any("Alarmas activas: 2" in m["content"] for m in llm.llamadas[1]["messages"])
