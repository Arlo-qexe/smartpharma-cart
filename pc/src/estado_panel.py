"""
Estado en memoria del panel de control: historial de lotes, fotos del lote y
alarmas. Es el único punto donde `server.py` (escritor) y `panel_web.py`
(lector) se tocan.

Las imágenes viven SOLO en RAM (ver pc/CLAUDE.md: nunca se escriben a disco)
y solo se conservan las de los últimos MAX_LOTES_CON_FOTOS lotes.
"""
import itertools
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_DESTINO_JSON,
    CLAVE_ESTADO_DECISION_JSON,
    DESTINO_DESCARTE,
    ESTADO_DECISION_NINGUNA,
    ESTADO_DECISION_PENDIENTE,
    ESTADO_DECISION_RESUELTA,
    RESULTADO_ERROR_REVISION_MANUAL,
)

MAX_LOTES_CON_FOTOS = 5
MAX_HISTORIAL_LOTES = 50
MAX_ALARMAS = 100
# Formato del destino que el regente puede escribir (TIPO_X o DESCARTE).
# Validación del lado PC; el esquema del contrato usa el mismo patrón.
PATRON_DESTINO = re.compile(r"^[A-Z0-9_]{1,32}$")

# Resultados de resolver_alarma()
RESUELTA_CON_DECISION = "resuelta"        # se fijó el destino para la Orange Pi
CERRADA_SIN_DECISION = "cerrada"          # alarma antigua: solo se cierra
DESTINO_INVALIDO = "destino_invalido"
NO_EXISTE = "no_existe"


class EstadoPanel:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids_lote = itertools.count(1)
        self._ids_alarma = itertools.count(1)
        self._lotes = deque(maxlen=MAX_HISTORIAL_LOTES)
        self._alarmas = deque(maxlen=MAX_ALARMAS)
        # UNA sola decisión en espera (flujo secuencial, sin IDs de correlación):
        # {"alarma_id": int, "estado": pendiente|resuelta, "destino": str|None}
        self._decision = None

    def registrar_lote(self, origen: str, imagenes: list, resultado: str) -> int:
        with self._lock:
            lote_id = next(self._ids_lote)
            self._lotes.append({
                "id": lote_id,
                "ts": time.time(),
                "origen": origen,
                "resultado": resultado,
                "n_imagenes": len(imagenes),
                "imagenes": list(imagenes),
            })
            for viejo in list(self._lotes)[:-MAX_LOTES_CON_FOTOS]:
                viejo["imagenes"] = None
            return lote_id

    def registrar_alarma(self, motivo: str, lote_id: int | None = None) -> int:
        with self._lock:
            alarma_id = next(self._ids_alarma)
            self._alarmas.append({
                "id": alarma_id,
                "ts": time.time(),
                "motivo": motivo,
                "lote_id": lote_id,
                "activa": True,
                "ts_resuelta": None,
                "destino": None,
            })
            return alarma_id

    # -- decisión del regente (ver docs/propuesta_canal_regente.md, opción A) --
    def abrir_decision(self, alarma_id: int) -> None:
        """Un lote terminó en ERROR_REVISION_MANUAL: la Orange Pi esperará la
        decisión del regente. Reemplaza cualquier decisión anterior."""
        with self._lock:
            self._decision = {
                "alarma_id": alarma_id,
                "estado": ESTADO_DECISION_PENDIENTE,
                "destino": None,
            }

    def cerrar_decision(self) -> None:
        """Llegó un lote sin error: la Orange Pi ya siguió adelante."""
        with self._lock:
            self._decision = None

    def consultar_decision(self) -> dict:
        """Respuesta para la consulta de la Orange Pi (no consume la decisión)."""
        with self._lock:
            d = self._decision
            if d is None:
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_NINGUNA}
            if d["estado"] == ESTADO_DECISION_RESUELTA:
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_RESUELTA,
                        CLAVE_DESTINO_JSON: d["destino"]}
            return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_PENDIENTE}

    def resolver_alarma(self, alarma_id: int, destino: str | None) -> str:
        """El regente resuelve una alarma desde el panel.

        Si es la que la Orange Pi está esperando, `destino` es obligatorio
        (TIPO_X o DESCARTE) y queda disponible para su próxima consulta. Si es
        una alarma antigua (la Orange Pi ya siguió con otro lote), solo se cierra.
        """
        with self._lock:
            alarma = next((a for a in self._alarmas if a["id"] == alarma_id), None)
            if alarma is None:
                return NO_EXISTE
            d = self._decision
            espera = (d is not None and d["alarma_id"] == alarma_id
                      and d["estado"] == ESTADO_DECISION_PENDIENTE)
            if espera:
                if not isinstance(destino, str) or not PATRON_DESTINO.match(destino):
                    return DESTINO_INVALIDO
                d["estado"] = ESTADO_DECISION_RESUELTA
                d["destino"] = destino
                alarma["destino"] = destino
            if alarma["activa"]:
                alarma["activa"] = False
                alarma["ts_resuelta"] = time.time()
            return RESUELTA_CON_DECISION if espera else CERRADA_SIN_DECISION

    def foto(self, lote_id: int, indice: int) -> bytes | None:
        with self._lock:
            for lote in self._lotes:
                if lote["id"] == lote_id:
                    imagenes = lote["imagenes"]
                    if imagenes is not None and 0 <= indice < len(imagenes):
                        return imagenes[indice]
                    return None
            return None

    def snapshot(self) -> dict:
        """Vista serializable a JSON (sin bytes de imagen), la más reciente primero."""
        with self._lock:
            lotes = [{
                "id": l["id"],
                "ts": l["ts"],
                "origen": l["origen"],
                "resultado": l["resultado"],
                "n_imagenes": l["n_imagenes"],
                "fotos_disponibles": l["imagenes"] is not None and l["n_imagenes"] > 0,
            } for l in reversed(self._lotes)]
            d = self._decision
            espera_id = (d["alarma_id"] if d and d["estado"] == ESTADO_DECISION_PENDIENTE
                         else None)
            alarmas = []
            for a in reversed(self._alarmas):
                a = dict(a)
                a["espera_decision"] = a["id"] == espera_id
                alarmas.append(a)
            # Sugerencias para el selector del regente: lo que el reconocimiento
            # ya ha clasificado antes, más el descarte. No se inventan tipos.
            vistos = sorted({l["resultado"] for l in self._lotes
                             if l["resultado"] != RESULTADO_ERROR_REVISION_MANUAL})
            return {
                "ts_servidor": time.time(),
                "alarma_activa": any(a["activa"] for a in alarmas),
                "max_lotes_con_fotos": MAX_LOTES_CON_FOTOS,
                "lotes": lotes,
                "alarmas": alarmas,
                "destinos_sugeridos": vistos + [DESTINO_DESCARTE],
            }


estado = EstadoPanel()
