"""Pruebas del transporte TCP (server.py) sin mDNS ni hardware real.

Correr desde pc/:  python3 -m pytest tests/
"""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "tests"))

import server  # noqa: E402
from mock_orangepi_client import enviar_lote_de_prueba, enviar_lote_vacio  # noqa: E402


@pytest.fixture(scope="module")
def puerto():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
    threading.Thread(target=server.iniciar_servidor, args=(p,), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return p


def test_lote_normal(puerto, capsys):
    enviar_lote_de_prueba(puerto=puerto)
    assert "TIPO_A" in capsys.readouterr().out


def test_lote_vacio_da_error_revision_manual(puerto, capsys):
    enviar_lote_vacio(puerto=puerto)
    assert "ERROR_REVISION_MANUAL" in capsys.readouterr().out


def test_imagen_grande_fragmentada(puerto, capsys):
    enviar_lote_de_prueba(puerto=puerto, num_imagenes=5, tamano_bytes=2_000_000)
    assert "TIPO_A" in capsys.readouterr().out


def test_ip_local_siempre_devuelve_algo(monkeypatch):
    import mdns_service

    class SocketSinRuta:
        def connect(self, *_):
            raise OSError("Network is unreachable")

        def close(self):
            pass

    monkeypatch.setattr(mdns_service.socket, "socket", lambda *a, **k: SocketSinRuta())
    assert mdns_service.obtener_ip_local()
