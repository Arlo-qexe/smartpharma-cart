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


def _enviar_crudo(puerto, *campos):
    import struct
    from protocol_constants import FRAMING_STRUCT_FORMAT
    sock = socket.create_connection(("127.0.0.1", puerto), timeout=3)
    for c in campos:
        sock.sendall(struct.pack(FRAMING_STRUCT_FORMAT, c))
    return sock


def _leer(sock):
    from mock_orangepi_client import _leer_respuesta
    return _leer_respuesta(sock)


def test_cantidad_excesiva_se_rechaza(puerto):
    from protocol_constants import MAX_IMAGENES_POR_LOTE
    sock = _enviar_crudo(puerto, MAX_IMAGENES_POR_LOTE + 1)
    assert _leer(sock)["clasificacion"] == "ERROR_REVISION_MANUAL"
    sock.close()


def test_tamano_excesivo_se_rechaza(puerto):
    from protocol_constants import TAMANO_MAX_IMAGEN_BYTES
    sock = _enviar_crudo(puerto, 1, TAMANO_MAX_IMAGEN_BYTES + 1)
    assert _leer(sock)["clasificacion"] == "ERROR_REVISION_MANUAL"
    sock.close()


def test_cliente_colgado_libera_el_hilo(puerto, monkeypatch):
    monkeypatch.setattr(server, "TIMEOUT_INACTIVIDAD_RECEPCION_S", 0.3)
    sock = _enviar_crudo(puerto, 1)  # promete una imagen y se calla
    sock.settimeout(3)
    assert sock.recv(1) == b""       # el servidor cierra tras el timeout
    sock.close()


def test_lote_queda_registrado_en_el_panel(puerto):
    enviar_lote_de_prueba(puerto=puerto, num_imagenes=5, tamano_bytes=100)
    ultimo = server.estado.snapshot()["lotes"][0]
    assert ultimo["n_imagenes"] == 5 and ultimo["resultado"] == "TIPO_A"
    assert server.estado.foto(ultimo["id"], 3) == bytes([3]) * 100


def test_lote_vacio_genera_alarma_en_el_panel(puerto):
    enviar_lote_vacio(puerto=puerto)
    snap = server.estado.snapshot()
    assert snap["alarmas"][0]["motivo"] == "fallo_captura"
    assert snap["alarmas"][0]["lote_id"] == snap["lotes"][0]["id"]
    assert snap["alarma_activa"] is True
