"""
Descubrimiento de la PC por mDNS/Zeroconf, con caché e invalidación bajo
demanda (ver docs/arquitectura_comunicacion.md, sección 3).

Requiere: pip install zeroconf
"""
import socket
import time

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
from protocol_constants import MDNS_SERVICE_TYPE  # noqa: E402

from zeroconf import Zeroconf, ServiceBrowser, ServiceListener


class _Listener(ServiceListener):
    def __init__(self, on_found):
        self._on_found = on_found

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            self._on_found(ip, info.port)

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        pass


class DescubridorPC:
    """Mantiene en caché la última IP/puerto conocidos de la PC.
    Solo vuelve a buscar por mDNS cuando se invalida explícitamente
    (por ejemplo, tras un fallo de conexión)."""

    def __init__(self, service_type: str = MDNS_SERVICE_TYPE):
        self.service_type = service_type
        self.ip = None
        self.puerto = None

    def obtener_destino(self):
        """Devuelve (ip, puerto) cacheados, buscando primero si no hay caché."""
        if self.ip is None:
            self.buscar_hasta_encontrar()
        return self.ip, self.puerto

    def obtener_destino_sin_bloquear(self, timeout=3):
        """Como `obtener_destino`, pero con UN solo intento de descubrimiento:
        devuelve (ip, puerto) o (None, None). Para bucles que deben seguir
        vigilando otras condiciones mientras la PC no aparece."""
        if self.ip is None:
            self._intentar_descubrimiento(timeout=timeout)
        return self.ip, self.puerto

    def invalidar(self):
        """Llamar cuando una conexión falla: fuerza re-descubrimiento la
        próxima vez que se pida el destino."""
        self.ip = None
        self.puerto = None

    def buscar_hasta_encontrar(self, backoff_inicial=1, backoff_maximo=15, timeout_por_intento=3):
        espera = backoff_inicial
        while self.ip is None:
            self._intentar_descubrimiento(timeout=timeout_por_intento)
            if self.ip is None:
                print(f"[mDNS] Servicio no encontrado, reintentando en {espera}s...")
                time.sleep(espera)
                espera = min(espera * 2, backoff_maximo)
        print(f"[mDNS] PC localizada en {self.ip}:{self.puerto}")

    def _intentar_descubrimiento(self, timeout):
        zc = Zeroconf()
        encontrado = {}

        def on_found(ip, port):
            encontrado["ip"] = ip
            encontrado["port"] = port

        listener = _Listener(on_found)
        ServiceBrowser(zc, self.service_type, listener)
        t0 = time.time()
        while not encontrado and (time.time() - t0) < timeout:
            time.sleep(0.1)
        zc.close()
        if encontrado:
            self.ip = encontrado["ip"]
            self.puerto = encontrado["port"]
