"""
Usuarios, turnos y permisos del panel (Parte B del plan de inventario y usuarios).

Quién puede hacer qué:
  - Ver el panel y el registro de eventos: cualquiera (no requiere sesión).
  - Operar (iniciar/detener el recorrido, resolver alarmas, confirmar acciones del asistente) y
    editar el inventario: el AUXILIAR EN TURNO o un administrador.
  - Administrar usuarios y códigos: solo un administrador.

Credenciales:
  - Auxiliar: un CÓDIGO personal de 6 dígitos que genera el administrador (se muestra una sola
    vez; no se puede recuperar, solo regenerar). El código identifica a la persona.
  - Administrador: nombre y CLAVE de al menos 10 caracteres. El primero se crea por consola
    (`bash scripts/usuarios.sh crear-admin`), nunca desde el panel.
  - Códigos y claves se guardan con PBKDF2 y sal propia; nunca en claro, ni en el registro.

Turnos: hay UN solo turno abierto a la vez. Expira por inactividad (INACTIVIDAD_TURNO_S). Si
otro auxiliar quiere entrar, el que está en turno debe cerrarlo; un administrador puede tomar
el control (cierra el turno ajeno y queda anotado). Un recorrido en marcha NO se detiene al
vencer o cerrarse un turno: sigue a nombre de quien lo inició y se detiene con otra sesión.

Defensa contra adivinar códigos: tras FALLOS_ANTES_DE_BLOQUEO intentos fallidos seguidos
(globales: con un código no se sabe a qué persona se ataca) se bloquea el inicio de sesión un
tiempo creciente.

Todo cambio (crear, desactivar, eliminar, regenerar código, abrir o cerrar turno, intentos
fallidos) queda en el registro de eventos con su responsable, y se confirma en la MISMA
transacción que el cambio: no puede haber un cambio sin su evento. Los usuarios viven en el
mismo archivo que el registro (auditoria.py).

Los nombres son únicos y NUNCA se reutilizan, ni siquiera tras eliminar al usuario: así el
`responsable` de un evento antiguo no se puede confundir con otra persona.
"""
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass

from auditoria import Auditoria, ErrorRegistro

log = logging.getLogger("usuarios")

ROL_ADMINISTRADOR = "administrador"
ROL_AUXILIAR = "auxiliar"
PERMISO_OPERAR = "operar"
PERMISO_EDITAR_INVENTARIO = "editar_inventario"
PERMISO_EXPORTAR = "exportar"
PERMISO_ADMINISTRAR = "administrar"
PERMISOS_POR_ROL = {
    ROL_ADMINISTRADOR: {PERMISO_OPERAR, PERMISO_EDITAR_INVENTARIO, PERMISO_EXPORTAR, PERMISO_ADMINISTRAR},
    ROL_AUXILIAR: {PERMISO_OPERAR, PERMISO_EDITAR_INVENTARIO, PERMISO_EXPORTAR},
}

RESPONSABLE_CONSOLA = "consola local"      # acciones hechas desde scripts/usuarios.sh
RESPONSABLE_SISTEMA = "sistema"

LARGO_CODIGO = 6
LARGO_MIN_CLAVE = 10
LARGO_MAX_CLAVE = 200
LARGO_MIN_NOMBRE, LARGO_MAX_NOMBRE = 2, 60
ITERACIONES_HASH = 100_000                 # PBKDF2-HMAC-SHA256 (las pruebas lo bajan)
INACTIVIDAD_TURNO_S = 30 * 60              # valor inicial; calibrar con los auxiliares
FALLOS_ANTES_DE_BLOQUEO = 5
BLOQUEO_BASE_S = 60.0
BLOQUEO_MAX_S = 900.0

