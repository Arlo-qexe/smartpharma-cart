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
    # connect() sobre UDP no envía tráfico: solo elige la interfaz de salida.
    # En una LAN aislada (sin ruta por defecto) falla, así que se cae a la IP
    # del hostname y, en último caso, a loopback.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
        return ip
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
    # allow_name_change: si el nombre ya existe en la red (reinicio rápido u
    # otra PC), zeroconf lo renombra en vez de lanzar NonUniqueNameException.
    zc.register_service(info, allow_name_change=True)
    print(f"[mDNS] Servicio registrado como {info.name} en {ip_local}:{puerto}")
    return zc, info


def detener_servicio_mdns(zc: Zeroconf, info: ServiceInfo):
    zc.unregister_service(info)
    zc.close()
