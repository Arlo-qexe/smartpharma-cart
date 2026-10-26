"""
Panel de control web de la PC (solo biblioteca estándar). Lo consume el
regente de farmacia desde el navegador (ver docs/arquitectura_comunicacion.md,
sección 8).

Por defecto escucha SOLO en 127.0.0.1: el prototipo no tiene autenticación
(sección 9 del informe). Para abrirlo a la LAN: PANEL_HOST=0.0.0.0.

Rutas:
    GET  /                                  página (panel_static/index.html)
    GET  /static/<panel.css|panel.js>       recursos de la página
    GET  /api/estado                        JSON: lotes, alarmas, configuración
    GET  /foto/<lote_id>/<indice>           JPEG de una cara (solo desde RAM)
    POST /api/alarmas/<id>/resolver         cuerpo {"destino": "TIPO_X|DESCARTE"}: el regente
                                            resuelve la alarma y la Orange Pi recibe el destino
                                            en su próxima consulta (servidor_decision.py)
    POST /api/ciclo/iniciar                 el regente ordena iniciar el ciclo de auditoría: la Orange
                                            Pi la recoge en su próxima consulta (informe 4.7). Cuerpo
                                            opcional {"cantidad": N}: cajas a procesar (sin él, sin límite)
    POST /api/ciclo/detener                 el regente ordena detener el ciclo: la Orange Pi lo recoge
                                            antes del siguiente objeto (informe 4.8)
    GET  /api/log                           registro de eventos (solo lectura), del más reciente al más
                                            antiguo. Parámetros: limite, antes, despues (ids), tipo, q
                                            (texto), desde, hasta (epoch ms). Ver auditoria.py
    GET  /api/log/verificar                 comprueba la cadena de hashes del registro
    POST /api/sesion/iniciar                abre el turno: {"codigo"} (auxiliar) o {"nombre","clave"}
                                            (administrador); responde con la cookie de sesión (usuarios.py)
    POST /api/sesion/cerrar | actividad     cierra el turno / lo mantiene vivo
    POST /api/sesion/clave                  un administrador cambia su clave: {"actual","nueva"}
    GET  /api/usuarios, POST /api/usuarios[/<id>/codigo|activar|desactivar|eliminar]
                                            administración de usuarios (solo administrador)
    GET  /api/asistente/estado              {"disponible", "modo": "modelo|prueba|desactivado"}
    POST /api/asistente/mensaje             {"texto"} -> respuesta o propuesta (ver asistente_api.py)
    POST /api/asistente/confirmar|cancelar  {"id"} de una propuesta pendiente
    POST /api/asistente/reiniciar           nueva conversación
El asistente es opcional: sin él (asistente=None) estas rutas responden 503 / "desactivado".

Seguridad: con un gestor de usuarios (`usuarios=`), iniciar/detener el recorrido, resolver
alarmas y confirmar acciones del asistente exigen un turno abierto (cookie de sesión HttpOnly,
SameSite=Strict); sin sesión responden 401, sin permiso 403. Mirar el panel y el registro es
libre. Sin gestor (solo pruebas) no se exige sesión: `server.py` SIEMPRE lo conecta.
"""
import json
import logging
import os
import re
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import protocol_constants as pc  # noqa: E402

from asistente_api import AsistenteOcupado, PropuestaInexistente, TextoInvalido  # noqa: E402
from auditoria import LIMITE_DEFECTO, ErrorRegistro  # noqa: E402
from usuarios import PERMISO_ADMINISTRAR, PERMISO_OPERAR, ErrorUsuarios  # noqa: E402
from estado_panel import (  # noqa: E402
    CERRADA_SIN_DECISION,
    DESTINO_INVALIDO,
    DETENCION_ORDENADA,
    INICIO_CANTIDAD_INVALIDA,
    INICIO_ORDENADO,
    INICIO_YA_INICIADO,
    ORDEN_CANCELADA,
    RESPONSABLE_SIN_SESION,
    RESUELTA_CON_DECISION,
    SIN_CICLO,
    EstadoPanel,
)

