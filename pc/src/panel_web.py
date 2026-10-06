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
"""
import json
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import protocol_constants as pc  # noqa: E402

from estado_panel import (  # noqa: E402
    CERRADA_SIN_DECISION,
    DESTINO_INVALIDO,
    RESUELTA_CON_DECISION,
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
_MAX_CUERPO_POST_BYTES = 1024

# Todo el contenido es propio: nada de recursos externos ni scripts inline.
_CSP = "default-src 'self'; img-src 'self'; frame-ancestors 'none'"


def _configuracion() -> list:
    """Parámetros del contrato (solo lectura), importados de shared/."""
    return [
        {"nombre": "Servicio mDNS", "valor": pc.MDNS_SERVICE_TYPE},
        {"nombre": "Puerto TCP (lotes)", "valor": str(pc.TCP_PUERTO_DEFECTO)},
        {"nombre": "Puerto TCP (consulta de decisión)", "valor": str(pc.TCP_PUERTO_DECISION_DEFECTO)},
        {"nombre": "Intervalo de consulta de decisión", "valor": f"{pc.INTERVALO_CONSULTA_DECISION_S} s"},
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
    def _responder(self, codigo, tipo, cuerpo: bytes):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo, datos):
        self._responder(codigo, "application/json; charset=utf-8",
                        json.dumps(datos).encode("utf-8"))

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
            self._json(200, datos)
        elif (m := _RE_FOTO.match(ruta)):
            foto = estado.foto(int(m.group(1)), int(m.group(2)))
            if foto is None:
                self._json(404, {"error": "foto no disponible"})
            else:
                self._responder(200, "image/jpeg", foto)
        else:
            self._json(404, {"error": "no encontrado"})

    def _leer_cuerpo_json(self):
        """Cuerpo JSON opcional del POST; devuelve {} si no hay o es inválido."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > _MAX_CUERPO_POST_BYTES:
            return {}
        try:
            datos = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return datos if isinstance(datos, dict) else {}

    def do_POST(self):
        ruta = urlsplit(self.path).path
        m = _RE_RESOLVER.match(ruta)
        if not m:
            self._json(404, {"error": "no encontrado"})
            return
        # Defensa básica contra CSRF desde otra página abierta en el navegador
        # del regente: el Origin de un navegador debe coincidir con el Host.
        origen = self.headers.get("Origin")
        if origen and urlsplit(origen).netloc != self.headers.get("Host"):
            self._json(403, {"error": "origen no permitido"})
            return
        destino = self._leer_cuerpo_json().get("destino")
        resultado = self.server.estado.resolver_alarma(int(m.group(1)), destino)
        if resultado == RESUELTA_CON_DECISION:
            log.info("Alarma %s resuelta por el regente: destino %s", m.group(1), destino)
            self._json(200, {"ok": True, "decision_aplicada": True})
        elif resultado == CERRADA_SIN_DECISION:
            log.info("Alarma %s cerrada (la Orange Pi ya no la esperaba)", m.group(1))
            self._json(200, {"ok": True, "decision_aplicada": False})
        elif resultado == DESTINO_INVALIDO:
            self._json(400, {"error": "destino_invalido"})
        else:  # NO_EXISTE
            self._json(404, {"error": "alarma inexistente"})


def crear_servidor_panel(estado: EstadoPanel, host: str = PANEL_HOST,
                         puerto: int = PANEL_PUERTO) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer((host, puerto), _Handler)
    servidor.daemon_threads = True
    servidor.estado = estado
    return servidor


def iniciar_panel(estado: EstadoPanel, host: str = PANEL_HOST,
                  puerto: int = PANEL_PUERTO) -> ThreadingHTTPServer:
    """Levanta el panel en un hilo daemon y devuelve el servidor."""
    servidor = crear_servidor_panel(estado, host, puerto)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor
