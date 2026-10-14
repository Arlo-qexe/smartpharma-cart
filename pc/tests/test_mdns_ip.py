"""Pruebas de la elección de la IP que la PC anuncia por mDNS (mdns_service.py).
Correr desde pc/:  python3 -m pytest tests/
"""
import logging
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import mdns_service as m  # noqa: E402

INTERFACES_CASERAS = [
    ("lo", "127.0.0.1"),
    ("docker0", "172.17.0.1"),
    ("wlx502b73d82f11", "192.168.0.50"),
]


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    monkeypatch.delenv("PC_IP", raising=False)
    # Que ninguna prueba dependa de la red real ni del hostname de esta máquina:
    monkeypatch.setattr(m, "_ip_por_ruta", lambda: None)
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: [])

    def hostname_prohibido(*a, **k):
        raise AssertionError("no se debe usar el hostname: en Ubuntu da 127.0.1.1")
    monkeypatch.setattr(socket, "gethostbyname", hostname_prohibido)


def test_es_utilizable():
    for buena in ("192.168.0.50", "10.0.0.7", "172.16.4.2"):
        assert m._es_utilizable(buena)
    for mala in ("127.0.0.1", "127.0.1.1", "169.254.3.4", "0.0.0.0", "224.0.0.251", "abc", "", "999.1.1.1"):
        assert not m._es_utilizable(mala)


def test_la_ruta_por_defecto_es_la_primera_opcion(monkeypatch):
    monkeypatch.setattr(m, "_ip_por_ruta", lambda: "192.168.0.50")
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: [("eth9", "10.9.9.9")])   # no se mira
    assert m.elegir_ip_anunciada() == ("192.168.0.50", "ruta por defecto")
    assert m.obtener_ip_local() == "192.168.0.50"


def test_pc_ip_gana_sobre_todo(monkeypatch):
    monkeypatch.setenv("PC_IP", "10.1.2.3")
    monkeypatch.setattr(m, "_ip_por_ruta", lambda: "192.168.0.50")
    assert m.elegir_ip_anunciada() == ("10.1.2.3", "variable PC_IP")


def test_pc_ip_invalida_se_ignora_con_aviso(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("PC_IP", "no-es-una-ip")
    monkeypatch.setattr(m, "_ip_por_ruta", lambda: "192.168.0.50")
    assert m.elegir_ip_anunciada() == ("192.168.0.50", "ruta por defecto")
    assert "PC_IP='no-es-una-ip' no es una IPv4 válida" in caplog.text


def test_sin_ruta_por_defecto_se_usa_la_interfaz_real_sin_tocar_el_hostname(monkeypatch):
    """El caso que antes fallaba: sin ruta por defecto se anunciaba 127.0.1.1."""
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: INTERFACES_CASERAS)
    ip, origen = m.elegir_ip_anunciada()
    assert ip == "192.168.0.50"
    assert "wlx502b73d82f11" in origen and "no hay ruta por defecto" in origen


def test_el_respaldo_descarta_virtuales_y_enlace_local_y_prefiere_wifi_o_ethernet(monkeypatch):
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: [
        ("lo", "127.0.0.1"),
        ("docker0", "172.17.0.1"),
        ("br-1a2b3c", "172.18.0.1"),
        ("veth1234", "172.19.0.2"),
        ("eth0", "169.254.7.7"),          # enlace-local: nadie puede alcanzarla
        ("tun0", "10.8.0.2"),             # VPN: utilizable, pero menos preferida
        ("enp3s0", "192.168.5.20"),       # Ethernet
    ])
    assert m.elegir_ip_anunciada()[0] == "192.168.5.20"
    # sin Wi-Fi ni Ethernet utilizables, se acepta la VPN
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: [("lo", "127.0.0.1"), ("tun0", "10.8.0.2")])
    assert m.elegir_ip_anunciada()[0] == "10.8.0.2"


def test_sin_nada_utilizable_cae_a_loopback_y_lo_dice(monkeypatch):
    monkeypatch.setattr(m, "_interfaces_ipv4", lambda: [("lo", "127.0.0.1"), ("docker0", "172.17.0.1")])
    ip, origen = m.elegir_ip_anunciada()
    assert ip == "127.0.0.1" and "loopback" in origen


class _SocketFalso:
    """UDP 'conectado' cuya IP de origen es la que se le indique, o que falla."""
    def __init__(self, ip=None):
        self._ip = ip
    def __call__(self, *a, **k):
        return self
    def connect(self, *_):
        if self._ip is None:
            raise OSError("Network is unreachable")
    def getsockname(self):
        return (self._ip, 0)
    def close(self):
        pass


def test_ip_por_ruta_descarta_loopback_y_errores(monkeypatch):
    monkeypatch.undo()                          # aquí sí se prueba la función real, con un socket falso
    import mdns_service as real
    for ip, esperado in (("192.168.0.50", "192.168.0.50"), ("127.0.1.1", None), (None, None)):
        monkeypatch.setattr(real.socket, "socket", _SocketFalso(ip))
        assert real._ip_por_ruta() == esperado
