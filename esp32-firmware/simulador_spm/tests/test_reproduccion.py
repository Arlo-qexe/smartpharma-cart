"""Pruebas de la reproducción del programa por UART (spm/reproduccion.py) contra la placa simulada."""
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from spm.placa_simulada import PlacaSimulada
from spm.reproduccion import Abortado, Reproductor, guardar_csv, trayectoria_programa

F = 20.0


def montar(**kw):
    p = PlacaSimulada(factor_tiempo=F, K=270.0, tau=0.027, u0=0.04, ppr=4532.0, pwm_max=0.7, **kw)
    p.timeout = 0.004
    return p, Reproductor(p, log=lambda *_: None, reloj=lambda: time.monotonic() * F, dormir=lambda s: time.sleep(s / F))


class TestReproduccion(unittest.TestCase):
    def test_trayectoria_programa(self):
        t, q = trayectoria_programa(130.0, 1.0)
        self.assertEqual(q.shape, (len(t), 3))
        self.assertTrue(np.allclose(q[0], 0))
        self.assertAlmostEqual(t[1] - t[0], 0.04)
        self.assertLess(np.abs(np.diff(q, axis=0) / 0.04).max(), 140.0)          # respeta el tope de velocidad (con holgura del remuestreo)

    def test_reproduce_y_registra(self):
        p, r = montar()
        r.preparar(None, 4532.0)
        t = np.arange(0, 3.0, 0.04)
        q = np.column_stack([30 * np.sin(np.pi * t / 6)] * 3)
        res = r.reproducir(t, q)
        p.close()
        self.assertTrue(res.ok, res.motivo)
        self.assertGreater(len(res.filas), 50)
        self.assertEqual(len(res.filas[0]), 13)
        with tempfile.TemporaryDirectory() as d:
            guardar_csv(res, Path(d) / "r.csv")
            self.assertEqual(len((Path(d) / "r.csv").read_text().splitlines()), len(res.filas) + 2)

    def test_ppr_distinto_impide_empezar(self):
        p, r = montar()
        p.ppr[2] = 4352.0
        with self.assertRaises(Abortado) as cm:
            r.preparar(None, 4532.0)
        p.close()
        self.assertIn("[3]", str(cm.exception))

    def test_falla_de_la_placa_para(self):
        p, r = montar()
        r.preparar(None, 4532.0)
        t = np.arange(0, 3.0, 0.04)
        q = np.zeros((len(t), 3))
        orig = r.leer

        def leer():
            orig()
            if r.ult and r.ult["t"] > 600:
                p.estado = "fault"
        r.leer = leer
        res = r.reproducir(t, q)
        p.close()
        self.assertFalse(res.ok)
        self.assertIn("fault", res.motivo)
        self.assertEqual(p.enviados[-1], {"cmd": "stop"})

    def test_error_grande_para(self):
        p, r = montar(falla="sin_giro")
        r.preparar(None, 4532.0)
        t = np.arange(0, 4.0, 0.04)
        q = np.column_stack([np.linspace(0, 200, len(t))] * 3)
        res = r.reproducir(t, q, e_max=10.0)
        p.close()
        self.assertFalse(res.ok)
        self.assertIn("seguimiento", res.motivo)

    def test_rango_excesivo(self):
        p, r = montar()
        r.preparar(None, 4532.0)
        with self.assertRaises(Abortado):
            r.reproducir(np.arange(0, 1, 0.04), np.full((25, 3), 2000.0))
        p.close()


if __name__ == "__main__":
    unittest.main()