_RE_CODIGO = re.compile(rf"^\d{{{LARGO_CODIGO}}}$")
_RE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre     TEXT    NOT NULL,
    nombre_clave TEXT  NOT NULL,
    rol        TEXT    NOT NULL CHECK (rol IN ('administrador', 'auxiliar')),
    sal        TEXT    NOT NULL,
    hash       TEXT    NOT NULL,
    activo     INTEGER NOT NULL DEFAULT 1,
    eliminado  INTEGER NOT NULL DEFAULT 0,
    creado_ms  INTEGER NOT NULL,
    creado_por TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS usuarios_nombre_unico ON usuarios (nombre_clave);
"""


class ErrorUsuarios(Exception):
    """Error de negocio con un `codigo` estable (el panel lo traduce a texto y a HTTP)."""

    def __init__(self, codigo: str, **detalle):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalle = detalle


@dataclass
class Sesion:
    usuario_id: int
    nombre: str
    rol: str
    token_hash: str
    inicio: float
    ultima_actividad: float

    def tiene(self, permiso: str) -> bool:
        return permiso in PERMISOS_POR_ROL.get(self.rol, ())


def _hash(secreto: str, sal: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", secreto.encode("utf-8"), sal, ITERACIONES_HASH).hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalizar_nombre(nombre) -> str:
    if not isinstance(nombre, str):
        raise ErrorUsuarios("nombre_invalido")
    n = " ".join(nombre.split())
    if not LARGO_MIN_NOMBRE <= len(n) <= LARGO_MAX_NOMBRE or _RE_CONTROL.search(n):
        raise ErrorUsuarios("nombre_invalido")
    return n


def _validar_clave(clave) -> str:
    if (not isinstance(clave, str) or not LARGO_MIN_CLAVE <= len(clave) <= LARGO_MAX_CLAVE
            or clave.strip() != clave):
        raise ErrorUsuarios("clave_invalida", minimo=LARGO_MIN_CLAVE)
    return clave


def _clave_nombre(nombre: str) -> str:
    """Clave para comparar nombres: sin mayúsculas ni tildes ("ANA PÉREZ" = "Ana Perez").
    Se calcula aquí y no con lower() de SQLite, que solo entiende ASCII y dejaría pasar "PÉREZ"."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", nombre) if not unicodedata.combining(c))
    return sin_tildes.casefold()


def _info(f) -> dict:
    return {"id": f[0], "nombre": f[1], "rol": f[2], "activo": bool(f[3]), "eliminado": bool(f[4]),
            "creado_ms": f[5], "creado_por": f[6]}


_COLUMNAS = "id, nombre, rol, activo, eliminado, creado_ms, creado_por"


