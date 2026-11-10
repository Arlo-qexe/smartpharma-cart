"""Pruebas de spm/identificacion.py con capturas sintéticas en el mismo formato que envía el firmware. No necesitan placa, pyserial ni scipy."""
import math
import os
import tempfile
import unittest

import numpy as np

from spm.identificacion import ajustar_escalon, ajustar_motor, analizar_rampa, cargar_csv, guardar_csv, informe, leer_capturas, reescalar


def simular(motor, modo, duty, ms, K=900.0, tau=0.06, u0=0.08, ppr=1500.0, dec=1, pre=100, post=500, signo=1, aborto=0, ack=True, seed=0):
    """Líneas que enviaría {"cmd":"dump"}: planta de 1er orden con zona muerta u0, encoder cuantizado y periodo de muestreo con jitter."""
    rng = np.random.default_rng(seed + motor)
    dpc = 360.0 / ppr
    t, th, om, k, muestras = 0.0, 0.0, 0.0, 0, []
    total = (pre + ms + post) * 1e-3
    while t < total:
        dt = 1e-3 * (1 + rng.uniform(-0.05, 0.05))
        t1 = t - pre * 1e-3
        u = 0.0
        if 0 <= t1 < ms * 1e-3:
            f = 1.0 if modo == "step" else (t1 / (ms * 1e-3) if modo == "ramp" else 1 - abs(2 * t1 / (ms * 1e-3) - 1))
            u = duty * f
        ue = u - math.copysign(u0, u) if abs(u) > u0 else 0.0
        om += dt * (signo * K * ue - om) / tau
        th += om * dt
        k += 1
        if k % dec == 0:
            muestras.append((int(t * 1e6), int(round(th / dpc)), int(round(u * 1000))))
        t += dt
    cab = '{"cap":{"motor":%d,"mode":"%s","n":%d,"dec":%d,"duty":%.4f,"pre_ms":%d,"ms":%d,"post_ms":%d,"ppr":%.1f,"dpc":%.6f,"abort":%d}}' % (
        motor, modo, len(muestras), dec, duty, pre, ms, post, ppr, dpc, aborto)
    lineas = [cab]
    for i in range(0, len(muestras), 8):
        lineas.append('{"c":[' + ",".join("%d,%d,%d" % m for m in muestras[i:i + 8]) + "]}")
    if ack:
        lineas.append('{"ack":"dump","n":%d}' % len(muestras))
    return lineas


def plan(motor=1, K=900.0, tau=0.06, u0=0.08, signo=1, duties=(0.25, 0.4, 0.6), **kw):
    lineas = []
    for s in (+1, -1):
        for d in duties:
            lineas += ["telemetría basura", '{"t":1,"st":"idle"}'] + simular(motor, "step", s * d, 700, K=K, tau=tau, u0=u0, signo=signo, **kw)
        lineas += simular(motor, "tri", s * 0.6, 3000, K=K, tau=tau, u0=u0, signo=signo, dec=3, post=300, **kw)
    return lineas


class TestLectura(unittest.TestCase):
    def test_lee_todas_las_capturas_e_ignora_telemetria_y_basura(self):
        caps = leer_capturas(plan())
        self.assertEqual(len(caps), 8)
        self.assertTrue(all(c.completa for c in caps))
        self.assertEqual({c.modo for c in caps}, {"step", "tri"})
        c = caps[0]
        self.assertAlmostEqual(c.dpc, 360 / 1500)
        self.assertAlmostEqual(float(c.t[0]), 0.0)
        self.assertLess(abs(c.u.max() - 0.25), 1e-6)

    def test_captura_sin_ack_queda_incompleta(self):
        caps = leer_capturas(simular(1, "step", 0.5, 700, ack=False))
        self.assertEqual(len(caps), 1)
        self.assertFalse(caps[0].completa)

    def test_cabecera_nueva_sin_ack_del_anterior_marca_el_anterior_incompleta(self):
        caps = leer_capturas(simular(1, "step", 0.5, 700, ack=False) + simular(1, "step", 0.3, 700))
        self.assertEqual([c.completa for c in caps], [False, True])

    def test_lineas_json_rotas_se_ignoran(self):
        lineas = simular(1, "step", 0.5, 700)
        lineas[3] = lineas[3][:20]                                          # una línea de datos cortada a la mitad
        caps = leer_capturas(lineas)
        self.assertEqual(len(caps), 1)
        self.assertFalse(caps[0].completa)                                  # faltan muestras: no se fía de ella

    def test_csv_ida_y_vuelta(self):
        c = leer_capturas(simular(2, "tri", 0.6, 3000, dec=3))[0]
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "x.csv")
            guardar_csv(c, ruta)
            c2 = cargar_csv(ruta)
        self.assertEqual((c2.motor, c2.modo, c2.dec, c2.ms), (2, "tri", 3, 3000))
        self.assertTrue(np.allclose(c.pos, c2.pos) and np.allclose(c.u, c2.u, atol=1e-4))


