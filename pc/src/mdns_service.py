"""
Registro del servicio `_ocr-service._tcp.local.` para que la Orange Pi
descubra esta PC automáticamente (ver docs/arquitectura_comunicacion.md,
sección 3.1).

Requiere: pip install zeroconf
"""
import ipaddress
import logging
import os
import socket
import sys
from pathlib import Path

import ifaddr  # ya es dependencia de zeroconf

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import MDNS_SERVICE_TYPE  # noqa: E402

from zeroconf import ServiceInfo, Zeroconf

log = logging.getLogger("mdns")

# Interfaces virtuales (contenedores, puentes) que nunca sirven para anunciar la PC.
_PREFIJOS_VIRTUALES = ("docker", "br-", "veth", "virbr")
# Interfaces de Wi-Fi y Ethernet de Linux: se prefieren sobre cualquier otra.
_PREFIJOS_FISICAS = ("wl", "en", "eth")


def _es_utilizable(ip: str) -> bool:
    """Una IPv4 que otro equipo de la red puede alcanzar: no loopback (127.x, que es lo
    que devuelve el hostname en Ubuntu), ni enlace-local (169.254.x), ni 0.0.0.0."""
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast)


def _ip_por_ruta() -> str | None:
    # connect() sobre UDP no envía tráfico: solo elige la interfaz de salida según la
    # ruta por defecto. Falla (None) si no hay ruta por defecto.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ip if _es_utilizable(ip) else None


def _interfaces_ipv4() -> list[tuple[str, str]]:
    """(nombre de interfaz, IPv4) de todas las interfaces del equipo."""
    return [
        (adaptador.name, direccion.ip)
        for adaptador in ifaddr.get_adapters()
        for direccion in adaptador.ips
        if direccion.is_IPv4
    ]


def _ip_por_interfaces() -> tuple[str, str] | None:
    candidatas = [
        (nombre, ip) for nombre, ip in _interfaces_ipv4()
        if _es_utilizable(ip) and not nombre.startswith(_PREFIJOS_VIRTUALES)
    ]
    candidatas.sort(key=lambda c: not c[0].startswith(_PREFIJOS_FISICAS))  # estable
    return candidatas[0] if candidatas else None


def elegir_ip_anunciada() -> tuple[str, str]:
    """Devuelve (ip, origen) de la IP que la PC anuncia por mDNS. En orden:
    1) la variable PC_IP, si se define (útil con varias interfaces o redes);
    2) la IP de la interfaz de la ruta por defecto;
    3) la primera IP utilizable de las interfaces reales (sin ruta por defecto);
    4) 127.0.0.1, que la Orange Pi no puede alcanzar (se avisa en el log)."""
    forzada = os.environ.get("PC_IP", "").strip()
    if forzada:
        try:
            ipaddress.IPv4Address(forzada)
        except ValueError:
            log.warning("PC_IP=%r no es una IPv4 válida; se ignora", forzada)
        else:
            return forzada, "variable PC_IP"
    ip = _ip_por_ruta()
    if ip:
        return ip, "ruta por defecto"
    candidata = _ip_por_interfaces()
    if candidata:
        return candidata[1], f"interfaz {candidata[0]} (no hay ruta por defecto)"
    return "127.0.0.1", "ninguna interfaz utilizable (loopback)"


def obtener_ip_local() -> str:
    return elegir_ip_anunciada()[0]


def registrar_servicio_mdns(puerto: int, nombre_servicio: str = "OCRServer"):
    zc = Zeroconf()
    ip_local, origen_ip = elegir_ip_anunciada()
    if ipaddress.IPv4Address(ip_local).is_loopback:
        log.warning(
            "Se anunciará %s: la Orange Pi NO podrá conectarse. Conecta la PC a la red "
            "de la demo antes de arrancar, o fuerza la IP con PC_IP=<ip>.", ip_local)
    info = ServiceInfo(
        MDNS_SERVICE_TYPE,
        f"{nombre_servicio}.{MDNS_SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip_local)],
        port=puerto,
        properties={},
    )
    # allow_name_change: si el nombre ya existe en la red (reinicio rápido u
    # otra PC), zeroconf lo renombra en vez de lanzar NonUniqueNameException.
    zc.register_service(info, allow_name_change=True)
    log.info("Servicio registrado como %s en %s:%d (IP elegida: %s)", info.name, ip_local, puerto, origen_ip)
    return zc, info


def detener_servicio_mdns(zc: Zeroconf, info: ServiceInfo):
    zc.unregister_service(info)
    zc.close()
