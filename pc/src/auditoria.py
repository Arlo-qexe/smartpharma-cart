"""
Registro de eventos (log) del sistema: lotes recibidos, alarmas, decisiones del regente,
órdenes del ciclo, arranques... cada uno con fecha, hora y responsable.

Es de SOLO ESCRITURA y está pensado para auditoría:
  - SQLite (biblioteca estándar), un archivo en disco: sobrevive a reinicios de la PC.
  - Triggers que rechazan UPDATE y DELETE sobre la tabla: ni el panel ni el código de la
    PC pueden editar o borrar un evento; solo se agregan nuevos.
  - Cadena de hashes: cada evento incluye el hash del anterior. Si alguien modifica o
    quita una fila saltándose los triggers (editando el archivo a mano), `verificar()` lo
    detecta y dice desde qué evento se rompe la cadena.

Límites (honestos): quien tenga acceso al archivo puede manipularlo; la cadena hace la
alteración DETECTABLE, no imposible. Tampoco detecta que se recorte el final del registro
salvo que se conserve aparte el último sello (`ultimo_hash`, que el panel muestra).

Una sola variable de entorno: PC_DB = ruta del archivo (por defecto pc/data/smartpharma.db;
`:memory:` = sin persistencia, útil para demos y pruebas).
"""
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("auditoria")

