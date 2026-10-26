"""
Estado en memoria del panel de control: historial de lotes, fotos del lote y
alarmas. Es el único punto donde `server.py` (escritor) y `panel_web.py`
(lector) se tocan.

Las imágenes viven SOLO en RAM (ver pc/CLAUDE.md: nunca se escriben a disco)
y solo se conservan las de los últimos MAX_LOTES_CON_FOTOS lotes.
"""
import itertools
from datetime import date
import logging
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

log_auditoria = logging.getLogger("auditoria")

# Quién queda anotado en el registro de eventos. Los eventos automáticos (lote recibido,
# alarma abierta...) los hace el "sistema"; las acciones del panel (iniciar, detener,
# resolver una alarma) las hace una persona. Mientras no haya inicio de sesión (Parte B del
# plan de usuarios) no se sabe quién es: queda como "operador sin sesión".
RESPONSABLE_SISTEMA = "sistema"
RESPONSABLE_SIN_SESION = "operador sin sesión"

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
        self._orden = {"estado": ORDEN_SIN_ORDEN, "ts_ordenada": None, "ts_entregada": None,
                       "ordenada_por": None}
        self._ts_ultima_consulta_orden = None
        self._cantidad_ordenada = None      # cantidad fijada al pulsar "Iniciar", aún sin recoger
        self._ciclo = {"estado": CICLO_INACTIVO, "cantidad": None, "iniciadas": 0,
                       "motivo_fin": None, "ts_fin": None,
                       "iniciado_por": None, "detencion_ordenada_por": None}
        self._contacto = {"ip": None, "ts": None}   # último mensaje válido de la Orange Pi
        self._auditoria = None                      # registro de eventos (auditoria.py), opcional
        self._inventario = None                     # inventario de cajas (inventario.py), opcional
        self._arranque = None                       # id del evento "sistema_iniciado" de esta ejecución

    # -- registro de eventos (log) -------------------------------------------------
    def conectar_auditoria(self, auditoria) -> None:
        """Conecta el registro de eventos persistente. Sin él, el estado funciona igual."""
        self._auditoria = auditoria

    def conectar_inventario(self, inventario) -> None:
        """Conecta el inventario: cada lote recibido crea su caja y cada decisión del regente le
        fija el destino, en la misma transacción que su evento. Requiere el registro conectado."""
        self._inventario = inventario

    def _con_arranque(self, detalle: dict) -> dict:
        """El detalle con el id de arranque (ver _evento): para eventos que escribe el inventario."""
        if self._arranque is not None:
            detalle = {**detalle, "arranque": self._arranque} if "arranque" not in detalle else detalle
        return detalle

    def _evento(self, tipo: str, responsable: str, **detalle):
        """Anota un evento. Se llama DENTRO del candado, así el orden del registro es el de
        las transiciones del estado. Un fallo al registrar nunca rompe el flujo físico:
        se deja en el log técnico y el ciclo sigue (la Orange Pi no puede quedar esperando)."""
        if self._auditoria is None:
            return None
        if self._arranque is not None:
            # Los ids de lote y de alarma reinician en 1 con cada arranque del servidor:
            # el de arranque desambigua "lote #3" entre ejecuciones distintas.
            detalle.setdefault("arranque", self._arranque)
        try:
            return self._auditoria.registrar(tipo, responsable, detalle)
        except Exception:
            log_auditoria.exception("no se pudo registrar el evento %s", tipo)
            return None

    def registrar_arranque(self, **detalle):
        """Primer evento de cada ejecución. Devuelve su id (los demás lo llevan como `arranque`)."""
        with self._lock:
            self._arranque = None
            self._arranque = self._evento("sistema_iniciado", RESPONSABLE_SISTEMA, **detalle)
            return self._arranque

    def registrar_evento(self, tipo: str, responsable: str = RESPONSABLE_SISTEMA, **detalle):
        """Evento genérico (arranque del servicio, apagado...) desde fuera del estado."""
        with self._lock:
            return self._evento(tipo, responsable, **detalle)

    def registrar_contacto(self, ip: str) -> None:
        """La Orange Pi envió un mensaje válido (lote o consulta): sirve de indicador de
        conexión en el panel."""
        with self._lock:
            self._contacto = {"ip": ip, "ts": time.time()}

    @staticmethod
    def _datos_de(reconocimiento) -> dict | None:
        """Lo que leyó el módulo de IA (ocr_interface.ResultadoReconocimiento), o None si no hubo lectura
        real (modo placeholder o lote vacío)."""
        if reconocimiento is None or getattr(reconocimiento, "origen", None) != "ia":
            return None
        return {k: getattr(reconocimiento, k) for k in
                ("medicamento", "gramaje", "numero_lote", "vencimiento", "confianza", "motivo", "segundos")}

    @staticmethod
    def _lectura_visible(datos: dict | None) -> dict | None:
        """Lo que leyó la IA de un lote, para la pestaña Inicio (None si no hubo lectura real). Agrega los
        días que faltan para el vencimiento, contados desde hoy."""
        if not datos:
            return None
        lectura = {k: datos.get(k) for k in ("medicamento", "gramaje", "numero_lote", "vencimiento", "confianza", "motivo")}
        lectura["dias_para_vencer"] = None
        if lectura["vencimiento"]:
            try:
                lectura["dias_para_vencer"] = (date.fromisoformat(lectura["vencimiento"]) - date.today()).days
            except ValueError:
                pass
        return lectura

    def registrar_lote(self, origen: str, imagenes: list, resultado: str, datos=None) -> int:
        """`datos`: el ResultadoReconocimiento del lote; si es del módulo de IA, sus campos llenan la caja."""
        datos = self._datos_de(datos)
        with self._lock:
            lote_id = next(self._ids_lote)
            self._lotes.append({
                "id": lote_id,
                "ts": time.time(),
                "origen": origen,
                "resultado": resultado,
                "n_imagenes": len(imagenes),
                "imagenes": list(imagenes),
                "lectura": datos,
            })
            for viejo in list(self._lotes)[:-MAX_LOTES_CON_FOTOS]:
                viejo["imagenes"] = None
            detalle = dict(lote_id=lote_id, origen=origen, n_imagenes=len(imagenes), resultado=resultado)
            if datos is not None:        # lo que leyó la IA queda también en el registro, aunque luego se corrija
                detalle["lectura"] = {k: v for k, v in datos.items() if v is not None}
            registrada = False
            if self._inventario is not None and self._auditoria is not None:
                try:
                    # La caja se anota a nombre de quien tiene el recorrido en marcha, si lo hay.
                    turno_de = self._ciclo.get("iniciado_por") if self._ciclo["estado"] != CICLO_INACTIVO else None
                    self._inventario.registrar_caja(
                        arranque=self._arranque, captura_id=lote_id, origen=origen,
                        n_imagenes=len(imagenes), resultado=resultado, turno_de=turno_de, datos=datos,
                        evento=("lote_recibido", RESPONSABLE_SISTEMA, self._con_arranque(detalle)))
                    registrada = True
                except Exception:
                    log_auditoria.exception("no se pudo registrar la caja del lote %s", lote_id)
            if not registrada:        # sin inventario, o falló: el evento se deja igual, sin caja
                self._evento("lote_recibido", RESPONSABLE_SISTEMA, **detalle)
            return lote_id

    def registrar_alarma(self, motivo: str, lote_id: int | None = None, detalle: str | None = None) -> int:
        """`detalle`: por qué, en palabras del reconocimiento (p. ej. "lote:ausente"), para el regente."""
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
                "detalle": detalle,
            })
            self._evento("alarma_abierta", RESPONSABLE_SISTEMA, alarma_id=alarma_id,
                         motivo=motivo, lote_id=lote_id, **({"detalle": detalle} if detalle else {}))
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

    def consultar_decision(self, ip: str | None = None) -> dict:
        """Respuesta para la consulta de la Orange Pi (no consume la decisión). `ip` es la de
        quien consulta, solo para el registro de eventos."""
        with self._lock:
            d = self._decision
            if d is None:
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_NINGUNA}
            if d["estado"] == ESTADO_DECISION_RESUELTA:
                if not d.get("entregada"):
                    d["entregada"] = True       # solo la PRIMERA vez: es el hecho que se audita
                    self._evento("decision_entregada", RESPONSABLE_SISTEMA,
                                 alarma_id=d["alarma_id"], destino=d["destino"], ip_orangepi=ip)
                return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_RESUELTA,
                        CLAVE_DESTINO_JSON: d["destino"]}
            return {CLAVE_ESTADO_DECISION_JSON: ESTADO_DECISION_PENDIENTE}

    def resolver_alarma(self, alarma_id: int, destino: str | None,
                        responsable: str = RESPONSABLE_SIN_SESION) -> str:
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
                alarma["resuelta_por"] = responsable
                # Qué alarma era, qué se decidió (TIPO_X o DESCARTE) y quién lo decidió.
                detalle = dict(alarma_id=alarma_id, motivo=alarma["motivo"], lote_id=alarma["lote_id"],
                               destino=destino if espera else None, decision_aplicada=espera)
                anotada = False
                if espera and self._inventario is not None and self._auditoria is not None:
                    try:             # ese destino pasa a ser el de la caja, junto con el evento
                        self._inventario.definir_destino(
                            arranque=self._arranque, captura_id=alarma["lote_id"], destino=destino,
                            responsable=responsable,
                            evento=("alarma_resuelta", responsable, self._con_arranque(detalle)))
                        anotada = True
                    except Exception:
                        log_auditoria.exception("no se pudo fijar el destino de la caja de la alarma %s", alarma_id)
                if not anotada:
                    self._evento("alarma_resuelta" if espera else "alarma_cerrada", responsable, **detalle)
            return RESUELTA_CON_DECISION if espera else CERRADA_SIN_DECISION

    # -- orden de inicio del ciclo (consulta S-06, opción 1; informe 4.7) ----------
    def _vencer_orden_locked(self) -> None:
        """Una orden que la Orange Pi no recoge a tiempo vence: así no puede arrancar
        el ciclo en un arranque posterior sin que nadie lo decida."""
        o = self._orden
        if o["estado"] == ORDEN_ORDENADA and time.time() - o["ts_ordenada"] >= VIGENCIA_ORDEN_CICLO_S:
            self._evento("ciclo_orden_vencida", RESPONSABLE_SISTEMA,
                         ordenada_por=o.get("ordenada_por"), cantidad=self._cantidad_ordenada)
            o.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None, ordenada_por=None)

    def ordenar_inicio(self, cantidad: int | None = None,
                       responsable: str = RESPONSABLE_SIN_SESION) -> str:
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
            o.update(estado=ORDEN_ORDENADA, ts_ordenada=time.time(), ordenada_por=responsable)
            self._cantidad_ordenada = cantidad
            self._evento("ciclo_inicio_ordenado", responsable, cantidad=cantidad,
                         vigencia_s=VIGENCIA_ORDEN_CICLO_S)
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
                if self._ciclo["estado"] != CICLO_INACTIVO:
                    self._evento("ciclo_interrumpido", RESPONSABLE_SISTEMA,
                                 motivo="la Orange Pi volvió a pedir orden (se reinició)",
                                 cajas_iniciadas=self._ciclo["iniciadas"],
                                 iniciado_por=self._ciclo.get("iniciado_por"))
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
                               "iniciadas": 0, "motivo_fin": None, "ts_fin": None,
                               "iniciado_por": o.get("ordenada_por"), "detencion_ordenada_por": None}
                # El ciclo corre bajo la autoridad de quien dio la orden de inicio.
                self._evento("ciclo_iniciado", o.get("ordenada_por") or RESPONSABLE_SISTEMA,
                             cantidad=self._cantidad_ordenada)

    # -- continuar o detener entre objetos (informe 4.8) ---------------------------
    def ordenar_detencion(self, responsable: str = RESPONSABLE_SIN_SESION) -> str:
        """El regente pulsó "Detener". No interrumpe una caja en vuelo: la Orange Pi lo
        recoge en su próxima consulta, antes del siguiente objeto."""
        with self._lock:
            c = self._ciclo
            if c["estado"] == CICLO_ACTIVO:
                c["estado"] = CICLO_DETENIENDO
                c["detencion_ordenada_por"] = responsable
                self._evento("ciclo_detencion_ordenada", responsable,
                             cajas_iniciadas=c["iniciadas"], cantidad=c["cantidad"])
                return DETENCION_ORDENADA
            if c["estado"] == CICLO_DETENIENDO:
                return DETENCION_YA_ORDENADA
            self._vencer_orden_locked()
            if self._orden["estado"] == ORDEN_ORDENADA:
                # Pulsó "Iniciar" y se arrepintió antes de que la Orange Pi la recogiera.
                self._evento("ciclo_orden_cancelada", responsable,
                             ordenada_por=self._orden.get("ordenada_por"),
                             cantidad=self._cantidad_ordenada)
                self._orden.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None, ordenada_por=None)
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
                por_regente = c["estado"] == CICLO_DETENIENDO
                c.update(estado=CICLO_INACTIVO, ts_fin=time.time(),
                         motivo_fin=FIN_POR_REGENTE if por_regente else FIN_POR_CANTIDAD)
                self._orden.update(estado=ORDEN_SIN_ORDEN, ts_ordenada=None, ts_entregada=None,
                                   ordenada_por=None)
                # Si lo detuvo una persona, es su acción; si fue por la cantidad, del sistema.
                self._evento("ciclo_detenido",
                             (c.get("detencion_ordenada_por") if por_regente else None)
                             or RESPONSABLE_SISTEMA,
                             motivo=c["motivo_fin"], cajas_iniciadas=c["iniciadas"],
                             cantidad=c["cantidad"], iniciado_por=c.get("iniciado_por"))
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
                "lectura": self._lectura_visible(l.get("lectura")),
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
                    "ordenada_por": o.get("ordenada_por"),
                    "ts_ultima_consulta": self._ts_ultima_consulta_orden,
                },
                "ts_servidor": time.time(),
                "arranque": self._arranque,      # para saber si una caja del inventario aún tiene sus fotos en RAM
                "alarma_activa": any(a["activa"] for a in alarmas),
                "max_lotes_con_fotos": MAX_LOTES_CON_FOTOS,
                "lotes": lotes,
                "alarmas": alarmas,
                "destinos_sugeridos": vistos + [DESTINO_DESCARTE],
            }


estado = EstadoPanel()
