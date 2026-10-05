"""
Registro del servicio `_ocr-service._tcp.local.` para que la Orange Pi
descubra esta PC automáticamente (ver docs/arquitectura_comunicacion.md,
sección 3.1).

Requiere: pip install zeroconf
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import MDNS_SERVICE_TYPE  # noqa: E402

from zeroconf import ServiceInfo, Zeroconf


def obtener_ip_local() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def registrar_servicio_mdns(puerto: int, nombre_servicio: str = "OCRServer"):
    zc = Zeroconf()
    ip_local = obtener_ip_local()
    info = ServiceInfo(
        MDNS_SERVICE_TYPE,
        f"{nombre_servicio}.{MDNS_SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip_local)],
        port=puerto,
        properties={},
    )
    zc.register_service(info)
    print(f"[mDNS] Servicio registrado en {ip_local}:{puerto}")
    return zc, info


def detener_servicio_mdns(zc: Zeroconf, info: ServiceInfo):
    zc.unregister_service(info)
    zc.close()