RUTA_DB_DEFECTO = Path(__file__).resolve().parents[1] / "data" / "smartpharma.db"
HASH_INICIAL = "0" * 64          # "hash anterior" del primer evento
LIMITE_DEFECTO = 200
LIMITE_MAXIMO = 500

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    tipo        TEXT    NOT NULL,
    responsable TEXT    NOT NULL,
    detalle     TEXT    NOT NULL,
    hash_prev   TEXT    NOT NULL,
    hash        TEXT    NOT NULL
);
CREATE TRIGGER IF NOT EXISTS eventos_sin_update BEFORE UPDATE ON eventos
BEGIN SELECT RAISE(ABORT, 'el registro de eventos es de solo escritura'); END;
CREATE TRIGGER IF NOT EXISTS eventos_sin_delete BEFORE DELETE ON eventos
BEGIN SELECT RAISE(ABORT, 'el registro de eventos es de solo escritura'); END;
"""


class ErrorRegistro(Exception):
    """No se pudo escribir o leer el registro de eventos."""


def ruta_db_desde_entorno() -> str:
    """Ruta del archivo según PC_DB; sin definir (o vacía), la de por defecto."""
    valor = os.environ.get("PC_DB", "").strip()
    return valor or str(RUTA_DB_DEFECTO)


def _canonico(detalle: dict) -> str:
    return json.dumps(detalle, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _calcular_hash(hash_prev: str, id_: int, ts_ms: int, tipo: str, responsable: str,
                   detalle_json: str) -> str:
    datos = "\n".join((hash_prev, str(id_), str(ts_ms), tipo, responsable, detalle_json))
    return hashlib.sha256(datos.encode("utf-8")).hexdigest()


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Auditoria:
    def __init__(self, ruta: str | Path):
        self.ruta = str(ruta)
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        try:
            # isolation_level=None: las transacciones se manejan a mano (BEGIN IMMEDIATE).
            self._con = sqlite3.connect(self.ruta, check_same_thread=False, timeout=5,
                                        isolation_level=None)
            self._con.executescript(_ESQUEMA)
        except sqlite3.Error as e:
            raise ErrorRegistro(f"no se pudo abrir el registro en {self.ruta}: {e}") from e
        self._lock = threading.Lock()   # una sola conexión compartida por todos los hilos

    def cerrar(self) -> None:
        with self._lock:
            self._con.close()

    # -- escritura ----------------------------------------------------------------
    def registrar(self, tipo: str, responsable: str, detalle: dict | None = None,
                  ts_ms: int | None = None) -> int:
        """Agrega un evento y devuelve su id. Nunca modifica uno existente."""
        if not isinstance(tipo, str) or not tipo.strip():
            raise ErrorRegistro("el evento necesita un tipo")
        if not isinstance(responsable, str) or not responsable.strip():
            raise ErrorRegistro("el evento necesita un responsable")
        if detalle is not None and not isinstance(detalle, dict):
            raise ErrorRegistro("el detalle del evento debe ser un diccionario")
        try:
            detalle_json = _canonico(detalle or {})
        except (TypeError, ValueError) as e:
            raise ErrorRegistro(f"detalle no serializable: {e}") from e
        ts_ms = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
        with self._lock:
            try:
                self._con.execute("BEGIN IMMEDIATE")
                # Se relee el último evento dentro de la transacción: así la cadena sigue
                # bien aunque otro proceso comparta el archivo.
                fila = self._con.execute(
                    "SELECT id, hash FROM eventos ORDER BY id DESC LIMIT 1").fetchone()
                id_, hash_prev = (fila[0] + 1, fila[1]) if fila else (1, HASH_INICIAL)
                h = _calcular_hash(hash_prev, id_, ts_ms, tipo, responsable, detalle_json)
                self._con.execute(
                    "INSERT INTO eventos (id, ts_ms, tipo, responsable, detalle, hash_prev, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (id_, ts_ms, tipo, responsable, detalle_json, hash_prev, h))
                self._con.execute("COMMIT")
            except sqlite3.Error as e:
                try:
                    self._con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise ErrorRegistro(f"no se pudo registrar el evento: {e}") from e
        return id_

    # -- lectura ------------------------------------------------------------------
    def listar(self, limite: int = LIMITE_DEFECTO, antes_id: int | None = None,
               despues_id: int | None = None, tipo: str | None = None,
               texto: str | None = None, desde_ms: int | None = None,
               hasta_ms: int | None = None) -> dict:
        """Eventos del más reciente al más antiguo.

        antes_id   = solo los anteriores a ese id (paginar hacia atrás);
        despues_id = solo los posteriores (para ir añadiendo los nuevos en el panel).
        """
        limite = max(1, min(int(limite), LIMITE_MAXIMO))
        donde, args = [], []
        if antes_id is not None:
            donde.append("id < ?")
            args.append(int(antes_id))
        if despues_id is not None:
            donde.append("id > ?")
            args.append(int(despues_id))
        if tipo:
            donde.append("tipo = ?")
            args.append(tipo)
        if desde_ms is not None:
            donde.append("ts_ms >= ?")
            args.append(int(desde_ms))
        if hasta_ms is not None:
            donde.append("ts_ms <= ?")
            args.append(int(hasta_ms))
        if texto:
            donde.append("(tipo || ' ' || responsable || ' ' || detalle) LIKE ? ESCAPE '\\'")
            args.append("%" + _escapar_like(texto) + "%")
        sql = "SELECT id, ts_ms, tipo, responsable, detalle, hash FROM eventos"
        if donde:
            sql += " WHERE " + " AND ".join(donde)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            try:
                filas = self._con.execute(sql, (*args, limite + 1)).fetchall()
            except sqlite3.Error as e:
                raise ErrorRegistro(f"no se pudo leer el registro: {e}") from e
        eventos = [{"id": f[0], "ts_ms": f[1], "tipo": f[2], "responsable": f[3],
                    "detalle": json.loads(f[4]), "hash": f[5]} for f in filas[:limite]]
        return {"eventos": eventos, "hay_mas": len(filas) > limite}

    def tipos(self) -> list:
        with self._lock:
            filas = self._con.execute("SELECT DISTINCT tipo FROM eventos ORDER BY tipo").fetchall()
        return [f[0] for f in filas]

    def total(self) -> int:
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]

    def verificar(self) -> dict:
        """Recorre toda la cadena. Devuelve {"ok", "total", "ultimo_id", "ultimo_hash",
        "primer_invalido", "motivo"}; si se rompe, `primer_invalido` es el id donde se nota."""
        esperado_id, hash_previo, total = 1, HASH_INICIAL, 0
        with self._lock:
            filas = self._con.execute(
                "SELECT id, ts_ms, tipo, responsable, detalle, hash_prev, hash "
                "FROM eventos ORDER BY id")
            for id_, ts_ms, tipo, responsable, detalle, hash_prev, h in filas:
                motivo = None
                if id_ != esperado_id:
                    motivo = "falta_un_evento"           # hueco en la numeración
                elif hash_prev != hash_previo:
                    motivo = "cadena_rota"               # no apunta al hash del anterior
                elif _calcular_hash(hash_prev, id_, ts_ms, tipo, responsable, detalle) != h:
                    motivo = "evento_modificado"         # su contenido no coincide con su hash
                if motivo:
                    return {"ok": False, "total": total, "ultimo_id": esperado_id - 1,
                            "ultimo_hash": hash_previo, "primer_invalido": id_, "motivo": motivo}
                esperado_id, hash_previo, total = id_ + 1, h, total + 1
        return {"ok": True, "total": total, "ultimo_id": esperado_id - 1,
                "ultimo_hash": hash_previo, "primer_invalido": None, "motivo": None}
