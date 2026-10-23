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
    CICLO_CONTINUAR,
    CICLO_DETENER,
    CLAVE_CICLO_JSON,
    CLAVE_DESTINO_JSON,
    CLAVE_ESTADO_DECISION_JSON,
    CLAVE_ORDEN_CICLO_JSON,
    DESTINO_DESCARTE,
    ESTADO_DECISION_NINGUNA,
    ESTADO_DECISION_PENDIENTE,
    ESTADO_DECISION_RESUELTA,
    ORDEN_CICLO_ESPERANDO,
    ORDEN_CICLO_INICIAR,
    RESULTADO_ERROR_REVISION_MANUAL,
    VIGENCIA_ORDEN_CICLO_S,
)

MAX_LOTES_CON_FOTOS = 5
MAX_HISTORIAL_LOTES = 50
MAX_ALARMAS = 100
# Formato del destino que el regente puede escribir (TIPO_X o DESCARTE).
# Validación del lado PC; el esquema del contrato usa el mismo patrón.
PATRON_DESTINO = re.compile(r"^[A-Z0-9_]{1,32}$")

# Textos con que se muestra cada motivo de alarma al regente. Los usan el asistente
# (pc/assistant/comandos_panel.py) y, duplicados, panel.js: si se cambia uno, hay
# que cambiar el otro (tests/test_panel.py vigila que coincidan).
MOTIVOS_ALARMA = {
    "fallo_captura": "Lote vacío (captura fallida o reintento tras perder la conexión)",
    "sin_consenso_ocr": "Sin consenso en el reconocimiento",
    "framing_invalido": "Lote inválido (framing)",
}

# Estados de la orden de inicio del ciclo (informe 4.7). `sin_orden` = el regente aún
# no pulsó el botón; `ordenada` = pulsó y la Orange Pi aún no la recogió; `iniciada` =
# la Orange Pi ya la recogió (el ciclo corre).
ORDEN_SIN_ORDEN = "sin_orden"
ORDEN_ORDENADA = "ordenada"
ORDEN_INICIADA = "iniciada"

# Resultados de ordenar_inicio()
INICIO_ORDENADO = "ordenada"
INICIO_YA_ORDENADO = "ya_ordenada"
INICIO_YA_INICIADO = "ya_iniciada"
INICIO_CANTIDAD_INVALIDA = "cantidad_invalida"

# Ciclo en marcha (informe 4.8). `inactivo` = no hay ciclo (nunca se inició, ya terminó o
# la PC se reinició); `activo` = la Orange Pi pregunta antes de cada objeto y sigue;
# `deteniendo` = el regente pidió detener y la Orange Pi aún no lo ha recogido.
CICLO_INACTIVO = "inactivo"
CICLO_ACTIVO = "activo"
CICLO_DETENIENDO = "deteniendo"
MAX_CANTIDAD_CAJAS = 9999
FIN_POR_REGENTE = "detenido_por_regente"
FIN_POR_CANTIDAD = "cantidad_alcanzada"

# Resultados de ordenar_detencion()
DETENCION_ORDENADA = "ordenada"
DETENCION_YA_ORDENADA = "ya_ordenada"
ORDEN_CANCELADA = "orden_cancelada"       # la Orange Pi aún no había recogido el inicio
SIN_CICLO = "sin_ciclo"