log = logging.getLogger("panel")

PANEL_HOST = os.environ.get("PANEL_HOST", "127.0.0.1")
PANEL_PUERTO = int(os.environ.get("PANEL_PUERTO", "8080"))

_DIR_ESTATICOS = Path(__file__).resolve().parent / "panel_static"
_ESTATICOS = {
    "panel.css": "text/css; charset=utf-8",
    "panel.js": "text/javascript; charset=utf-8",
}
_RE_FOTO = re.compile(r"^/foto/(\d+)/(\d+)$")
_RE_RESOLVER = re.compile(r"^/api/alarmas/(\d+)/resolver$")
_RE_CICLO = re.compile(r"^/api/ciclo/(iniciar|detener)$")
_RE_SESION = re.compile(r"^/api/sesion/(iniciar|cerrar|actividad|clave)$")
_RE_USUARIOS = re.compile(r"^/api/usuarios(?:/(\d+)/(codigo|activar|desactivar|eliminar))?$")
_RE_ASISTENTE = re.compile(r"^/api/asistente/(estado|mensaje|confirmar|cancelar|reiniciar)$")
_MAX_CUERPO_POST_BYTES = 1024
_COOKIE_SESION = "sesion"
_VENTANA_DENEGADAS_S = 10.0     # un intento denegado igual se anota como máximo una vez en este tiempo
_HTTP_ERRORES_USUARIOS = {
    "credenciales_invalidas": 401, "sesion_requerida": 401, "sin_permiso": 403, "bloqueado": 429,
    "turno_en_curso": 409, "nombre_duplicado": 409, "ultimo_administrador": 409, "usuario_inexistente": 404,
}


def _http_de(e: ErrorUsuarios):
    """Código HTTP y cuerpo con que se responde un error del gestor de usuarios."""
    return _HTTP_ERRORES_USUARIOS.get(e.codigo, 400), {"error": e.codigo, **e.detalle}
_MAX_CUERPO_ASISTENTE_BYTES = 8192   # 1000 caracteres pueden ser hasta ~4000 bytes en UTF-8
_AYUDA_ASISTENTE_DESACTIVADO = (
    "Arranca el servidor con ASISTENTE_MODO=prueba (sin modelo, reglas fijas) o con "
    "ASISTENTE_MODELO=/ruta/al/modelo.gguf para activarlo."
)

# Todo el contenido es propio: nada de recursos externos ni scripts inline.
_CSP = "default-src 'self'; img-src 'self'; frame-ancestors 'none'"


def _configuracion() -> list:
    """Parámetros del contrato (solo lectura), importados de shared/."""
    return [
        {"nombre": "Servicio mDNS", "valor": pc.MDNS_SERVICE_TYPE},
        {"nombre": "Puerto TCP (lotes)", "valor": str(pc.TCP_PUERTO_DEFECTO)},
        {"nombre": "Puerto TCP (consulta de decisión)", "valor": str(pc.TCP_PUERTO_DECISION_DEFECTO)},
        {"nombre": "Intervalo de consulta de decisión", "valor": f"{pc.INTERVALO_CONSULTA_DECISION_S} s"},
        {"nombre": "Vigencia de la orden de inicio del ciclo", "valor": f"{pc.VIGENCIA_ORDEN_CICLO_S} s"},
        {"nombre": "Timeout de conexión TCP", "valor": f"{pc.TIMEOUT_CONEXION_TCP_S} s"},
        {"nombre": "Timeout de respuesta del reconocimiento", "valor": f"{pc.TIMEOUT_RESPUESTA_RECONOCIMIENTO_S} s"},
        {"nombre": "Límite total de transacción", "valor": f"{pc.TIMEOUT_TOTAL_TRANSACCION_S} s"},
        {"nombre": "Timeout de inactividad en recepción", "valor": f"{pc.TIMEOUT_INACTIVIDAD_RECEPCION_S} s"},
        {"nombre": "Tamaño máximo por imagen", "valor": f"{pc.TAMANO_MAX_IMAGEN_BYTES // (1024 * 1024)} MiB"},
        {"nombre": "Imágenes por lote", "valor": str(pc.MAX_IMAGENES_POR_LOTE)},
        {"nombre": "Umbral de votación", "valor": f"{pc.UMBRAL_VOTACION_MAYORIA:.0%}"},
    ]