class GestorUsuarios:
    def __init__(self, auditoria: Auditoria, reloj=time.time, inactividad_s: float = INACTIVIDAD_TURNO_S):
        self._aud = auditoria
        self._reloj = reloj
        self._inactividad_s = inactividad_s
        self._lock = threading.RLock()
        self._sesion: Sesion | None = None       # UN solo turno abierto a la vez
        self._fallos = 0
        self._bloqueado_hasta = 0.0
        with self._aud.transaccion() as tx:
            for sentencia in _ESQUEMA.strip().split(";"):
                if sentencia.strip():
                    tx.execute(sentencia)

    # ------------------------------------------------------------------ consultas
    def hay_administrador(self) -> bool:
        return self._aud.leer(
            "SELECT COUNT(*) FROM usuarios WHERE rol = 'administrador' AND activo = 1 AND eliminado = 0"
        )[0][0] > 0

    def listar(self) -> list:
        return [_info(f) for f in self._aud.leer(f"SELECT {_COLUMNAS} FROM usuarios ORDER BY id")]

    def obtener(self, usuario_id: int) -> dict | None:
        f = self._aud.leer(f"SELECT {_COLUMNAS} FROM usuarios WHERE id = ?", (usuario_id,))
        return _info(f[0]) if f else None

    # ------------------------------------------------------------------ administración
    def _generar_codigo_unico(self) -> tuple[str, bytes, str]:
        """Un código de 6 dígitos que no use ningún otro auxiliar (ni siquiera uno desactivado
        o eliminado: un código viejo no debe pasar a identificar a otra persona)."""
        existentes = self._aud.leer("SELECT sal, hash FROM usuarios WHERE rol = 'auxiliar'")
        for _ in range(200):
            codigo = f"{secrets.randbelow(10 ** LARGO_CODIGO):0{LARGO_CODIGO}d}"
            if not any(hmac.compare_digest(_hash(codigo, bytes.fromhex(sal)), h) for sal, h in existentes):
                sal = secrets.token_bytes(16)
                return codigo, sal, _hash(codigo, sal)
        raise ErrorUsuarios("sin_codigos_libres")

    def crear_usuario(self, actor: str, nombre: str, rol: str = ROL_AUXILIAR,
                      clave: str | None = None) -> tuple[dict, str | None]:
        """Crea un usuario. Para un auxiliar devuelve su código (UNA sola vez: no se guarda en
        claro); para un administrador, `clave` es obligatoria y no se devuelve nada."""
        if rol not in PERMISOS_POR_ROL:
            raise ErrorUsuarios("rol_invalido")
        nombre = _normalizar_nombre(nombre)
        with self._lock:
            if rol == ROL_AUXILIAR:
                secreto, sal, h = self._generar_codigo_unico()
                devolver = secreto
            else:
                secreto = _validar_clave(clave)
                sal = secrets.token_bytes(16)
                h = _hash(secreto, sal)
                devolver = None
            with self._aud.transaccion() as tx:
                if tx.execute("SELECT 1 FROM usuarios WHERE nombre_clave = ?", (_clave_nombre(nombre),)).fetchone():
                    raise ErrorUsuarios("nombre_duplicado")
                ts = int(self._reloj() * 1000)
                cur = tx.execute(
                    "INSERT INTO usuarios (nombre, nombre_clave, rol, sal, hash, creado_ms, creado_por) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (nombre, _clave_nombre(nombre), rol, sal.hex(), h, ts, actor))
                uid = cur.lastrowid
                tx.registrar("usuario_creado", actor, {"usuario_id": uid, "nombre": nombre, "rol": rol})
            return self.obtener(uid), devolver

    def _usuario_vivo(self, tx, usuario_id: int):
        f = tx.execute(f"SELECT {_COLUMNAS} FROM usuarios WHERE id = ? AND eliminado = 0", (usuario_id,)).fetchone()
        if f is None:
            raise ErrorUsuarios("usuario_inexistente")
        return _info(f)

    @staticmethod
    def _no_dejar_sin_administrador(tx, u: dict):
        if u["rol"] == ROL_ADMINISTRADOR and u["activo"] and tx.execute(
                "SELECT COUNT(*) FROM usuarios WHERE rol = 'administrador' AND activo = 1 "
                "AND eliminado = 0 AND id != ?", (u["id"],)).fetchone()[0] == 0:
            raise ErrorUsuarios("ultimo_administrador")

    def regenerar_codigo(self, actor: str, usuario_id: int) -> str:
        """Nuevo código para un auxiliar (el anterior deja de servir). Se devuelve UNA sola vez."""
        with self._lock:
            codigo, sal, h = self._generar_codigo_unico()
            with self._aud.transaccion() as tx:
                u = self._usuario_vivo(tx, usuario_id)
                if u["rol"] != ROL_AUXILIAR:
                    raise ErrorUsuarios("solo_auxiliares")
                tx.execute("UPDATE usuarios SET sal = ?, hash = ? WHERE id = ?", (sal.hex(), h, usuario_id))
                tx.registrar("usuario_codigo_regenerado", actor, {"usuario_id": usuario_id, "nombre": u["nombre"]})
                self._cerrar_si_es_de_locked(tx, usuario_id, actor, "codigo_regenerado")
            return codigo

    def cambiar_activo(self, actor: str, usuario_id: int, activo: bool) -> dict:
        with self._lock:
            with self._aud.transaccion() as tx:
                u = self._usuario_vivo(tx, usuario_id)
                if not activo:
                    self._no_dejar_sin_administrador(tx, u)
                if u["activo"] != activo:
                    tx.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (1 if activo else 0, usuario_id))
                    tx.registrar("usuario_activado" if activo else "usuario_desactivado", actor,
                                 {"usuario_id": usuario_id, "nombre": u["nombre"], "rol": u["rol"]})
                    if not activo:
                        self._cerrar_si_es_de_locked(tx, usuario_id, actor, "usuario_desactivado")
            return self.obtener(usuario_id)

    def eliminar(self, actor: str, usuario_id: int) -> dict:
        """Eliminación LÓGICA: ya no puede entrar, pero su nombre sigue reservado y sus
        eventos pasados conservan su identidad."""
        with self._lock:
            with self._aud.transaccion() as tx:
                u = self._usuario_vivo(tx, usuario_id)
                self._no_dejar_sin_administrador(tx, u)
                tx.execute("UPDATE usuarios SET eliminado = 1, activo = 0 WHERE id = ?", (usuario_id,))
                tx.registrar("usuario_eliminado", actor,
                             {"usuario_id": usuario_id, "nombre": u["nombre"], "rol": u["rol"]})
                self._cerrar_si_es_de_locked(tx, usuario_id, actor, "usuario_eliminado")
            return self.obtener(usuario_id)

    def cambiar_clave(self, usuario_id: int, actual: str, nueva: str) -> None:
        """Un administrador cambia su propia clave."""
        nueva = _validar_clave(nueva)
        with self._lock:
            with self._aud.transaccion() as tx:
                f = tx.execute("SELECT nombre, rol, sal, hash FROM usuarios WHERE id = ? AND eliminado = 0",
                               (usuario_id,)).fetchone()
                if f is None or f[1] != ROL_ADMINISTRADOR:
                    raise ErrorUsuarios("usuario_inexistente")
                if not isinstance(actual, str) or not hmac.compare_digest(_hash(actual, bytes.fromhex(f[2])), f[3]):
                    raise ErrorUsuarios("credenciales_invalidas")
                sal = secrets.token_bytes(16)
                tx.execute("UPDATE usuarios SET sal = ?, hash = ? WHERE id = ?", (sal.hex(), _hash(nueva, sal), usuario_id))
                tx.registrar("usuario_clave_cambiada", f[0], {"usuario_id": usuario_id, "nombre": f[0]})

    def restablecer_clave(self, nombre: str, nueva: str, actor: str = RESPONSABLE_CONSOLA) -> dict:
        """Solo desde la consola de la PC (quien tiene acceso al equipo): para un administrador
        que olvidó su clave. Cierra su sesión si la tenía."""
        nueva = _validar_clave(nueva)
        with self._lock:
            with self._aud.transaccion() as tx:
                f = tx.execute(f"SELECT {_COLUMNAS} FROM usuarios WHERE nombre_clave = ? AND eliminado = 0",
                               (_clave_nombre(" ".join(str(nombre).split())),)).fetchone()
                if f is None or f[2] != ROL_ADMINISTRADOR:
                    raise ErrorUsuarios("usuario_inexistente")
                sal = secrets.token_bytes(16)
                tx.execute("UPDATE usuarios SET sal = ?, hash = ?, activo = 1 WHERE id = ?",
                           (sal.hex(), _hash(nueva, sal), f[0]))
                tx.registrar("usuario_clave_restablecida", actor, {"usuario_id": f[0], "nombre": f[1]})
                self._cerrar_si_es_de_locked(tx, f[0], actor, "clave_restablecida")
                self._fallos, self._bloqueado_hasta = 0, 0.0
            return self.obtener(f[0])

    # ------------------------------------------------------------------ turnos y sesiones
    def _cerrar_locked(self, tx, quien: str, motivo: str) -> None:
        """Cierra el turno abierto (si hay) y lo anota. `quien` es el responsable del cierre."""
        s = self._sesion
        if s is None:
            return
        self._sesion = None
        tx.registrar("turno_cerrado", quien,
                     {"usuario_id": s.usuario_id, "nombre": s.nombre, "rol": s.rol, "motivo": motivo,
                      "duracion_s": int(self._reloj() - s.inicio)})

    def _cerrar_si_es_de_locked(self, tx, usuario_id: int, quien: str, motivo: str) -> None:
        if self._sesion is not None and self._sesion.usuario_id == usuario_id:
            self._cerrar_locked(tx, quien, motivo)

    def _vigente_locked(self) -> Sesion | None:
        """El turno abierto, si no venció por inactividad (si venció, lo cierra y lo anota)."""
        s = self._sesion
        if s is not None and self._reloj() - s.ultima_actividad > self._inactividad_s:
            with self._aud.transaccion() as tx:
                self._cerrar_locked(tx, RESPONSABLE_SISTEMA, "expirado")
            return None
        return s

    def _comprobar_bloqueo_locked(self) -> None:
        espera = self._bloqueado_hasta - self._reloj()
        if espera > 0:
            raise ErrorUsuarios("bloqueado", espera_s=int(espera) + 1)

    def _fallo_locked(self, via: str, nombre_intentado: str | None) -> None:
        self._fallos += 1
        detalle = {"via": via, "fallos_seguidos": self._fallos}
        if nombre_intentado:
            detalle["nombre_intentado"] = nombre_intentado[:LARGO_MAX_NOMBRE]
        with self._aud.transaccion() as tx:
            tx.registrar("login_fallido", RESPONSABLE_SISTEMA, detalle)
            if self._fallos >= FALLOS_ANTES_DE_BLOQUEO:
                espera = min(BLOQUEO_BASE_S * 2 ** (self._fallos - FALLOS_ANTES_DE_BLOQUEO), BLOQUEO_MAX_S)
                self._bloqueado_hasta = self._reloj() + espera
                tx.registrar("login_bloqueado", RESPONSABLE_SISTEMA,
                             {"via": via, "fallos_seguidos": self._fallos, "espera_s": int(espera)})
        raise ErrorUsuarios("credenciales_invalidas")

    def _abrir_locked(self, usuario: dict) -> tuple[str, Sesion]:
        """Abre el turno de `usuario` (ya autenticado) respetando que hay UNO solo a la vez."""
        actual = self._vigente_locked()
        if actual is not None and actual.usuario_id != usuario["id"]:
            if usuario["rol"] != ROL_ADMINISTRADOR:
                raise ErrorUsuarios("turno_en_curso", nombre=actual.nombre)
        token = secrets.token_urlsafe(32)
        ahora = self._reloj()
        with self._aud.transaccion() as tx:
            if actual is not None and actual.usuario_id != usuario["id"]:   # un administrador toma el control
                self._cerrar_locked(tx, usuario["nombre"], "tomado_por_administrador")
            elif actual is not None:                                       # la misma persona vuelve a entrar
                self._cerrar_locked(tx, usuario["nombre"], "reemplazado_por_nuevo_inicio")
            self._sesion = Sesion(usuario["id"], usuario["nombre"], usuario["rol"], _hash_token(token), ahora, ahora)
            tx.registrar("turno_iniciado", usuario["nombre"], {"usuario_id": usuario["id"], "rol": usuario["rol"]})
        self._fallos = 0
        return token, self._sesion

    def iniciar_turno_con_codigo(self, codigo) -> tuple[str, Sesion]:
        """Un auxiliar abre su turno con su código. Devuelve (token, sesión)."""
        with self._lock:
            self._comprobar_bloqueo_locked()
            encontrado = None
            if isinstance(codigo, str) and _RE_CODIGO.match(codigo):
                for f in self._aud.leer(f"SELECT {_COLUMNAS}, sal, hash FROM usuarios "
                                        "WHERE rol = 'auxiliar' AND activo = 1 AND eliminado = 0"):
                    if hmac.compare_digest(_hash(codigo, bytes.fromhex(f[7])), f[8]):
                        encontrado = _info(f[:7])        # se recorren todos: sin salida anticipada
            if encontrado is None:
                self._fallo_locked("codigo", None)
            return self._abrir_locked(encontrado)

    def iniciar_sesion_administrador(self, nombre, clave) -> tuple[str, Sesion]:
        with self._lock:
            self._comprobar_bloqueo_locked()
            encontrado = None
            f = None
            if isinstance(nombre, str) and isinstance(clave, str):
                filas = self._aud.leer(f"SELECT {_COLUMNAS}, sal, hash FROM usuarios WHERE nombre_clave = ? "
                                       "AND rol = 'administrador' AND activo = 1 AND eliminado = 0",
                                       (_clave_nombre(" ".join(nombre.split())),))
                f = filas[0] if filas else None
            # Se calcula el hash aunque no exista el usuario: no se distingue "no existe" de "clave mala".
            sal = bytes.fromhex(f[7]) if f else b"\x00" * 16
            calculado = _hash(clave if isinstance(clave, str) else "", sal)
            if f is not None and hmac.compare_digest(calculado, f[8]):
                encontrado = _info(f[:7])
            if encontrado is None:
                self._fallo_locked("administrador", nombre if isinstance(nombre, str) else None)
            return self._abrir_locked(encontrado)

    def sesion_de(self, token: str | None) -> Sesion | None:
        """La sesión que corresponde a ese token, si sigue vigente (no la renueva)."""
        if not token:
            return None
        with self._lock:
            s = self._vigente_locked()
            if s is not None and hmac.compare_digest(s.token_hash, _hash_token(token)):
                return s
            return None

    def autorizar(self, token: str | None, permiso: str) -> Sesion:
        """Comprueba que el token tenga turno vigente y el permiso; cuenta como actividad."""
        with self._lock:
            s = self.sesion_de(token)
            if s is None:
                raise ErrorUsuarios("sesion_requerida")
            if not s.tiene(permiso):
                raise ErrorUsuarios("sin_permiso", permiso=permiso)
            s.ultima_actividad = self._reloj()
            return s

    def actividad(self, token: str | None) -> bool:
        with self._lock:
            s = self.sesion_de(token)
            if s is None:
                return False
            s.ultima_actividad = self._reloj()
            return True

    def cerrar_turno(self, token: str | None) -> bool:
        """Cierra el turno de quien lo tiene (con su propio token)."""
        with self._lock:
            s = self.sesion_de(token)
            if s is None:
                return False
            with self._aud.transaccion() as tx:
                self._cerrar_locked(tx, s.nombre, "voluntario")
            return True

    def estado_publico(self, token: str | None) -> dict:
        """Lo que el panel necesita saber: mi sesión, quién está en turno y si falta un administrador."""
        with self._lock:
            propia = self.sesion_de(token)
            turno = self._vigente_locked()
            bloqueo = max(0, int(self._bloqueado_hasta - self._reloj()) + 1) if self._bloqueado_hasta > self._reloj() else 0
            return {
                "sesion": ({"usuario_id": propia.usuario_id, "nombre": propia.nombre, "rol": propia.rol,
                            "permisos": sorted(PERMISOS_POR_ROL[propia.rol])} if propia else None),
                "turno": ({"nombre": turno.nombre, "rol": turno.rol, "inicio": turno.inicio} if turno else None),
                "admin_configurado": self.hay_administrador(),
                "inactividad_s": self._inactividad_s,
                "bloqueado_s": bloqueo,
            }