# Una Orange Pi sin contacto durante más de esto se muestra como desconectada. Consulta
# cada 2 s mientras espera y una vez por objeto durante el ciclo (~20 s o más con la
# mecánica real): valor inicial, calibrar.
VENTANA_CONEXION_ORANGEPI_S = 45.0

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
        # Orden de inicio del ciclo (una sola, de un solo uso; ver ordenar_inicio)
        self._orden = {"estado": ORDEN_SIN_ORDEN, "ts_ordenada": None, "ts_entregada": None}
        self._ts_ultima_consulta_orden = None
        self._cantidad_ordenada = None      # cantidad fijada al pulsar "Iniciar", aún sin recoger
        self._ciclo = {"estado": CICLO_INACTIVO, "cantidad": None, "iniciadas": 0,
                       "motivo_fin": None, "ts_fin": None}
        self._contacto = {"ip": None, "ts": None}   # último mensaje válido de la Orange Pi

    def registrar_contacto(self, ip: str) -> None:
        """La Orange Pi envió un mensaje válido (lote o consulta): sirve de indicador de
        conexión en el panel."""
        with self._lock:
            self._contacto = {"ip": ip, "ts": time.time()}

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

    # -- orden de inicio del ciclo (consulta S-06, opción 1; informe 4.7) ----------
    def _vencer_orden_locked(self) -> None:
        """Una orden que la Orange Pi no recoge a tiempo vence: así no puede arrancar
        el ciclo en un arranque posterior sin que nadie lo decida."""
        o = self._orden
        if o["estado"] == ORDEN_ORDENADA and time.time() - o["ts_ordenada"] >= VIGENCIA_ORDEN_CICLO_S:
            o.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None)

    def ordenar_inicio(self, cantidad: int | None = None) -> str:
        """El regente pulsó "Iniciar recorrido". `cantidad` = cuántas cajas procesar
        (None = sin límite: sigue hasta que el regente pulse "Detener")."""
        with self._lock:
            if cantidad is not None and (isinstance(cantidad, bool) or not isinstance(cantidad, int)
                                         or not 1 <= cantidad <= MAX_CANTIDAD_CAJAS):
                return INICIO_CANTIDAD_INVALIDA
            self._vencer_orden_locked()
            o = self._orden
            if o["estado"] == ORDEN_INICIADA:
                return INICIO_YA_INICIADO
            if o["estado"] == ORDEN_ORDENADA:
                return INICIO_YA_ORDENADO
            o.update(estado=ORDEN_ORDENADA, ts_ordenada=time.time())
            self._cantidad_ordenada = cantidad
            return INICIO_ORDENADO

    def consultar_orden_ciclo(self) -> dict:
        """Respuesta a la consulta de la Orange Pi. Entregar `iniciar` se confirma
        aparte, con confirmar_entrega_orden(), una vez enviada la respuesta: si el
        envío falla, la orden sigue vigente para la siguiente consulta."""
        with self._lock:
            self._ts_ultima_consulta_orden = time.time()
            self._vencer_orden_locked()
            o = self._orden
            if o["estado"] == ORDEN_INICIADA:
                # La Orange Pi solo pregunta mientras espera: si vuelve a preguntar
                # después de recibir la orden, reinició y necesita una orden nueva.
                o.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None, ts_entregada=None)
                self._ciclo.update(estado=CICLO_INACTIVO)
            orden = ORDEN_CICLO_INICIAR if o["estado"] == ORDEN_ORDENADA else ORDEN_CICLO_ESPERANDO
            return {CLAVE_ORDEN_CICLO_JSON: orden}

    def confirmar_entrega_orden(self) -> None:
        with self._lock:
            o = self._orden
            if o["estado"] == ORDEN_ORDENADA:
                o.update(estado=ORDEN_INICIADA, ts_entregada=time.time())
                self._ciclo = {"estado": CICLO_ACTIVO, "cantidad": self._cantidad_ordenada,
                               "iniciadas": 0, "motivo_fin": None, "ts_fin": None}

    # -- continuar o detener entre objetos (informe 4.8) ---------------------------
    def ordenar_detencion(self) -> str:
        """El regente pulsó "Detener". No interrumpe una caja en vuelo: la Orange Pi lo
        recoge en su próxima consulta, antes del siguiente objeto."""
        with self._lock:
            c = self._ciclo
            if c["estado"] == CICLO_ACTIVO:
                c["estado"] = CICLO_DETENIENDO
                return DETENCION_ORDENADA
            if c["estado"] == CICLO_DETENIENDO:
                return DETENCION_YA_ORDENADA
            self._vencer_orden_locked()
            if self._orden["estado"] == ORDEN_ORDENADA:
                # Pulsó "Iniciar" y se arrepintió antes de que la Orange Pi la recogiera.
                self._orden.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None)
                return ORDEN_CANCELADA
            return SIN_CICLO

    def consultar_estado_ciclo(self) -> dict:
        """Respuesta a la consulta de la Orange Pi antes de cada objeto. Cada `continuar`
        cuenta una caja iniciada; al llegar a la cantidad fijada, o si el regente pidió
        detener, responde `detener` y deja el ciclo inactivo."""
        with self._lock:
            c = self._ciclo
            if c["estado"] == CICLO_INACTIVO:
                # Sin ciclo conocido (p. ej. la PC se reinició): nada sigue sin decisión humana.
                return {CLAVE_CICLO_JSON: CICLO_DETENER}
            cantidad_alcanzada = c["cantidad"] is not None and c["iniciadas"] >= c["cantidad"]
            if c["estado"] == CICLO_DETENIENDO or cantidad_alcanzada:
                c.update(estado=CICLO_INACTIVO, ts_fin=time.time(),
                         motivo_fin=FIN_POR_REGENTE if c["estado"] == CICLO_DETENIENDO
                         else FIN_POR_CANTIDAD)
                self._orden.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None, ts_entregada=None)
                return {CLAVE_CICLO_JSON: CICLO_DETENER}
            c["iniciadas"] += 1
            return {CLAVE_CICLO_JSON: CICLO_CONTINUAR}

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
            self._vencer_orden_locked()
            o = self._orden
            restante = (max(0, int(VIGENCIA_ORDEN_CICLO_S - (time.time() - o["ts_ordenada"])))
                        if o["estado"] == ORDEN_ORDENADA else None)
            ahora = time.time()
            ts_contacto = self._contacto["ts"]
            return {
                "ciclo": dict(self._ciclo),
                "orangepi": {
                    "ip": self._contacto["ip"],
                    "ts_ultimo_contacto": ts_contacto,
                    "conectada": ts_contacto is not None
                                 and ahora - ts_contacto <= VENTANA_CONEXION_ORANGEPI_S,
                },
                "orden_ciclo": {
                    "estado": o["estado"],
                    "restante_s": restante,
                    "ts_entregada": o["ts_entregada"],
                    "ts_ultima_consulta": self._ts_ultima_consulta_orden,
                },
                "ts_servidor": time.time(),
                "alarma_activa": any(a["activa"] for a in alarmas),
                "max_lotes_con_fotos": MAX_LOTES_CON_FOTOS,
                "lotes": lotes,
                "alarmas": alarmas,
                "destinos_sugeridos": vistos + [DESTINO_DESCARTE],
            }


estado = EstadoPanel()
