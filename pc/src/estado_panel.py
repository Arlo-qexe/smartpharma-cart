"""
Estado en memoria del panel de control: historial de lotes, fotos del lote y
alarmas. Es el único punto donde `server.py` (escritor) y `panel_web.py`
(lector) se tocan.

Las imágenes viven SOLO en RAM (ver pc/CLAUDE.md: nunca se escriben a disco)
y solo se conservan las de los últimos MAX_LOTES_CON_FOTOS lotes.
"""
import itertools
import threading
import time
from collections import deque

MAX_LOTES_CON_FOTOS = 5
MAX_HISTORIAL_LOTES = 50
MAX_ALARMAS = 100


class EstadoPanel:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids_lote = itertools.count(1)
        self._ids_alarma = itertools.count(1)
        self._lotes = deque(maxlen=MAX_HISTORIAL_LOTES)
        self._alarmas = deque(maxlen=MAX_ALARMAS)

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
            })
            return alarma_id

    def desactivar_alarma(self, alarma_id: int) -> bool:
        """Marca la alarma como resuelta en la PC. NO avisa a la Orange Pi:
        ese canal está pendiente de decisión del equipo."""
        with self._lock:
            for a in self._alarmas:
                if a["id"] == alarma_id:
                    if a["activa"]:
                        a["activa"] = False
                        a["ts_resuelta"] = time.time()
                    return True
            return False

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
            alarmas = [dict(a) for a in reversed(self._alarmas)]
            return {
                "ts_servidor": time.time(),
                "alarma_activa": any(a["activa"] for a in alarmas),
                "max_lotes_con_fotos": MAX_LOTES_CON_FOTOS,
                "lotes": lotes,
                "alarmas": alarmas,
            }


estado = EstadoPanel()
