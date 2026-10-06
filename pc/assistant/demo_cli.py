"""
demo_cli.py - prueba el asistente en terminal, sin panel ni servidor.

Los comandos de SOLO LECTURA (`estado_sistema`, `ultima_clasificacion`,
`listar_alarmas`) son los reales de comandos_panel.py, pero aquí corren sobre un
estado de EJEMPLO (dos lotes y dos alarmas) porque no hay servidor en marcha. Los
demás (`listar_proximas_a_vencer`, `iniciar_captura`, `reclasificar_caja`,
`limpiar_alarma`) siguen siendo STUBS: dependen de decisiones abiertas, ver los
comentarios de cada uno.

Ejecutar:  python demo_cli.py     (requiere un modelo .gguf en models/)
Pruebas sin modelo:  python3 -m pytest ../tests/test_assistant.py
"""
import logging

from pydantic import BaseModel, Field

from assistant import Assistant, Proposal, command, make_docs_retriever
from comandos_panel import registrar_comandos_panel, resumen_estado
from estado_panel import EstadoPanel  # comandos_panel ya agregó ../src al path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MODEL_PATH = "models/tu-modelo-q4_k_m.gguf"  # <- coloca aquí tu .gguf


# ---- modelos de parámetros (mantenerlos planos) ---------------------------
class CajaArgs(BaseModel):
    caja_id: str = Field(description="Identificador de la caja, ej. 'C-0012'")


class ProximasArgs(BaseModel):
    dias: int = Field(default=7, description="Ventana de días a futuro a consultar")


class AlarmaArgs(BaseModel):
    alarma_id: str = Field(description="Identificador de la alarma, ej. 'A-35'")


# ---- comandos de solo lectura REALES, sobre un estado de ejemplo ---------------
def _estado_de_ejemplo() -> EstadoPanel:
    estado = EstadoPanel()
    estado.registrar_lote("192.168.20.72", [b"x"] * 5, "TIPO_A")
    antigua = estado.registrar_alarma("sin_consenso_ocr", 1)
    estado.abrir_decision(antigua)
    lote = estado.registrar_lote("192.168.20.72", [], "ERROR_REVISION_MANUAL")
    estado.abrir_decision(estado.registrar_alarma("fallo_captura", lote))
    return estado


ESTADO_DEMO = _estado_de_ejemplo()
registrar_comandos_panel(ESTADO_DEMO)


# ---- comandos STUB (dependen de decisiones abiertas) -----------------------------
# STUB: necesita la fecha de vencimiento, que el reconocimiento aún no entrega
# (S-05 / PC-11 en shared/checklist.md). No se registra en el panel real hasta entonces.
@command(
    "listar_proximas_a_vencer",
    "Lista las cajas en base de datos que vencen dentro de los próximos N días (orden FEFO).",
    params=ProximasArgs,
    examples=("¿qué vence esta semana?", "cajas que vencen en los próximos 30 días"),
)
def listar_proximas_a_vencer(dias: int = 7):
    return f"(stub) 3 cajas vencen dentro de {dias} días, orden FEFO: C-0003, C-0007, C-0012"


# STUB: depende de la consulta abierta S-06 (cómo la PC le da órdenes a la Orange Pi).
@command(
    "iniciar_captura",
    "Inicia la secuencia de captura (5 fotos) para una caja a través de la Orange Pi.",
    params=CajaArgs,
    needs_confirm=True,
    examples=("escanea la caja C-0013", "inicia captura de C-0013"),
)
def iniciar_captura(caja_id: str):
    return f"captura iniciada para {caja_id}"


# STUB: no existe un canal PC -> Orange Pi para repetir una captura (ver S-06).
@command(
    "reclasificar_caja",
    "Repite captura y extracción de fecha para una caja que ya fue escaneada (ej. tras ERROR_REVISION_MANUAL).",
    params=CajaArgs,
    needs_confirm=True,
    examples=("reclasifica la caja C-0012", "repite la última caja"),
)
def reclasificar_caja(caja_id: str):
    return f"reclasificación en cola para {caja_id}"


# STUB — NO CONECTAR TAL CUAL: resolver una alarma implica elegir el destino de la caja,
# y esa decisión es del regente (D-07 en shared/checklist.md), no del asistente.
@command(
    "limpiar_alarma",
    "Reconoce y limpia una alarma activa del panel de control.",
    params=AlarmaArgs,
    needs_confirm=True,
    examples=("limpia la alarma A-35",),
)
def limpiar_alarma(alarma_id: str):
    return f"alarma {alarma_id} limpiada"


def main():
    bot = Assistant(
        MODEL_PATH,
        n_gpu_layers=-1,  # usar 0 para correr el LLM en CPU y dejar la VRAM al modelo de vision
        docs_retriever=make_docs_retriever("ASSISTANT_CONTEXT.md"),
    )
    print("Escribe una solicitud (q para salir).")
    while True:
        text = input("operador> ").strip()
        if text.lower() in {"q", "quit", "exit"}:
            break
        if not text:
            continue
        out = bot.handle(text, resumen_estado(ESTADO_DEMO))
        while isinstance(out, Proposal):
            answer = input(f"  ¿Ejecutar {out.summary}? [y/N] ").strip().lower()
            out = bot.confirm(out, resumen_estado(ESTADO_DEMO)) if answer == "y" else bot.cancel(out)
        print("asistente>", out.text)


if __name__ == "__main__":
    main()
