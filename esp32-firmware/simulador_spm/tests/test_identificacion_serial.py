"""Pruebas del cliente de captura (spm/identificacion_serial.py) contra un firmware falso en Python que habla el mismo protocolo. Sin placa ni pyserial."""
import json
import os
import tempfile
import unittest
from collections import deque

try:
    from test_identificacion import simular
except ImportError:                                           # al correr como `python -m unittest tests.test_identificacion_serial`
    from tests.test_identificacion import simular

from spm.identificacion import cargar_csv
from spm.identificacion_serial import Capturador, ErrorCaptura, ejecutar_plan, medir_ppr, niveles_de_escalon


class FirmwareFalso:
    """Responde como el firmware a status / reset / capture / dump, con un motor de 1er orden con zona muerta. El reloj avanza con cada lectura."""

    def __init__(self, K=900.0, tau=0.06, u0=0.08, pwm_max=0.6, ppr=1500.0, mudo=False, err_en=None):
        self.K, self.tau, self.u0, self.pwm_max, self.ppr, self.mudo, self.err_en = K, tau, u0, pwm_max, ppr, mudo, err_en
        self.cola, self.t, self.ult, self.pos, self.enviados = deque(), 0.0, None, 0, []

    def reloj(self):
        return self.t

    def _poner(self, d):
        self.cola.append(json.dumps(d))

    def write(self, datos):
        d = json.loads(datos.decode())
        self.enviados.append(d)
        if self.mudo:
            return
        cmd = d.get("cmd")
        if cmd == "status":
            self._poner({"info": "status", "st": "idle", "pwm_max": [self.pwm_max] * 3, "ppr": [self.ppr] * 3})
        elif cmd == "reset":
            self._poner({"ack": "reset"})
        elif cmd == "capture":
            if self.err_en == "capture" or not 1 <= d["motor"] <= 3:
                self._poner({"err": "capture", "hint": "motor 1..3"})
                return
            duty = max(-self.pwm_max, min(self.pwm_max, d["duty"]))
            self.ult = dict(d, duty=duty)
            self._poner({"ack": "capture", "motor": d["motor"], "mode": d["mode"], "samples": 1000})
            self._poner({"t": 1, "st": "capture"})                           # telemetría mezclada
            self._poner({"ack": "capture_done", "motor": d["motor"], "n": 1000, "abort": 0})
        elif cmd == "dump":
            u = self.ult
            for linea in simular(u["motor"], u["mode"], u["duty"], u["ms"], K=self.K, tau=self.tau, u0=self.u0, ppr=self.ppr, dec=u["dec"],
                                 pre=u["pre_ms"], post=u["post_ms"]):
                self._poner(json.loads(linea))
        elif cmd == "stop":
            self._poner({"ack": "stop"})

    def readline(self):
        self.t += 0.01
        if self.cola:
            return (self.cola.popleft() + "\n").encode()
        if int(self.t * 100) % 5 == 0:
            return (json.dumps({"t": int(self.t * 1000), "st": "idle", "m1": {"posRef": 0, "pos": self.pos, "ok": True}}) + "\n").encode()
        return b""


def capturador(fw):
    return Capturador(fw, log=lambda *_: None, reloj=fw.reloj, dormir=lambda s: None)


class TestPlan(unittest.TestCase):
    def test_plan_completo_recupera_el_motor(self):
        fw = FirmwareFalso()
        with tempfile.TemporaryDirectory() as d:
            caps, m = ejecutar_plan(capturador(fw), 1, d, log=lambda *_: None)
            self.assertEqual(len(caps), 2 + 8)                                        # 2 rampas + 4 escalones por sentido
            self.assertEqual(len([f for f in os.listdir(d) if f.endswith(".csv")]), 10)
            self.assertTrue(all(cargar_csv(os.path.join(d, f)).completa for f in os.listdir(d)))
        self.assertAlmostEqual(m.K_pos, 900, delta=45)
        self.assertAlmostEqual(m.u0_pos, 0.08, delta=0.015)
        self.assertAlmostEqual(m.tau_pos, 0.06, delta=0.012)
        self.assertAlmostEqual(m.sugerencias_firmware["pwm_min"], 0.08, delta=0.03)

    def test_la_orden_de_captura_lleva_los_parametros_y_el_limite_de_giro(self):
        fw = FirmwareFalso()
        with tempfile.TemporaryDirectory() as d:
            ejecutar_plan(capturador(fw), 2, d, ms=400, max_deg=90, log=lambda *_: None)
        pasos = [e for e in fw.enviados if e.get("cmd") == "capture" and e["mode"] == "step"]
        self.assertTrue(pasos and all(e["motor"] == 2 and e["ms"] == 400 and e["max_deg"] == 90 for e in pasos))
        self.assertEqual(fw.enviados[1], {"cmd": "reset"})                              # primero se limpian fallas

    def test_hold_se_envia_a_cada_captura(self):
        fw = FirmwareFalso()
        with tempfile.TemporaryDirectory() as d:
            ejecutar_plan(capturador(fw), 1, d, hold=True, log=lambda *_: None)
        caps = [e for e in fw.enviados if e.get("cmd") == "capture"]
        self.assertTrue(caps and all(e["hold"] == 1 for e in caps))

    def test_zona_muerta_grande_hace_que_los_escalones_suban(self):
        fw = FirmwareFalso(u0=0.35, pwm_max=0.9)
        with tempfile.TemporaryDirectory() as d:
            caps, m = ejecutar_plan(capturador(fw), 1, d, log=lambda *_: None)
        niveles = sorted({abs(c.duty) for c in caps if c.modo == "step"})
        self.assertGreater(min(niveles), 0.4)                                            # ninguno por debajo de la zona muerta
        self.assertAlmostEqual(m.u0_pos, 0.35, delta=0.03)

    def test_tope_se_limita_a_pwm_max_de_la_placa(self):
        fw = FirmwareFalso(pwm_max=0.5)
        with tempfile.TemporaryDirectory() as d:
            caps, _ = ejecutar_plan(capturador(fw), 1, d, tope=0.9, log=lambda *_: None)
        self.assertLessEqual(max(abs(c.duty) for c in caps), 0.5 + 1e-9)

    def test_motor_que_no_gira_da_un_error_claro(self):
        fw = FirmwareFalso(K=0.0)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ErrorCaptura) as cm:
                ejecutar_plan(capturador(fw), 1, d, log=lambda *_: None)
        self.assertIn("no gira", str(cm.exception))

    def test_pwm_max_demasiado_bajo(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ErrorCaptura) as cm:
                ejecutar_plan(capturador(FirmwareFalso(pwm_max=0.1)), 1, d, log=lambda *_: None)
        self.assertIn("pwm_max", str(cm.exception))

    def test_error_del_firmware_se_propaga(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ErrorCaptura) as cm:
                ejecutar_plan(capturador(FirmwareFalso(err_en="capture")), 1, d, log=lambda *_: None)
        self.assertIn("error", str(cm.exception))

    def test_firmware_mudo_agota_el_tiempo(self):
        with self.assertRaises(ErrorCaptura) as cm:
            capturador(FirmwareFalso(mudo=True)).estado()
        self.assertIn("sin respuesta", str(cm.exception))

    def test_ppr_tomado_de_la_telemetria(self):
        fw = FirmwareFalso()

        def girar():
            fw.pos = 1540
        self.assertEqual(medir_ppr(capturador(fw), 1, girar, log=lambda *_: None), 1540.0)

    def test_niveles_requieren_margen_sobre_la_zona_muerta(self):
        n = niveles_de_escalon(0.1, 0.6)
        self.assertEqual(len(n), 4)
        self.assertTrue(n[0] > 0.1 and n[-1] == 0.6 and n == sorted(n))
        with self.assertRaises(ErrorCaptura):
            niveles_de_escalon(0.55, 0.6)


if __name__ == "__main__":
    unittest.main()
