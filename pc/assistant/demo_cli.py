"""
demo_cli.py - prueba el asistente en terminal antes de que exista la GUI del panel
de control.

Reemplaza el cuerpo de los comandos stub por llamadas al código real del servidor
(src/server.py, src/ocr_interface.py) una vez esté integrado.

Ejecutar:  python demo_cli.py
"""
import logging

from pydantic import BaseModel, Field

from assistant import Assistant, Proposal, command, make_docs_retriever

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MODEL_PATH = "models/tu-modelo-q4_k_m.gguf"  # <- coloca aquí tu .gguf


# ---- modelos de parámetros (mantenerlos planos) ---------------------------
class CajaArgs(BaseModel):
    caja_id: str = Field(description="Identificador de la caja, ej. 'C-0012'")


class ProximasArgs(BaseModel):
    dias: int = Field(default=7, description="Ventana de días a futuro a consultar")


class AlarmaArgs(BaseModel):
    alarma_id: str = Field(description="Identificador de la alarma, ej. 'A-35'")


# ---- comandos (stubs) ------------------------------------------------------
@command(
    "estado_sistema",
    "Reporta si el carro está inactivo, clasificando o en error (tracción, manipulador, dispensador).",
    examples=("¿el carro está listo?", "¿qué está haciendo el sistema?"),
)
def estado_sistema():
    return "inactivo"


@command(
    "ultima_clasificacion",
    "Muestra la última caja clasificada: id, fecha de vencimiento extraída, confianza del OCR, si se disparó alarma.",
    examples=("¿cuál fue el último resultado?", "muéstrame la última clasificación"),
)
def ultima_clasificacion():
    return "caja C-0012, fecha 2026-11-02, confianza 0.94, sin alarma"


@command(
    "listar_proximas_a_vencer",
    "Lista las cajas en base de datos que vencen dentro de los próximos N días (orden FEFO).",
    params=ProximasArgs,
    examples=("¿qué vence esta semana?", "cajas que vencen en los próximos 30 días"),
)
def listar_proximas_a_vencer(dias: int = 7):
    return f"(stub) 3 cajas vencen dentro de {dias} días, orden FEFO: C-0003, C-0007, C-0012"


@command(
    "iniciar_captura",
    "Inicia la secuencia de captura (5 fotos) para una caja a través de la Orange Pi.",
    params=CajaArgs,
    needs_confirm=True,
    examples=("escanea la caja C-0013", "inicia captura de C-0013"),
)
def iniciar_captura(caja_id: str):
    return f"captura iniciada para {caja_id}"


@command(
    "reclasificar_caja",
    "Repite captura y extracción de fecha para una caja que ya fue escaneada (ej. tras ERROR_REVISION_MANUAL).",
    params=CajaArgs,
    needs_confirm=True,
    examples=("reclasifica la caja C-0012", "repite la última caja"),
)
def reclasificar_caja(caja_id: str):
    return f"reclasificación en cola para {caja_id}"


@command(
    "limpiar_alarma",
    "Reconoce y limpia una alarma activa del panel de control.",
    params=AlarmaArgs,
    needs_confirm=True,
    examples=("limpia la alarma A-35",),
)
def limpiar_alarma(alarma_id: str):
    return f"alarma {alarma_id} limpiada"


# ---- estado de la app inyectado en cada prompt -----------------------------
def obtener_estado() -> dict:
    return {
        "carro": "inactivo",
        "ultima_caja_id": "C-0012",
        "ultima_fecha_vencimiento": "2026-11-02",
        "ultima_confianza_ocr": 0.94,
        "alarmas_activas": [],
    }


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
        out = bot.handle(text, obtener_estado())
        while isinstance(out, Proposal):
            answer = input(f"  ¿Ejecutar {out.summary}? [y/N] ").strip().lower()
            out = bot.confirm(out, obtener_estado()) if answer == "y" else bot.cancel(out)
        print("asistente>", out.text)


if __name__ == "__main__":
    main()