class TestAjuste(unittest.TestCase):
    def test_un_escalon_recupera_K_y_tau(self):
        c = leer_capturas(simular(1, "step", 0.6, 700))[0]
        a = ajustar_escalon(c)
        self.assertAlmostEqual(a.K_efectiva, 900 * (0.6 - 0.08), delta=0.03 * 900 * 0.52)
        self.assertAlmostEqual(a.tau, 0.06, delta=0.012)
        self.assertEqual(a.aviso, "")

    def test_modelo_completo_recupera_la_planta(self):
        m = ajustar_motor(leer_capturas(plan()))
        for K in (m.K_pos, m.K_neg):
            self.assertAlmostEqual(K, 900, delta=45)
        for u0 in (m.u0_pos, m.u0_neg):
            self.assertAlmostEqual(u0, 0.08, delta=0.012)
        for tau in (m.tau_pos, m.tau_neg):
            self.assertAlmostEqual(tau, 0.06, delta=0.012)
        for b in (m.breakaway_pos, m.breakaway_neg, m.u_stop_pos, m.u_stop_neg):
            self.assertAlmostEqual(b, 0.08, delta=0.025)
        self.assertEqual(m.avisos, [])
        s = m.sugerencias_firmware
        self.assertAlmostEqual(s["pwm_min"], 0.08, delta=0.025)
        self.assertAlmostEqual(s["kff"], 1 / (s["K_media_degs"] * (1 - s["pwm_min"])), places=5)

    def test_motor_mas_lento_y_con_otra_zona_muerta(self):
        m = ajustar_motor(leer_capturas(plan(K=400, tau=0.15, u0=0.2, duties=(0.35, 0.5, 0.7))))
        self.assertAlmostEqual(m.K_pos, 400, delta=30)
        self.assertAlmostEqual(m.u0_pos, 0.2, delta=0.02)
        self.assertAlmostEqual(m.tau_pos, 0.15, delta=0.03)

    def test_asimetria_entre_sentidos_se_detecta_por_separado(self):
        lineas = []
        for s, K in ((+1, 900.0), (-1, 450.0)):
            for d in (0.25, 0.4, 0.6):
                lineas += simular(1, "step", s * d, 700, K=K)
        m = ajustar_motor(leer_capturas(lineas))
        self.assertAlmostEqual(m.K_pos, 900, delta=45)
        self.assertAlmostEqual(m.K_neg, 450, delta=25)
        self.assertTrue(any("K muy distinta" in a for a in m.avisos))

    def test_polaridad_invertida_avisa_enc_sign(self):
        m = ajustar_motor(leer_capturas(plan(signo=-1)))
        self.assertLess(m.K_pos, 0)
        self.assertTrue(any("enc_sign" in a for a in m.avisos))

    def test_ventana_corta_avisa(self):
        a = ajustar_escalon(leer_capturas(simular(1, "step", 0.6, 120, tau=0.15, post=100))[0])
        self.assertIn("ventana corta", a.aviso)

    def test_motor_trabado_no_entra_al_modelo(self):
        lineas = simular(1, "step", 0.25, 700, K=900) + simular(1, "step", 0.4, 700, K=900) + simular(1, "step", 0.5, 700, K=0.5)
        m = ajustar_motor(leer_capturas(lineas))
        self.assertTrue(any("casi no giró" in a for a in m.avisos))
        self.assertEqual(m.n_escalones, 2)

    def test_un_solo_ciclo_de_trabajo_no_separa_K_de_u0(self):
        m = ajustar_motor(leer_capturas(simular(1, "step", 0.5, 700) + simular(1, "step", -0.5, 700)))
        self.assertIsNone(m.K_pos)
        self.assertTrue(any("más de un ciclo" in a for a in m.avisos))

    def test_capturas_abortadas_o_incompletas_se_descartan_con_aviso(self):
        lineas = simular(1, "step", 0.25, 700) + simular(1, "step", 0.4, 700) + simular(1, "step", 0.6, 700, aborto=3) + simular(1, "step", 0.5, 700, ack=False)
        m = ajustar_motor(leer_capturas(lineas))
        self.assertEqual(m.n_escalones, 2)
        self.assertEqual(sum("se descarta" in a for a in m.avisos), 2)

    def test_aborto_por_max_deg_se_acepta_pero_no_otros(self):
        m = ajustar_motor(leer_capturas(simular(1, "step", 0.25, 700) + simular(1, "step", 0.6, 700, aborto=1)))
        self.assertEqual(m.n_escalones, 2)

    def test_mezclar_motores_es_un_error(self):
        with self.assertRaises(ValueError):
            ajustar_motor(leer_capturas(simular(1, "step", 0.3, 700) + simular(2, "step", 0.3, 700)))

    def test_sin_capturas_es_un_error(self):
        with self.assertRaises(ValueError):
            ajustar_motor([])