class _Handler(BaseHTTPRequestHandler):
    server_version = "SmartPharmaPanel"

    def log_message(self, fmt, *args):
        pass  # el panel sondea /api/estado cada pocos segundos; sin ruido

    # -- utilidades ---------------------------------------------------------
    def _responder(self, codigo, tipo, cuerpo: bytes, cabeceras=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        for nombre, valor in (cabeceras or {}).items():
            self.send_header(nombre, valor)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo, datos, cabeceras=None):
        self._responder(codigo, "application/json; charset=utf-8",
                        json.dumps(datos).encode("utf-8"), cabeceras)

    def _archivo_estatico(self, nombre):
        tipo = "text/html; charset=utf-8" if nombre == "index.html" else _ESTATICOS[nombre]
        try:
            self._responder(200, tipo, (_DIR_ESTATICOS / nombre).read_bytes())
        except OSError:
            self._json(404, {"error": "no encontrado"})

    # -- rutas --------------------------------------------------------------
    def do_GET(self):
        ruta = urlsplit(self.path).path
        estado = self.server.estado

        if ruta == "/":
            self._archivo_estatico("index.html")
        elif ruta.startswith("/static/") and ruta[len("/static/"):] in _ESTATICOS:
            self._archivo_estatico(ruta[len("/static/"):])
        elif ruta == "/api/estado":
            datos = estado.snapshot()
            datos["config"] = _configuracion()
            usuarios = self.server.usuarios
            datos["acceso"] = usuarios.estado_publico(self._token()) if usuarios else None
            self._json(200, datos)
        elif ruta == "/api/usuarios":
            sesion = self._autorizar(PERMISO_ADMINISTRAR, "listar_usuarios")
            if sesion[0]:
                self._json(200, {"usuarios": self.server.usuarios.listar()})
        elif ruta == "/api/log":
            self._get_log(urlsplit(self.path).query)
        elif ruta == "/api/log/verificar":
            auditoria = self.server.auditoria
            if auditoria is None:
                self._json(503, {"error": "registro_no_disponible"})
                return
            try:
                self._json(200, auditoria.verificar())
            except ErrorRegistro:
                log.exception("No se pudo verificar el registro de eventos")
                self._json(500, {"error": "error_interno"})
        elif ruta == "/api/asistente/estado":
            asistente = self.server.asistente
            if asistente is None:
                self._json(200, {"disponible": False, "modo": "desactivado",
                                 "ayuda": _AYUDA_ASISTENTE_DESACTIVADO})
            else:
                self._json(200, {"disponible": True, "modo": asistente.modo})
        elif (m := _RE_FOTO.match(ruta)):
            foto = estado.foto(int(m.group(1)), int(m.group(2)))
            if foto is None:
                self._json(404, {"error": "foto no disponible"})
            else:
                self._responder(200, "image/jpeg", foto)
        else:
            self._json(404, {"error": "no encontrado"})

    def _get_log(self, query: str):
        auditoria = self.server.auditoria
        if auditoria is None:
            self._json(503, {"error": "registro_no_disponible"})
            return
        p = {k: v[0] for k, v in parse_qs(query).items()}
        try:
            filtros = dict(
                limite=int(p.get("limite", LIMITE_DEFECTO)),
                antes_id=int(p["antes"]) if "antes" in p else None,
                despues_id=int(p["despues"]) if "despues" in p else None,
                tipo=p.get("tipo") or None,
                texto=(p.get("q") or "").strip()[:100] or None,
                desde_ms=int(p["desde"]) if "desde" in p else None,
                hasta_ms=int(p["hasta"]) if "hasta" in p else None,
            )
        except ValueError:
            self._json(400, {"error": "parametro_invalido"})
            return
        try:
            datos = auditoria.listar(**filtros)
            datos["total"] = auditoria.total()
            datos["tipos"] = auditoria.tipos()
        except ErrorRegistro:
            log.exception("No se pudo leer el registro de eventos")
            self._json(500, {"error": "error_interno"})
            return
        self._json(200, datos)

    def _token(self):
        crudo = self.headers.get("Cookie")
        if not crudo:
            return None
        try:
            cookies = SimpleCookie()
            cookies.load(crudo)
        except Exception:
            return None
        c = cookies.get(_COOKIE_SESION)
        return c.value if c else None

    def _autorizar(self, permiso: str, accion: str):
        """(ok, responsable). Sin gestor de usuarios (solo pruebas) no se exige sesión. Si no
        hay turno o falta el permiso, ya respondió el error: quien llama solo debe retirarse."""
        usuarios = self.server.usuarios
        if usuarios is None:
            return True, RESPONSABLE_SIN_SESION
        try:
            return True, usuarios.autorizar(self._token(), permiso).nombre
        except ErrorUsuarios as e:
            self._denegar(e, accion)
            return False, None

    def _denegar(self, e: ErrorUsuarios, accion: str):
        status, cuerpo = _http_de(e)
        ahora = time.monotonic()
        with self.server.lock_denegadas:
            registrar = ahora - self.server.denegadas.get((accion, e.codigo), -1e9) > _VENTANA_DENEGADAS_S
            if registrar:
                self.server.denegadas[(accion, e.codigo)] = ahora
        if registrar:                       # sin inundar el registro si alguien insiste
            sesion = self.server.usuarios.sesion_de(self._token())
            self.server.estado.registrar_evento(
                "accion_denegada", sesion.nombre if sesion else RESPONSABLE_SIN_SESION,
                accion=accion, motivo=e.codigo)
        self._json(status, cuerpo)

    def _post_sesion(self, accion: str):
        usuarios = self.server.usuarios
        if usuarios is None:
            self._json(503, {"error": "usuarios_no_disponibles"})
            return
        cuerpo = self._leer_cuerpo_json()
        try:
            if accion == "iniciar":
                if "codigo" in cuerpo:
                    token, _ = usuarios.iniciar_turno_con_codigo(cuerpo.get("codigo"))
                else:
                    token, _ = usuarios.iniciar_sesion_administrador(cuerpo.get("nombre"), cuerpo.get("clave"))
                cookie = f"{_COOKIE_SESION}={token}; HttpOnly; SameSite=Strict; Path=/"
                self._json(200, {"ok": True, "acceso": usuarios.estado_publico(token)}, {"Set-Cookie": cookie})
            elif accion == "cerrar":
                usuarios.cerrar_turno(self._token())
                cookie = f"{_COOKIE_SESION}=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"
                self._json(200, {"ok": True}, {"Set-Cookie": cookie})
            elif accion == "actividad":
                if usuarios.actividad(self._token()):
                    self._json(200, {"ok": True})
                else:
                    self._json(401, {"error": "sesion_requerida"})
            else:  # clave
                s = usuarios.autorizar(self._token(), PERMISO_ADMINISTRAR)
                usuarios.cambiar_clave(s.usuario_id, cuerpo.get("actual"), cuerpo.get("nueva"))
                self._json(200, {"ok": True})
        except ErrorUsuarios as e:
            self._json(*_http_de(e))
        except ErrorRegistro:
            log.exception("No se pudo registrar el evento de sesión")
            self._json(500, {"error": "error_interno"})

    def _post_usuarios(self, uid, accion):
        ok, actor = self._autorizar(PERMISO_ADMINISTRAR, "administrar_usuarios")
        if not ok:
            return
        usuarios = self.server.usuarios
        cuerpo = self._leer_cuerpo_json()
        try:
            if uid is None:
                info, secreto = usuarios.crear_usuario(
                    actor, cuerpo.get("nombre"), cuerpo.get("rol", "auxiliar"), cuerpo.get("clave"))
                log.info("Usuario creado por %s: %s (%s)", actor, info["nombre"], info["rol"])
                self._json(200, {"ok": True, "usuario": info, "codigo": secreto})
            elif accion == "codigo":
                self._json(200, {"ok": True, "codigo": usuarios.regenerar_codigo(actor, int(uid))})
            elif accion == "eliminar":
                self._json(200, {"ok": True, "usuario": usuarios.eliminar(actor, int(uid))})
            else:  # activar | desactivar
                self._json(200, {"ok": True, "usuario": usuarios.cambiar_activo(actor, int(uid), accion == "activar")})
        except ErrorUsuarios as e:
            self._json(*_http_de(e))
        except ErrorRegistro:
            log.exception("No se pudo completar la administración de usuarios")
            self._json(500, {"error": "error_interno"})

    def _leer_cuerpo_json(self, max_bytes=_MAX_CUERPO_POST_BYTES):
        """Cuerpo JSON opcional del POST; devuelve {} si no hay o es inválido."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > max_bytes:
            return {}
        try:
            datos = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return datos if isinstance(datos, dict) else {}

    def _origen_valido(self) -> bool:
        # Defensa básica contra CSRF desde otra página abierta en el navegador
        # del regente: el Origin de un navegador debe coincidir con el Host.
        origen = self.headers.get("Origin")
        return not origen or urlsplit(origen).netloc == self.headers.get("Host")

    def _post_asistente(self, accion):
        asistente = self.server.asistente
        if asistente is None:
            self._json(503, {"error": "asistente_no_disponible"})
            return
        if accion == "confirmar" and not self._autorizar(PERMISO_OPERAR, "asistente_confirmar")[0]:
            return                          # ejecutar lo que propone el asistente es operar: exige turno
        cuerpo = self._leer_cuerpo_json(_MAX_CUERPO_ASISTENTE_BYTES)
        try:
            if accion == "mensaje":
                resultado = asistente.mensaje(cuerpo.get("texto"))
            elif accion == "reiniciar":
                asistente.reiniciar()
                resultado = {"ok": True}
            else:  # confirmar | cancelar
                pid = cuerpo.get("id")
                if not isinstance(pid, int) or isinstance(pid, bool):
                    self._json(400, {"error": "id_invalido"})
                    return
                resultado = (asistente.confirmar if accion == "confirmar" else asistente.cancelar)(pid)
        except AsistenteOcupado:
            self._json(409, {"error": "asistente_ocupado"})
        except PropuestaInexistente:
            self._json(404, {"error": "propuesta_inexistente"})
        except TextoInvalido:
            self._json(400, {"error": "texto_invalido"})
        except Exception:
            log.exception("Error en el asistente (%s)", accion)
            self._json(500, {"error": "error_interno"})
        else:
            self._json(200, resultado)

    def do_POST(self):
        ruta = urlsplit(self.path).path
        m_asistente = _RE_ASISTENTE.match(ruta)
        m = _RE_RESOLVER.match(ruta)
        m_ciclo = _RE_CICLO.match(ruta)
        m_sesion = _RE_SESION.match(ruta)
        m_usuarios = _RE_USUARIOS.match(ruta)
        if (not m and not m_ciclo and not m_sesion and not m_usuarios
                and not (m_asistente and m_asistente.group(1) != "estado")):
            self._json(404, {"error": "no encontrado"})
            return
        if not self._origen_valido():
            self._json(403, {"error": "origen no permitido"})
            return
        if m_sesion:
            self._post_sesion(m_sesion.group(1))
            return
        if m_usuarios:
            self._post_usuarios(m_usuarios.group(1), m_usuarios.group(2))
            return
        if m_ciclo and m_ciclo.group(1) == "detener":
            ok, responsable = self._autorizar(PERMISO_OPERAR, "detener_ciclo")
            if not ok:
                return
            resultado = self.server.estado.ordenar_detencion(responsable=responsable)
            if resultado == SIN_CICLO:
                self._json(409, {"error": "sin_ciclo"})
            else:
                if resultado == DETENCION_ORDENADA:
                    log.info("%s ordenó detener el ciclo (la Orange Pi lo recoge antes del siguiente objeto)", responsable)
                elif resultado == ORDEN_CANCELADA:
                    log.info("%s canceló la orden de inicio antes de que la Orange Pi la recogiera", responsable)
                self._json(200, {"ok": True, "resultado": resultado})
            return
        if m_ciclo:
            ok, responsable = self._autorizar(PERMISO_OPERAR, "iniciar_ciclo")
            if not ok:
                return
            cantidad = self._leer_cuerpo_json().get("cantidad")
            resultado = self.server.estado.ordenar_inicio(cantidad, responsable=responsable)
            if resultado == INICIO_YA_INICIADO:
                self._json(409, {"error": "ya_iniciada"})
            elif resultado == INICIO_CANTIDAD_INVALIDA:
                self._json(400, {"error": "cantidad_invalida"})
            else:
                if resultado == INICIO_ORDENADO:
                    log.info("%s ordenó iniciar el ciclo de auditoría (%s)", responsable,
                             f"{cantidad} cajas" if cantidad else "sin límite de cajas")
                self._json(200, {"ok": True, "resultado": resultado})
            return
        if m_asistente:
            self._post_asistente(m_asistente.group(1))
            return
        ok, responsable = self._autorizar(PERMISO_OPERAR, "resolver_alarma")
        if not ok:
            return
        destino = self._leer_cuerpo_json().get("destino")
        resultado = self.server.estado.resolver_alarma(int(m.group(1)), destino, responsable=responsable)
        if resultado == RESUELTA_CON_DECISION:
            log.info("Alarma %s resuelta por %s: destino %s", m.group(1), responsable, destino)
            self._json(200, {"ok": True, "decision_aplicada": True})
        elif resultado == CERRADA_SIN_DECISION:
            log.info("Alarma %s cerrada por %s (la Orange Pi ya no la esperaba)", m.group(1), responsable)
            self._json(200, {"ok": True, "decision_aplicada": False})
        elif resultado == DESTINO_INVALIDO:
            self._json(400, {"error": "destino_invalido"})
        else:  # NO_EXISTE
            self._json(404, {"error": "alarma inexistente"})


def crear_servidor_panel(estado: EstadoPanel, host: str = PANEL_HOST,
                         puerto: int = PANEL_PUERTO, asistente=None,
                         auditoria=None, usuarios=None) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer((host, puerto), _Handler)
    servidor.daemon_threads = True
    servidor.estado = estado
    servidor.asistente = asistente      # opcional: ver asistente_api.py
    servidor.auditoria = auditoria      # opcional: registro de eventos (auditoria.py)
    servidor.usuarios = usuarios        # gestor de usuarios y turnos (usuarios.py); None = sin sesiones
    servidor.denegadas = {}             # (acción, motivo) -> último instante anotado (anti inundación)
    servidor.lock_denegadas = threading.Lock()
    return servidor


def iniciar_panel(estado: EstadoPanel, host: str = PANEL_HOST,
                  puerto: int = PANEL_PUERTO, asistente=None,
                  auditoria=None, usuarios=None) -> ThreadingHTTPServer:
    """Levanta el panel en un hilo daemon y devuelve el servidor."""
    servidor = crear_servidor_panel(estado, host, puerto, asistente, auditoria, usuarios)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor
