"""
Recuperación tras un fallo de comunicación con la PC (sección 4.6 de
docs/arquitectura_comunicacion.md): la Orange Pi sondea con un LOTE VACÍO cada
INTERVALO_REINTENTO_PC_S, sin tope, hasta que la PC responde. El lote vacío
fuerza el camino del regente (alarma en el panel y decisión pendiente), y el
ciclo NO se reanuda solo.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import INTERVALO_REINTENTO_PC_S  # noqa: E402

from network.tcp_client import enviar_lote


def recuperar_comunicacion(descubridor, activar_alarma_local, desactivar_alarma_local,
                           alarma_encendida: bool = False,
                           intervalo: float = INTERVALO_REINTENTO_PC_S,
                           enviar=enviar_lote, dormir=time.sleep) -> None:
    """Bloquea hasta que la PC responde a un lote vacío. Si la alarma local no
    estaba encendida y el primer sondeo falla, la enciende; al primer sondeo
    con respuesta deja de sondear (para no crear una alarma nueva por cada lote
    vacío) y apaga la alarma. Después el llamador espera la decisión del regente."""
    intento = 0
    while True:
        intento += 1
        _, fallo_de_red = enviar(descubridor, [])
        if not fallo_de_red:
            break
        print(f"[recuperacion] La PC no responde (sondeo {intento}); reintento en {intervalo:g}s")
        if not alarma_encendida:
            activar_alarma_local()
            alarma_encendida = True
        dormir(intervalo)
    print("[recuperacion] La PC respondió: se deja de sondear")
    if alarma_encendida:
        desactivar_alarma_local()