class TestMezclaDePpr(unittest.TestCase):
    def test_avisa_si_se_mezclan_corridas_con_distinto_ppr(self):
        m = ajustar_motor(leer_capturas(plan(ppr=1000.0) + plan(ppr=4532.0)))
        self.assertTrue(any("ppr distinto" in a for a in m.avisos))

    def test_con_un_solo_ppr_no_avisa(self):
        self.assertFalse(any("ppr distinto" in a for a in ajustar_motor(leer_capturas(plan(ppr=4532.0))).avisos))

    def test_reescalar_unifica_y_quita_el_aviso(self):
        caps = leer_capturas(plan(ppr=1000.0) + plan(ppr=4532.0))
        self.assertFalse(any("ppr distinto" in a for a in ajustar_motor([reescalar(c, 4532.0) for c in caps]).avisos))


class TestReescalar(unittest.TestCase):
    def test_reescalar_cambia_K_y_no_u0_ni_tau(self):
        caps = leer_capturas(plan(ppr=1000.0))
        m1 = ajustar_motor(caps)
        m2 = ajustar_motor([reescalar(c, 4000.0) for c in caps])
        self.assertAlmostEqual(m2.K_pos, m1.K_pos * 1000.0 / 4000.0, delta=0.01 * m1.K_pos)
        self.assertAlmostEqual(m2.u0_pos, m1.u0_pos, delta=1e-6)
        self.assertAlmostEqual(m2.tau_pos, m1.tau_pos, delta=1e-6)
        self.assertEqual(reescalar(caps[0], 4000.0).dpc, 360.0 / 4000.0)

    def test_rampa_abortada_por_max_deg_no_da_parada_y_avisa(self):
        lineas = []
        for s in (+1, -1):
            for d in (0.25, 0.4, 0.6):
                lineas += simular(1, "step", s * d, 700)
            lineas += simular(1, "tri", s * 0.6, 3000, dec=3, post=300, aborto=1)
        m = ajustar_motor(leer_capturas(lineas))
        self.assertIsNone(m.u_stop_pos)
        self.assertIsNone(m.u_stop_neg)
        self.assertTrue(any("max_deg" in a for a in m.avisos))
        self.assertIsNotNone(m.breakaway_pos)


class TestRampa(unittest.TestCase):
    def test_arranque_con_y_sin_correccion_por_retraso(self):
        c = leer_capturas(simular(1, "tri", 0.6, 3000, dec=3, post=300))[0]
        crudo, _ = analizar_rampa(c)
        corregido, parada = analizar_rampa(c, K=900, tau=0.06)
        self.assertGreater(crudo, 0.10)                                      # sin corregir sale alto (umbral de cuentas + retraso del motor)
        self.assertAlmostEqual(corregido, 0.08, delta=0.02)
        self.assertAlmostEqual(parada, 0.08, delta=0.02)

    def test_rampa_simple_no_tiene_parada(self):
        _, parada = analizar_rampa(leer_capturas(simular(1, "ramp", 0.6, 3000, dec=3))[0], K=900, tau=0.06)
        self.assertIsNone(parada)

    def test_motor_que_no_gira_no_tiene_arranque(self):
        ba, _ = analizar_rampa(leer_capturas(simular(1, "tri", 0.05, 3000, dec=3))[0])      # el ciclo de trabajo no vence la zona muerta (0.08)
        self.assertIsNone(ba)


class TestInforme(unittest.TestCase):
    def test_informe_contiene_lo_importante(self):
        txt = informe(ajustar_motor(leer_capturas(plan())))
        for clave in ("Motor 1", "K [°/s]", "pwm_min", "kff"):
            self.assertIn(clave, txt)


if __name__ == "__main__":
    unittest.main()
