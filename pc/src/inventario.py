"""
Inventario: una fila por CAJA auditada (Parte C del plan de inventario y usuarios).

Cada lote de fotos que llega de la Orange Pi crea una fila. Las columnas con los datos que
extrae el reconocimiento (medicamento, gramaje, lote del medicamento, vencimiento) están
vacías con el reconocimiento en modo placeholder; con PC_RECONOCIMIENTO=ia (Parte D) el OCR las
llena SIN pedir confirmación (`datos_origen` = 'ocr', con su `confianza`) y una persona con turno
abierto puede corregirlas o completarlas a mano (`datos_origen` pasa a 'manual'; queda en el registro).

Dos cosas que NO se confunden:
  - `captura_id`: el número del lote de FOTOS de esa ejecución del servidor (reinicia en 1 en
    cada arranque; por eso va con `arranque`). Es el "Lote #N" que ya conocía el panel.
  - `numero_lote`: el lote del MEDICAMENTO impreso en la caja (lo que lee el OCR).

Clasificación: `resultado` es lo que respondió el reconocimiento (TIPO_X o
ERROR_REVISION_MANUAL) y no se edita; `destino` es el destino FINAL: igual al resultado si fue
automático, o el que decidió el regente (TIPO_X o DESCARTE) si pasó por revisión manual
(vacío mientras esa decisión está pendiente).

Eliminar es LÓGICO (la fila se oculta y se puede restaurar), y cada cambio hecho por una persona
(editar, eliminar, restaurar) se confirma en la MISMA transacción que su evento en el registro
(auditoria.py): no hay cambio sin evento. La tabla vive en el mismo archivo que el registro.

La búsqueda ignora mayúsculas y tildes ("acetaminofen" encuentra "Acetaminofén"): se calcula en
Python (columna `busqueda`) porque lower() de SQLite solo entiende ASCII.
"""
import logging
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import RESULTADO_ERROR_REVISION_MANUAL  # noqa: E402

from auditoria import Auditoria, _escapar_like  # noqa: E402

log = logging.getLogger("inventario")

RESPONSABLE_SISTEMA = "sistema"
CAMPOS_EDITABLES = ("medicamento", "gramaje", "numero_lote", "vencimiento", "notas")
LARGO_MAXIMO = {"medicamento": 120, "gramaje": 40, "numero_lote": 40, "notas": 500}
LARGO_MAXIMO_MOTIVO = 200
LIMITE_DEFECTO, LIMITE_MAXIMO = 50, 200
LIMITE_EXPORTACION = 100_000
PATRON_DESTINO = re.compile(r"^[A-Z0-9_]{1,32}$")
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # se permite \n y \t en las notas
DESTINO_PENDIENTE = "pendiente"                            # valor del filtro: aún sin decisión del regente

# Columnas por las que se puede ordenar (lista blanca: nunca se arma SQL con lo que llega del cliente).
ORDENES = {
    "id": "id", "auditada": "creada_ms", "medicamento": "medicamento COLLATE NOCASE",
    "gramaje": "gramaje COLLATE NOCASE", "numero_lote": "numero_lote COLLATE NOCASE",
    "vencimiento": "vencimiento", "destino": "destino", "resultado": "resultado",
    "turno_de": "turno_de COLLATE NOCASE",
}

# Columnas que pueden estar vacías: al ordenar, las vacías van SIEMPRE al final (en SQLite NULL es el valor
# más pequeño y, sin esto, "ascendente" mostraría primero las cajas sin datos).
NULOS_AL_FINAL = ("medicamento", "gramaje", "numero_lote", "vencimiento", "destino", "turno_de")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS cajas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    creada_ms       INTEGER NOT NULL,
    arranque        INTEGER,
    captura_id      INTEGER,
    origen          TEXT,
    n_imagenes      INTEGER NOT NULL,
    resultado       TEXT    NOT NULL,
    destino         TEXT,
    destino_por     TEXT,
    destino_ms      INTEGER,
    turno_de        TEXT,
    medicamento     TEXT,
    gramaje         TEXT,
    numero_lote     TEXT,
    vencimiento     TEXT,
    notas           TEXT,
    datos_origen    TEXT,
    editada         INTEGER NOT NULL DEFAULT 0,
    eliminada       INTEGER NOT NULL DEFAULT 0,
    eliminada_ms    INTEGER,
    eliminada_por   TEXT,
    eliminada_motivo TEXT,
    busqueda        TEXT    NOT NULL DEFAULT '',
    confianza       REAL,
    motivo_ocr      TEXT
);
CREATE INDEX IF NOT EXISTS cajas_creada ON cajas (creada_ms);
CREATE INDEX IF NOT EXISTS cajas_vencimiento ON cajas (vencimiento);
CREATE INDEX IF NOT EXISTS cajas_captura ON cajas (arranque, captura_id);
"""

_COLUMNAS = ("id, creada_ms, arranque, captura_id, origen, n_imagenes, resultado, destino, destino_por, "
             "destino_ms, turno_de, medicamento, gramaje, numero_lote, vencimiento, notas, datos_origen, "
             "editada, eliminada, eliminada_ms, eliminada_por, eliminada_motivo, confianza, motivo_ocr")

# Columnas añadidas después de la primera versión de la tabla (la base de una PC que ya corría las necesita).
_COLUMNAS_NUEVAS = (("confianza", "REAL"), ("motivo_ocr", "TEXT"))


class ErrorInventario(Exception):
    """Error de negocio con un `codigo` estable (el panel lo traduce a HTTP y a texto)."""

    def __init__(self, codigo: str, **detalle):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalle = detalle


def normalizar(texto) -> str:
    """Minúsculas y sin tildes, para comparar y buscar."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))
    return " ".join(sin_tildes.casefold().split())


def _valor_campo(campo: str, valor):
    """Valida y normaliza el valor de un campo editable (None o "" = borrarlo)."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorInventario("valor_invalido", campo=campo)
    v = valor.strip() if campo == "notas" else " ".join(valor.split())
    if v == "":
        return None
    maximo = LARGO_MAXIMO.get(campo)                  # el vencimiento no tiene largo: se valida como fecha
    if (maximo is not None and len(v) > maximo) or _RE_CONTROL.search(v):
        raise ErrorInventario("valor_invalido", campo=campo)
    if campo == "vencimiento":
        try:
            f = date.fromisoformat(v)
        except ValueError:
            raise ErrorInventario("valor_invalido", campo=campo) from None
        if not 1990 <= f.year <= 2100 or v != f.isoformat():
            raise ErrorInventario("valor_invalido", campo=campo)
    return v


def _limpiar_dato_ocr(campo: str, valor):
    """Un dato leído por el OCR: texto limpio y recortado, o None si no sirve (una fecha imposible no se guarda)."""
    if not isinstance(valor, str):
        return None
    v = " ".join(_RE_CONTROL.sub(" ", valor).split())
    if not v:
        return None
    if campo == "vencimiento":
        try:
            f = date.fromisoformat(v)
        except ValueError:
            return None
        return v if 1990 <= f.year <= 2100 and v == f.isoformat() else None
    return v[:LARGO_MAXIMO[campo]]


class Inventario:
    def __init__(self, auditoria: Auditoria, hoy=date.today, ahora_ms=None):
        self._aud = auditoria
        self._hoy = hoy
        self._ahora_ms = ahora_ms or (lambda: int(datetime.now().timestamp() * 1000))
        with self._aud.transaccion() as tx:
            for sentencia in _ESQUEMA.strip().split(";"):
                if sentencia.strip():
                    tx.execute(sentencia)
            existentes = {f[1] for f in tx.execute("PRAGMA table_info(cajas)").fetchall()}
            for nombre, tipo in _COLUMNAS_NUEVAS:          # migración de una tabla creada antes
                if nombre not in existentes:
                    tx.execute(f"ALTER TABLE cajas ADD COLUMN {nombre} {tipo}")

    # ------------------------------------------------------------------ utilidades
    def _a_dict(self, f) -> dict:
        d = dict(zip(_COLUMNAS.replace(" ", "").split(","), f))
        for k in ("editada", "eliminada"):
            d[k] = bool(d[k])
        d["dias_para_vencer"] = None
        if d["vencimiento"]:
            d["dias_para_vencer"] = (date.fromisoformat(d["vencimiento"]) - self._hoy()).days
        return d

    def _reindexar(self, tx, caja_id: int) -> None:
        f = tx.execute("SELECT id, medicamento, gramaje, numero_lote, notas, destino, resultado, turno_de, "
                       "origen, captura_id, motivo_ocr FROM cajas WHERE id = ?", (caja_id,)).fetchone()
        texto = normalizar(" ".join(str(x) for x in (f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8],
                                                       f"captura {f[9]}" if f[9] else "", f[10])
                                    if x not in (None, "")))
        tx.execute("UPDATE cajas SET busqueda = ? WHERE id = ?", (texto, caja_id))

    def obtener(self, caja_id: int) -> dict | None:
        f = self._aud.leer(f"SELECT {_COLUMNAS} FROM cajas WHERE id = ?", (caja_id,))
        return self._a_dict(f[0]) if f else None

    # ------------------------------------------------------------------ altas automáticas
    @staticmethod
    def _campos_ocr(datos: dict | None) -> dict:
        """Lo que leyó el reconocimiento, limpio y con los largos de la tabla. El OCR puede devolver texto
        largo o sucio: aquí se recorta en vez de rechazar (a diferencia de lo que escribe una persona)."""
        datos = datos or {}
        campos = {c: _limpiar_dato_ocr(c, datos.get(c)) for c in ("medicamento", "gramaje", "numero_lote", "vencimiento")}
        conf = datos.get("confianza")
        campos["confianza"] = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0 <= conf <= 1 else None
        motivo = datos.get("motivo")
        campos["motivo_ocr"] = " ".join(motivo.split())[:200] if isinstance(motivo, str) and motivo.strip() else None
        return campos

    def registrar_caja(self, *, arranque, captura_id, origen, n_imagenes, resultado, turno_de=None,
                       datos: dict | None = None, evento: tuple) -> int:
        """Una caja nueva por cada lote recibido, y su evento `lote_recibido`, en una transacción.
        `datos` = lo que leyó el reconocimiento (medicamento, gramaje, numero_lote, vencimiento, confianza,
        motivo); `evento` = (tipo, responsable, detalle), al que se le añade `caja_id`."""
        manual = resultado == RESULTADO_ERROR_REVISION_MANUAL
        c = self._campos_ocr(datos)
        leyo = any(c[k] for k in ("medicamento", "gramaje", "numero_lote", "vencimiento"))
        with self._aud.transaccion() as tx:
            cur = tx.execute(
                "INSERT INTO cajas (creada_ms, arranque, captura_id, origen, n_imagenes, resultado, destino, "
                "destino_por, turno_de, medicamento, gramaje, numero_lote, vencimiento, datos_origen, "
                "confianza, motivo_ocr) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._ahora_ms(), arranque, captura_id, origen, n_imagenes, resultado,
                 None if manual else resultado, None if manual else RESPONSABLE_SISTEMA, turno_de,
                 c["medicamento"], c["gramaje"], c["numero_lote"], c["vencimiento"],
                 "ocr" if leyo else None, c["confianza"], c["motivo_ocr"]))
            caja_id = cur.lastrowid
            self._reindexar(tx, caja_id)
            tipo, responsable, detalle = evento
            tx.registrar(tipo, responsable, {**detalle, "caja_id": caja_id})
        return caja_id

    def definir_destino(self, *, arranque, captura_id, destino: str, responsable: str, evento: tuple):
        """El regente resolvió la alarma de esa captura: ese es el destino final de la caja.
        Siempre escribe el evento (aunque la caja no exista, p. ej. una alarma sin lote); devuelve
        el id de la caja o None."""
        with self._aud.transaccion() as tx:
            f = tx.execute("SELECT id FROM cajas WHERE arranque IS ? AND captura_id = ? ORDER BY id DESC",
                           (arranque, captura_id)).fetchone() if captura_id is not None else None
            caja_id = f[0] if f else None
            if caja_id:
                tx.execute("UPDATE cajas SET destino = ?, destino_por = ?, destino_ms = ? WHERE id = ?",
                           (destino, responsable, self._ahora_ms(), caja_id))
                self._reindexar(tx, caja_id)
            tipo, quien, detalle = evento
            tx.registrar(tipo, quien, {**detalle, "caja_id": caja_id} if caja_id else detalle)
        return caja_id

    # ------------------------------------------------------------------ cambios hechos por personas
    @staticmethod
    def _contexto(f) -> dict:
        """Lo que identifica a la caja en el registro, aunque luego cambie o se elimine."""
        return {"caja_id": f[0], "medicamento": f[1], "numero_lote": f[2], "captura_id": f[3]}

    def editar(self, actor: str, caja_id: int, cambios: dict) -> dict:
        """Corrige o completa los datos de una caja. Solo campos de CAMPOS_EDITABLES; el resultado y
        el destino no se editan. Devuelve la caja (sin escribir nada si no cambió nada)."""
        if not isinstance(cambios, dict) or not cambios:
            raise ErrorInventario("sin_cambios")
        for campo in cambios:
            if campo not in CAMPOS_EDITABLES:
                raise ErrorInventario("campo_no_editable", campo=str(campo)[:40])
        nuevos = {campo: _valor_campo(campo, valor) for campo, valor in cambios.items()}
        with self._aud.transaccion() as tx:
            f = tx.execute("SELECT id, medicamento, numero_lote, captura_id, eliminada, "
                           + ", ".join(CAMPOS_EDITABLES) + " FROM cajas WHERE id = ?", (caja_id,)).fetchone()
            if f is None:
                raise ErrorInventario("caja_inexistente")
            if f[4]:
                raise ErrorInventario("caja_eliminada")
            actuales = dict(zip(CAMPOS_EDITABLES, f[5:]))
            diferencias = {c: [actuales[c], v] for c, v in nuevos.items() if actuales[c] != v}
            if diferencias:
                sets = ", ".join(f"{c} = ?" for c in diferencias)
                tx.execute(f"UPDATE cajas SET {sets}, editada = 1, datos_origen = 'manual' WHERE id = ?",
                           (*[v for _, v in diferencias.values()], caja_id))
                self._reindexar(tx, caja_id)
                tx.registrar("caja_editada", actor, {**self._contexto(f), "cambios": diferencias})
        return self.obtener(caja_id)

    def eliminar(self, actor: str, caja_id: int, motivo: str | None = None) -> dict:
        """Eliminación LÓGICA: la fila se oculta (y se puede restaurar); el evento conserva qué era."""
        if motivo is not None and (not isinstance(motivo, str) or _RE_CONTROL.search(motivo)
                                   or len(motivo.strip()) > LARGO_MAXIMO_MOTIVO):
            raise ErrorInventario("valor_invalido", campo="motivo")
        motivo = (motivo or "").strip() or None
        with self._aud.transaccion() as tx:
            f = tx.execute("SELECT id, medicamento, numero_lote, captura_id, eliminada FROM cajas WHERE id = ?",
                           (caja_id,)).fetchone()
            if f is None:
                raise ErrorInventario("caja_inexistente")
            if f[4]:
                raise ErrorInventario("ya_eliminada")
            tx.execute("UPDATE cajas SET eliminada = 1, eliminada_ms = ?, eliminada_por = ?, eliminada_motivo = ? "
                       "WHERE id = ?", (self._ahora_ms(), actor, motivo, caja_id))
            tx.registrar("caja_eliminada", actor, {**self._contexto(f), "motivo": motivo})
        return self.obtener(caja_id)

    def restaurar(self, actor: str, caja_id: int) -> dict:
        with self._aud.transaccion() as tx:
            f = tx.execute("SELECT id, medicamento, numero_lote, captura_id, eliminada FROM cajas WHERE id = ?",
                           (caja_id,)).fetchone()
            if f is None:
                raise ErrorInventario("caja_inexistente")
            if not f[4]:
                raise ErrorInventario("no_eliminada")
            tx.execute("UPDATE cajas SET eliminada = 0, eliminada_ms = NULL, eliminada_por = NULL, "
                       "eliminada_motivo = NULL WHERE id = ?", (caja_id,))
            tx.registrar("caja_restaurada", actor, self._contexto(f))
        return self.obtener(caja_id)

    # ------------------------------------------------------------------ consultas
    def _donde(self, *, q=None, destino=None, revision=False, venc=None, mostrar="activas",
               desde_ms=None, hasta_ms=None) -> tuple[str, list]:
        donde, args = [], []
        if mostrar == "activas":
            donde.append("eliminada = 0")
        elif mostrar == "eliminadas":
            donde.append("eliminada = 1")
        elif mostrar != "todas":
            raise ErrorInventario("parametro_invalido", parametro="mostrar")
        for palabra in normalizar(q or "").split():             # todas las palabras deben aparecer
            donde.append("busqueda LIKE ? ESCAPE '\\'")
            args.append("%" + _escapar_like(palabra) + "%")
        if destino == DESTINO_PENDIENTE:
            donde.append("destino IS NULL")
        elif destino:
            donde.append("destino = ?")
            args.append(destino)
        if revision:
            donde.append("resultado = ?")
            args.append(RESULTADO_ERROR_REVISION_MANUAL)
        if venc:
            hoy = self._hoy()
            if venc == "vencidas":
                donde.append("vencimiento < ?")
                args.append(hoy.isoformat())
            elif venc == "sin":
                donde.append("vencimiento IS NULL")
            elif venc in ("30", "90", "180"):
                donde.append("vencimiento >= ? AND vencimiento <= ?")
                args.extend([hoy.isoformat(), (hoy + timedelta(days=int(venc))).isoformat()])
            else:
                raise ErrorInventario("parametro_invalido", parametro="venc")
        if desde_ms is not None:
            donde.append("creada_ms >= ?")
            args.append(int(desde_ms))
        if hasta_ms is not None:
            donde.append("creada_ms <= ?")
            args.append(int(hasta_ms))
        return (" WHERE " + " AND ".join(donde)) if donde else "", args

    @staticmethod
    def _paginacion(limite, desplazamiento) -> tuple[int, int]:
        try:
            return max(1, min(int(limite), LIMITE_MAXIMO)), max(0, int(desplazamiento))
        except (TypeError, ValueError):
            raise ErrorInventario("parametro_invalido", parametro="limite") from None

    def listar(self, *, orden: str = "auditada", direccion: str = "desc", limite: int = LIMITE_DEFECTO,
               desplazamiento: int = 0, **filtros) -> dict:
        """Cajas filtradas y ordenadas. Devuelve {"cajas", "total", "limite", "desplazamiento"}."""
        if orden not in ORDENES:
            raise ErrorInventario("parametro_invalido", parametro="orden")
        if direccion not in ("asc", "desc"):
            raise ErrorInventario("parametro_invalido", parametro="dir")
        limite, desplazamiento = self._paginacion(limite, desplazamiento)
        donde, args = self._donde(**filtros)
        columna = ORDENES[orden]
        # Lo vacío va siempre al final, sea cual sea el sentido.
        nulos = f"({ORDENES[orden].split()[0]} IS NULL), " if orden in NULOS_AL_FINAL else ""
        filas = self._aud.leer(
            f"SELECT {_COLUMNAS} FROM cajas{donde} ORDER BY {nulos}{columna} {direccion.upper()}, id DESC "
            "LIMIT ? OFFSET ?", (*args, limite, desplazamiento))
        total = self._aud.leer(f"SELECT COUNT(*) FROM cajas{donde}", tuple(args))[0][0]
        return {"cajas": [self._a_dict(f) for f in filas], "total": total, "limite": limite,
                "desplazamiento": desplazamiento}

    def exportar(self, *, orden: str = "auditada", direccion: str = "desc", **filtros) -> list:
        """TODAS las cajas que cumplen los filtros (sin paginar), para exportarlas. Tope de seguridad:
        LIMITE_EXPORTACION filas."""
        if orden not in ORDENES:
            raise ErrorInventario("parametro_invalido", parametro="orden")
        if direccion not in ("asc", "desc"):
            raise ErrorInventario("parametro_invalido", parametro="dir")
        donde, args = self._donde(**filtros)
        nulos = f"({ORDENES[orden].split()[0]} IS NULL), " if orden in NULOS_AL_FINAL else ""
        filas = self._aud.leer(
            f"SELECT {_COLUMNAS} FROM cajas{donde} ORDER BY {nulos}{ORDENES[orden]} {direccion.upper()}, id DESC "
            "LIMIT ?", (*args, LIMITE_EXPORTACION))
        return [self._a_dict(f) for f in filas]

    def agrupar(self, *, limite: int = LIMITE_DEFECTO, desplazamiento: int = 0, **filtros) -> dict:
        """Cajas agrupadas por medicamento, gramaje, lote del medicamento y vencimiento: lo más
        próximo a vencer primero (útil para FEFO). Las filas sin datos extraídos van juntas."""
        limite, desplazamiento = self._paginacion(limite, desplazamiento)
        donde, args = self._donde(**filtros)
        grupo = "medicamento, gramaje, numero_lote, vencimiento"
        filas = self._aud.leer(
            f"SELECT {grupo}, COUNT(*), MAX(creada_ms) FROM cajas{donde} GROUP BY {grupo} "
            "ORDER BY (vencimiento IS NULL), vencimiento ASC, medicamento COLLATE NOCASE, numero_lote "
            "LIMIT ? OFFSET ?", (*args, limite, desplazamiento))
        total = self._aud.leer(f"SELECT COUNT(*) FROM (SELECT 1 FROM cajas{donde} GROUP BY {grupo})",
                               tuple(args))[0][0]
        hoy = self._hoy()
        grupos = [{"medicamento": f[0], "gramaje": f[1], "numero_lote": f[2], "vencimiento": f[3],
                   "cajas": f[4], "ultima_auditoria_ms": f[5],
                   "dias_para_vencer": (date.fromisoformat(f[3]) - hoy).days if f[3] else None} for f in filas]
        return {"grupos": grupos, "total": total, "limite": limite, "desplazamiento": desplazamiento}

    def destinos(self) -> list:
        """Destinos que existen (para llenar el filtro de clasificación)."""
        return [f[0] for f in self._aud.leer(
            "SELECT DISTINCT destino FROM cajas WHERE destino IS NOT NULL ORDER BY destino")]
