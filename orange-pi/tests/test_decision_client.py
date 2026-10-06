"""Pruebas del cliente de decisión del regente y del ciclo de main.py, sin
hardware ni PC real. Correr desde orange-pi/:  python -m unittest discover -s tests
"""
import json
import socket
import struct
import sys
import threading
import unittest
from unittest import mock
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ.parent / "shared"))

import main  # noqa: E402
from network import decision_client as dc  # noqa: E402
from network import recuperacion  # noqa: E402
from protocol_constants import (  # noqa: E402
    ACCION_ACTIVAR_ALARMA_LOCAL,
    ACCION_CLASIFICAR,
    ACCION_DESACTIVAR_ALARMA_LOCAL,
    DESTINO_DESCARTE,
    RESULTADO_ERROR_REVISION_MANUAL,
    TIMEOUT_TOTAL_TRANSACCION_S,
)


class DescubridorFalso:
    def __init__(self, ip="127.0.0.1"):
        self.ip, self.invalidaciones = ip, 0

    def obtener_destino_sin_bloquear(self):
        return self.ip, 5000

    def invalidar(self):
        self.invalidaciones += 1


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def respuestas(*lista):
    it = iter(lista)

    def consultar(ip):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return consultar


class EsperarDecision(unittest.TestCase):
    def esperar(self, *lista, alarma=None):
        reloj = Reloj()
        return dc.esperar_decision(DescubridorFalso(), alarma or (lambda: None), intervalo=2.0,
                                   consultar=respuestas(*lista), dormir=reloj.dormir, reloj=reloj)

    def test_pendiente_y_luego_resuelta(self):
        r = self.esperar({"estado": "pendiente"}, {"estado": "pendiente"},
                         {"estado": "resuelta", "destino": "TIPO_B"})
        self.assertEqual(r, "TIPO_B")

    def test_descarte(self):
        self.assertEqual(self.esperar({"estado": "resuelta", "destino": DESTINO_DESCARTE}), "DESCARTE")

    def test_ninguna_levanta_decision_perdida(self):
        with self.assertRaises(dc.DecisionPerdida):
            self.esperar({"estado": "pendiente"}, {"estado": "ninguna"})

    def test_respuestas_fuera_de_contrato(self):
        for r in ({"error": "consulta_invalida"}, {"estado": "resuelta"}, {"estado": "x"}):
            with self.assertRaises(dc.RespuestaInesperada):
                self.esperar(r)

    def test_espera_indefinida_sin_alarma_mientras_la_pc_responde(self):
        llamadas = []
        r = self.esperar(*([{"estado": "pendiente"}] * 500 + [{"estado": "resuelta", "destino": "TIPO_A"}]),
                         alarma=lambda: llamadas.append(1))
        self.assertEqual(r, "TIPO_A")
        self.assertEqual(llamadas, [])  # 1000 s esperando: ningún tope ni alarma

    def test_pc_caida_activa_alarma_una_vez_y_se_recupera(self):
        n_fallos = int(TIMEOUT_TOTAL_TRANSACCION_S / 2) + 5
        llamadas, desc = [], DescubridorFalso()
        reloj = Reloj()
        r = dc.esperar_decision(
            desc, lambda: llamadas.append(1), intervalo=2.0,
            consultar=respuestas(*([ConnectionRefusedError("x")] * n_fallos),
                                 {"estado": "resuelta", "destino": "TIPO_C"}),
            dormir=reloj.dormir, reloj=reloj)
        self.assertEqual(r, "TIPO_C")
        self.assertEqual(llamadas, [1])
        self.assertEqual(desc.invalidaciones, n_fallos)

    def test_pc_que_vuelve_apaga_la_alarma_local(self):
        eventos, reloj = [], Reloj()
        n_fallos = int(TIMEOUT_TOTAL_TRANSACCION_S / 2) + 5
        dc.esperar_decision(
            DescubridorFalso(), lambda: eventos.append("on"), lambda: eventos.append("off"),
            intervalo=2.0,
            consultar=respuestas(*([ConnectionRefusedError("x")] * n_fallos),
                                 {"estado": "pendiente"}, {"estado": "resuelta", "destino": "TIPO_A"}),
            dormir=reloj.dormir, reloj=reloj)
        self.assertEqual(eventos, ["on", "off"])

    def test_fallos_cortos_no_activan_alarma(self):
        llamadas = []
        self.esperar(ConnectionResetError(), {"estado": "resuelta", "destino": "TIPO_A"},
                     alarma=lambda: llamadas.append(1))
        self.assertEqual(llamadas, [])


class ConsultarDecisionPorSocket(unittest.TestCase):
    def test_framing_de_una_consulta(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        visto = {}

        def atender():
            c, _ = srv.accept()
            (n,) = struct.unpack("!I", c.recv(4))
            visto["consulta"] = json.loads(c.recv(n))
            resp = json.dumps({"estado": "resuelta", "destino": "TIPO_A"}).encode()
            c.sendall(struct.pack("!I", len(resp)) + resp)
            c.close()

        threading.Thread(target=atender, daemon=True).start()
        r = dc.consultar_decision("127.0.0.1", srv.getsockname()[1])
        srv.close()
        self.assertEqual(visto["consulta"], {"consulta": "decision_regente"})
        self.assertEqual(r, {"estado": "resuelta", "destino": "TIPO_A"})


class Recuperacion(unittest.TestCase):
    def correr(self, *resultados, alarma_encendida=False):
        eventos, sondeos, reloj = [], [], Reloj()
        it = iter(resultados)

        def enviar(desc, imagenes):
            sondeos.append(len(imagenes))
            return next(it)
        recuperacion.recuperar_comunicacion(
            DescubridorFalso(), lambda: eventos.append("on"), lambda: eventos.append("off"),
            alarma_encendida=alarma_encendida, intervalo=10.0, enviar=enviar, dormir=reloj.dormir)
        return eventos, sondeos, reloj.t

    OK = ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, False)
    FALLO = ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, True)

    def test_sondea_con_lote_vacio_cada_10s_y_se_detiene_al_primer_exito(self):
        eventos, sondeos, t = self.correr(self.FALLO, self.FALLO, self.FALLO, self.OK, alarma_encendida=True)
        self.assertEqual(sondeos, [0, 0, 0, 0])   # siempre lotes vacíos; nunca más tras responder
        self.assertEqual(t, 30.0)                 # 3 esperas de 10 s
        self.assertEqual(eventos, ["off"])        # la alarma ya estaba encendida: solo se apaga

    def test_f2_la_pc_ya_responde_no_enciende_la_alarma(self):
        eventos, sondeos, _ = self.correr(self.OK)
        self.assertEqual((eventos, sondeos), ([], [0]))

    def test_si_la_alarma_no_estaba_la_enciende_tras_el_primer_fallo(self):
        eventos, _, _ = self.correr(self.FALLO, self.FALLO, self.OK)
        self.assertEqual(eventos, ["on", "off"])


class ConMockPC(unittest.TestCase):
    """Cliente real (enviar_lote + esperar_decision) contra tests/mock_pc_server.py."""

    @staticmethod
    def libre():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def levantar(self, **kw):
        sys.path.insert(0, str(RAIZ / "tests"))
        import mock_pc_server
        self.p_lotes, self.p_dec = self.libre(), self.libre()
        mock_pc_server.iniciar_mock(puerto=self.p_lotes, puerto_decision=self.p_dec,
                                    bloquear=False, **kw)
        desc = DescubridorFalso()
        desc.obtener_destino = lambda: ("127.0.0.1", self.p_lotes)
        return desc

    def esperar(self, desc):
        return dc.esperar_decision(
            desc, lambda: self.fail("no debía activarse la alarma local"), intervalo=0.1,
            consultar=lambda ip: dc.consultar_decision(ip, self.p_dec))

    def test_lote_vacio_abre_decision_y_se_resuelve(self):
        from network.tcp_client import enviar_lote
        desc = self.levantar(segundos_hasta_decision=0.5, destino_decision="DESCARTE")
        resultado, fallo = enviar_lote(desc, [])
        self.assertEqual((resultado["clasificacion"], fallo), (RESULTADO_ERROR_REVISION_MANUAL, False))
        self.assertEqual(dc.consultar_decision("127.0.0.1", self.p_dec), {"estado": "pendiente"})
        self.assertEqual(self.esperar(desc), "DESCARTE")

    def test_lote_correcto_cierra_la_decision(self):
        from network.tcp_client import enviar_lote
        desc = self.levantar(segundos_hasta_decision=0)
        enviar_lote(desc, [])
        self.assertEqual(dc.consultar_decision("127.0.0.1", self.p_dec)["estado"], "resuelta")
        resultado, _ = enviar_lote(desc, [b"j"] * 5)
        self.assertEqual(resultado["clasificacion"], "TIPO_A")
        with self.assertRaises(dc.DecisionPerdida):
            self.esperar(desc)

    def test_sin_decision_responde_ninguna(self):
        from network.tcp_client import enviar_lote
        desc = self.levantar(con_decision=False)
        enviar_lote(desc, [])
        with self.assertRaises(dc.DecisionPerdida):
            self.esperar(desc)

    def test_consulta_invalida(self):
        self.levantar()
        for payload in (b'{"consulta": "otra"}', b"no es json"):
            with socket.create_connection(("127.0.0.1", self.p_dec), timeout=3) as c:
                c.sendall(struct.pack("!I", len(payload)) + payload)
                (n,) = struct.unpack("!I", c.recv(4))
                self.assertEqual(json.loads(c.recv(n)), {"error": "consulta_invalida"})


class EnlaceFalso:
    def __init__(self, confirmar=True):
        self.enviados, self.confirmar = [], confirmar

    def enviar(self, m):
        self.enviados.append(m)

    def recibir(self, timeout=None):
        return None


class CicloMain(unittest.TestCase):
    def correr(self, enlace, lote, decision=None, imagenes=None, parchar_esperar=True):
        originales = (main.introducir_objeto, main.capturar_lote_completo,
                      main.enviar_lote, main.esperar_decision)
        enviados_a_pc = []
        main.introducir_objeto = lambda e: enlace.confirmar
        main.capturar_lote_completo = lambda e, c: imagenes or [b"j"] * 5

        def fake_enviar(d, imgs):
            enviados_a_pc.append(len(imgs))
            return lote
        main.enviar_lote = fake_enviar

        def fake_esperar(d, on, off=None):
            if isinstance(decision, Exception):
                raise decision
            return decision
        if parchar_esperar:
            main.esperar_decision = fake_esperar
        try:
            return main.ciclo_de_un_objeto(enlace, None, None), enviados_a_pc
        finally:
            (main.introducir_objeto, main.capturar_lote_completo,
             main.enviar_lote, main.esperar_decision) = originales

    def test_clasificacion_automatica(self):
        e = EnlaceFalso()
        ok, _ = self.correr(e, ({"clasificacion": "TIPO_A"}, False))
        self.assertTrue(ok)
        self.assertEqual(e.enviados, [{"accion": ACCION_CLASIFICAR, "destino": "TIPO_A"}])

    def test_error_de_la_pc_espera_decision_sin_alarma_local(self):
        e = EnlaceFalso()
        ok, _ = self.correr(e, ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, False),
                            decision="DESCARTE")
        self.assertTrue(ok)
        self.assertEqual(e.enviados, [{"accion": ACCION_CLASIFICAR, "destino": "DESCARTE"}])

    def test_decision_perdida_se_recupera_y_vuelve_a_esperar(self):
        e = EnlaceFalso()
        llamadas = []
        originales = (main.recuperar_comunicacion, main.esperar_decision)
        main.recuperar_comunicacion = lambda *a, **k: llamadas.append("recuperar")
        secuencia = iter([dc.DecisionPerdida("x"), "DESCARTE"])

        def esperar(d, on, off):
            r = next(secuencia)
            if isinstance(r, Exception):
                raise r
            return r
        main.esperar_decision = esperar
        try:
            ok, _ = self.correr(e, ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, False),
                                decision="no se usa", parchar_esperar=False)
        finally:
            main.recuperar_comunicacion, main.esperar_decision = originales
        self.assertTrue(ok)
        self.assertEqual(llamadas, ["recuperar"])
        self.assertEqual(e.enviados, [{"accion": ACCION_CLASIFICAR, "destino": "DESCARTE"}])

    def test_introducir_objeto_fallido_envia_lote_vacio_a_la_pc(self):
        e = EnlaceFalso(confirmar=False)
        ok, a_pc = self.correr(e, ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, False),
                               decision="TIPO_B")
        self.assertEqual(a_pc, [0])
        self.assertEqual(e.enviados[-1], {"accion": ACCION_CLASIFICAR, "destino": "TIPO_B"})

    def test_fallo_de_red_activa_alarma_sondea_y_espera_al_regente(self):
        e = EnlaceFalso()
        llamadas = []
        original = main.recuperar_comunicacion

        def fake_recuperar(desc, on, off, alarma_encendida=False, **k):
            llamadas.append(alarma_encendida)
            on(); off()
        main.recuperar_comunicacion = fake_recuperar
        try:
            ok, _ = self.correr(e, ({"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}, True),
                                decision="TIPO_C")
        finally:
            main.recuperar_comunicacion = original
        self.assertTrue(ok)
        self.assertEqual(llamadas, [True])   # la alarma ya estaba encendida (F1)
        self.assertEqual(e.enviados[0], {"accion": ACCION_ACTIVAR_ALARMA_LOCAL})
        self.assertIn({"accion": ACCION_DESACTIVAR_ALARMA_LOCAL}, e.enviados)
        # El ciclo no se reanuda solo: clasifica con el destino del regente, no con el error.
        self.assertEqual(e.enviados[-1], {"accion": ACCION_CLASIFICAR, "destino": "TIPO_C"})


if __name__ == "__main__":
    unittest.main()
