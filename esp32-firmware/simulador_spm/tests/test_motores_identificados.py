"""spm/motores_identificados.py con los modelos y ganancias entregados (identificacion/)."""
import unittest

import numpy as np

from spm import SPM, cargar_config, desde_inclinacion, planificar_con_paradas
from spm.motores_identificados import cargar_ganancias, cargar_planta, seguir_con_motores_identificados


class TestMotoresIdentificados(unittest.TestCase):
    def test_carga(self):
        for n in (1, 2, 3):
            p, g = cargar_planta(n), cargar_ganancias(n)
            self.assertTrue(250 < p.K_pos < 290 and 0.02 < p.tau < 0.03)
            self.assertEqual(g.ctrl, "casc")
            self.assertAlmostEqual(g.kpp, 12.0)

    def test_seguimiento_corto(self):
        spm = SPM(); trans, lim = cargar_config()
        Rs = [desde_inclinacion(0, 0, 0), desde_inclinacion(np.radians(8), 0, 0), desde_inclinacion(np.radians(8), 0, np.radians(30))]
        tray = planificar_con_paradas(spm, trans, lim, Rs, [2.0, 2.0], 0.04)
        reg, info = seguir_con_motores_identificados(spm, trans, lim, tray)
        self.assertEqual(len(info["por_motor"]), 3)
        self.assertFalse(any(m["inestable"] for m in info["por_motor"]))
        self.assertLess(info["error_plato_max_deg"], 5.0)


if __name__ == "__main__":
    unittest.main()
