"""
Comandos del asistente respaldados por el estado REAL del panel (Fase 1): solo
lectura, nada que mueva el carro ni decida por el regente.

Lo que el sistema sabe hoy de cada lote es solo: hora, origen, cuántas imágenes y la
clasificación (la fecha de vencimiento y la confianza del OCR aún no existen; ver
S-05 / PC-11 en shared/checklist.md). Por eso los textos no inventan esos datos.

Uso (el panel o el arnés de terminal):
    registrar_comandos_panel(estado)    # registra los comandos sobre ese estado
    app_state = resumen_estado(estado)  # pequeño resumen para cada prompt
"""
import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from estado_panel import MOTIVOS_ALARMA, EstadoPanel  # noqa: E402
from protocol_constants import (  # noqa: E402
    ESTADO_DECISION_PENDIENTE,
    ESTADO_DECISION_RESUELTA,
    RESULTADO_ERROR_REVISION_MANUAL,
)

from assistant import command  # noqa: E402


class UltimasArgs(BaseModel):
    cantidad: int = Field(default=1, ge=1, le=10, description="Cuántos lotes mostrar (1 a 10)")


class AlarmasArgs(BaseModel):
    solo_activas: bool = Field(
        default=True, description="true: solo las activas; false: también las ya resueltas"
    )


def _hora(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _resultado(r: str) -> str:
    return "Revisión manual" if r == RESULTADO_ERROR_REVISION_MANUAL else r


def _motivo(m: str) -> str:
    return MOTIVOS_ALARMA.get(m, m)


def _descripcion_alarma(a: dict) -> str:
    if a["activa"]:
        return "esperando la decisión del regente" if a["espera_decision"] else "antigua, sin cerrar"
    return f"resuelta → {a['destino']}" if a["destino"] else "cerrada"


def resumen_estado(estado: EstadoPanel) -> dict:
    """Pequeño resumen que se inyecta en cada prompt del asistente."""
    snap = estado.snapshot()
    activas = [a for a in snap["alarmas"] if a["activa"]]
    return {
        "lotes_en_historial": len(snap["lotes"]),
        "ultimo_resultado": _resultado(snap["lotes"][0]["resultado"]) if snap["lotes"] else None,
        "alarmas_activas": len(activas),
        "decision_del_regente": estado.consultar_decision()["estado"],
    }


def registrar_comandos_panel(estado: EstadoPanel) -> None:
    """Registra los comandos de solo lectura sobre `estado` (reemplaza los anteriores
    con el mismo nombre en el registro del asistente)."""

    @command(
        "estado_sistema",
        "Resume lo que la PC sabe ahora: lotes recibidos, último resultado, alarmas activas "
        "y si hay una decisión del regente en espera. No conoce el estado mecánico del carro.",
        examples=("¿cómo va el sistema?", "¿hay alguna alarma?", "¿qué está pasando?"),
    )
    def estado_sistema():
        snap = estado.snapshot()
        lotes = snap["lotes"]
        activas = [a for a in snap["alarmas"] if a["activa"]]
        esperando = [a for a in activas if a["espera_decision"]]
        decision = estado.consultar_decision()

        partes = []
        if lotes:
            u = lotes[0]
            partes.append(
                f"Lotes en el historial: {len(lotes)}. Último: lote #{u['id']} a las "
                f"{_hora(u['ts'])}, resultado: {_resultado(u['resultado'])}."
            )
        else:
            partes.append("Aún no se ha recibido ningún lote.")
        if activas:
            partes.append(
                f"Alarmas activas: {len(activas)} (esperando la decisión del regente: "
                f"{len(esperando)}; antiguas sin cerrar: {len(activas) - len(esperando)})."
            )
        else:
            partes.append("No hay alarmas activas.")
        if decision["estado"] == ESTADO_DECISION_PENDIENTE:
            partes.append("La Orange Pi está esperando la decisión del regente.")
        elif decision["estado"] == ESTADO_DECISION_RESUELTA:
            partes.append(f"El regente ya decidió: destino {decision['destino']}.")
        else:
            partes.append("La Orange Pi no tiene ninguna decisión en espera.")
        partes.append("No conozco el estado mecánico del carro (motores, dispensador).")
        return " ".join(partes)

    @command(
        "ultima_clasificacion",
        "Muestra los últimos lotes recibidos: número, hora, origen, cuántas imágenes y la "
        "clasificación. La fecha de vencimiento y la confianza del OCR aún no están disponibles.",
        params=UltimasArgs,
        examples=("¿cuál fue el último resultado?", "muéstrame las últimas 3 clasificaciones"),
    )
    def ultima_clasificacion(cantidad: int = 1):
        lotes = estado.snapshot()["lotes"][:cantidad]
        if not lotes:
            return "Aún no se ha recibido ningún lote."
        lineas = [
            f"Lote #{l['id']} · {_hora(l['ts'])} · desde {l['origen']} · "
            f"{l['n_imagenes']} imágenes · {_resultado(l['resultado'])}"
            for l in lotes
        ]
        lineas.append("(La fecha de vencimiento y la confianza del OCR aún no están disponibles.)")
        return "\n".join(lineas)

    @command(
        "listar_alarmas",
        "Lista las alarmas del panel con su motivo, el lote y su estado (esperando la decisión "
        "del regente, antigua sin cerrar, o resuelta).",
        params=AlarmasArgs,
        examples=("¿qué alarmas hay?", "muéstrame todas las alarmas, incluso las resueltas"),
    )
    def listar_alarmas(solo_activas: bool = True):
        alarmas = estado.snapshot()["alarmas"]
        if solo_activas:
            alarmas = [a for a in alarmas if a["activa"]]
        if not alarmas:
            return "No hay alarmas activas." if solo_activas else "No hay alarmas registradas."
        return "\n".join(
            f"Alarma #{a['id']} · {_hora(a['ts'])} · {_motivo(a['motivo'])} · "
            f"{'lote #' + str(a['lote_id']) if a['lote_id'] else 'sin lote'} · "
            f"{_descripcion_alarma(a)}"
            for a in alarmas
        )
