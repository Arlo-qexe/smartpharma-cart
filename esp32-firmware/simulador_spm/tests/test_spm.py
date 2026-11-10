"""Pruebas del simulador:  python -m unittest discover -s tests -v   (desde simulador_spm/)"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spm import (SPM, Transmision, Limites, desde_inclinacion, a_inclinacion, FueraDeEspacioDeTrabajo, planificar, CaminoPrecesion,
                 CaminoSlerp, seguir_trayectoria)
from spm.rot import rotz, rot_axis, log_so3, exp_so3, quat_de_R, R_de_quat, angulo_entre


def aleatoria(rng, tilt_max=12):
    return desde_inclinacion(math.radians(rng.uniform(0, tilt_max)), rng.uniform(0, 2 * math.pi), math.radians(rng.uniform(-25, 25)))


class Rotaciones(unittest.TestCase):
    def test_exp_log_y_cuaternion(self):
        rng = np.random.default_rng(1)
        for _ in range(50):
            w = rng.normal(size=3)
            w *= rng.uniform(0.1, 3.0) / np.linalg.norm(w)
            R = exp_so3(w)
            self.assertLess(np.linalg.norm(log_so3(R) - w), 1e-9)
            self.assertLess(angulo_entre(R, R_de_quat(quat_de_R(R))), 1e-9)

    def test_inclinacion_ida_y_vuelta(self):
        rng = np.random.default_rng(2)
        for _ in range(50):
            t, a, s = rng.uniform(0.01, 0.5), rng.uniform(-3, 3), rng.uniform(-0.6, 0.6)
            t2, a2, s2 = a_inclinacion(desde_inclinacion(t, a, s))
            self.assertAlmostEqual(t, t2, 9); self.assertAlmostEqual(math.atan2(math.sin(a - a2), math.cos(a - a2)), 0, 9); self.assertAlmostEqual(s, s2, 9)


class Cinematica(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spm = SPM()

    def test_pose_inicial_con_el_disco_horizontal(self):
        n_cad = self.spm.g.disco_normal
        self.assertAlmostEqual(math.degrees(math.acos(n_cad[2] / np.linalg.norm(n_cad))), 16.02, 1)         # en el CAD el disco está 16° inclinado
        plat = self.spm.orientaciones_cuerpos(np.eye(3), np.zeros(3))["plataforma"]
        self.assertLess(np.abs(plat @ n_cad / np.linalg.norm(n_cad) - [0, 0, 1]).max(), 1e-9)               # y en la pose inicial queda horizontal
        self.assertGreater(np.abs(self.spm.th_h).max(), math.radians(5))                                      # los NNR no están en su pose de ensamblaje
        spm_cad = SPM(horizontal=False); R, th = np.eye(3), np.zeros(3)
        self.assertLess(angulo_entre(spm_cad.fk(self.spm.th_h), self.spm.R_h), 1e-9)

    def test_pose_de_ensamblaje(self):
        self.assertLess(np.abs(self.spm.ik(np.eye(3))).max(), 1e-9)
        self.assertLess(angulo_entre(self.spm.fk(np.zeros(3)), np.eye(3)), 1e-9)

    def test_torsion_pura_mueve_los_tres_motores_igual(self):
        for s in (-25, -10, 10, 25):
            th = self.spm.ik(rotz(math.radians(s)))
            self.assertLess(np.abs(th - math.radians(s)).max(), 1e-9)

    def test_ik_fk_ida_y_vuelta(self):
        rng = np.random.default_rng(3)
        for _ in range(200):
            R = aleatoria(rng)
            th = self.spm.ik(R)
            self.assertLess(np.abs(self.spm.residuo(R, th)).max(), 1e-12)
            self.assertLess(angulo_entre(self.spm.fk(th, R0=np.eye(3)), R), 1e-8)

    def test_cierre_del_brazov(self):
        """El Brazov rotado debe llevar a2 a donde está el eje de la plataforma, y conservar a1 en el Brasop (u_i)."""
        rng = np.random.default_rng(4)
        for _ in range(50):
            R = aleatoria(rng); th = self.spm.ik(R); cu = self.spm.orientaciones_cuerpos(R, th); Rc, thc = self.spm.a_cad(R, th)       # ejes en el marco del CAD
            for i in range(3):
                B = cu["brazov_%d" % (i + 1)]
                self.assertLess(np.linalg.norm(B @ self.spm.a2[i] - Rc @ self.spm.a2[i]), 1e-9)
                self.assertLess(np.linalg.norm(B @ self.spm.a1[i] - rotz(thc[i]) @ self.spm.a1[i]), 1e-9)

    def test_jacobiano_contra_diferencias_finitas(self):
        rng = np.random.default_rng(5); h = 1e-6
        for _ in range(20):
            R = aleatoria(rng); th = self.spm.ik(R)
            om = rng.normal(size=3)
            td = self.spm.thetadot_desde_omega(R, th, om)
            R2 = exp_so3(om * h) @ R
            td_num = (self.spm.ik(R2, th) - th) / h
            self.assertLess(np.abs(td - td_num).max(), 1e-4)
            self.assertLess(np.linalg.norm(self.spm.omega_desde_thetadot(R, th, td) - om), 1e-8)

    def test_fuera_del_espacio_de_trabajo(self):
        with self.assertRaises(FueraDeEspacioDeTrabajo):
            self.spm.ik(desde_inclinacion(math.radians(80), 0.3))

    def test_regresion_valores_conocidos(self):
        """Valores obtenidos con el script original (cinem.py) que generó la animación contra el CAD."""
        from spm.rot import rotx
        spm_cad = SPM(horizontal=False)                  # el marco original del CAD (pose de ensamblaje = referencia)
        oro = [(12, 40, [-3.234822, 10.503222, -2.99059]), (10, 200, [0.835949, -7.587485, 5.831389]), (8, 310, [6.025603, 0.061, -8.106873])]
        for tilt, az, esperado in oro:             # orden NNR1, NNR2, NNR3; mismos R que usó la animación validada en el CAD
            R = rotz(math.radians(az)) @ rotx(math.radians(tilt)) @ rotz(-math.radians(az))
            self.assertLess(np.abs(np.degrees(spm_cad.ik(R)) - esperado).max(), 1e-5)


class Trayectorias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spm, cls.trans, cls.lim = SPM(), Transmision(), Limites()

    def test_precesion_respeta_limites_de_motor(self):
        tr = planificar(self.spm, self.trans, self.lim, CaminoPrecesion(12, 1), T=0.5)       # demasiado rápida: debe alargarse
        self.assertGreater(tr.factor_tiempo, 1.0)
        self.assertLessEqual(np.abs(tr.motor_vel).max(), self.lim.vel_motor_max * 1.01)
        self.assertLessEqual(np.abs(tr.motor_acc).max(), self.lim.acc_motor_max * 1.05)

    def test_trayectoria_continua_sin_saltos(self):
        tr = planificar(self.spm, self.trans, self.lim, CaminoPrecesion(12, 2), T=8.0)
        self.assertLess(np.abs(np.diff(tr.theta, axis=0)).max(), math.radians(2))

    def test_parte_y_termina_en_reposo(self):
        tr = planificar(self.spm, self.trans, self.lim, CaminoSlerp([np.eye(3), desde_inclinacion(0.2, 1.0, 0.1)]), T=2.0)
        self.assertLess(np.abs(tr.motor_vel[0]).max(), 0.05); self.assertLess(np.abs(tr.motor_vel[-1]).max(), 0.05)

    def test_inclinacion_excesiva_se_marca(self):
        tr = planificar(self.spm, self.trans, self.lim, CaminoPrecesion(34, 1), T=6.0)
        self.assertGreater(len(tr.violaciones), 0)
        with self.assertRaises(FueraDeEspacioDeTrabajo):
            planificar(self.spm, self.trans, self.lim, CaminoPrecesion(34, 1), T=6.0, estricto=True)

    def test_seguimiento_con_motores_simulados(self):
        tr = planificar(self.spm, self.trans, self.lim, CaminoPrecesion(10, 1), T=6.0)
        reg = seguir_trayectoria(self.spm, self.trans, self.lim, tr)
        self.assertLess(reg.error_deg.max(), 1.0)


class ModeloCADyColisiones(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from spm import cargar_config
        cls.spm = SPM(); cls.trans, cls.lim = cargar_config()

    def test_malla_cad_cargada(self):
        from spm.modelo3d import ModeloCAD
        m = ModeloCAD(self.spm)
        self.assertEqual({c for c, *_ in m.partes}, {"fijo", "plataforma", "nnr1", "nnr2", "nnr3", "brazov1", "brazov2", "brazov3"})
        self.assertGreater(sum(len(F) for *_, F, _, _ in m.partes), 20000)

    def test_cuerpos_rigidos_y_cierre_en_la_malla(self):
        """Los puntos de la plataforma deben moverse con R y los del Brazov mantener la distancia al eje a2 transformado."""
        from spm.modelo3d import ModeloCAD
        m = ModeloCAD(self.spm); R = desde_inclinacion(*np.radians([10, 50, 5])); th = self.spm.ik(R)
        n0, n1 = m.nubes(np.eye(3), np.zeros(3)), m.nubes(R, th)
        self.assertLess(np.abs(n1["fijo"] - n0["fijo"]).max(), 1e-9)
        c0, c1 = n0["plataforma"].mean(0), n1["plataforma"].mean(0)
        self.assertLess(np.linalg.norm((c1 - m.O) - R @ (c0 - m.O)), 1e-6)

    def test_mapa_de_colisiones_en_el_marco_horizontal(self):
        """Con el disco horizontal como referencia, el mapa del CAD no marca colisión hasta 24° y la pose inicial tiene holgura de sobra."""
        if self.lim.mapa is None:
            self.skipTest("sin datos/mapa_colision.npz")
        for tl, az in ((0, 0), (12, 60), (20, 200), (24, 330)):
            R = desde_inclinacion(math.radians(tl), math.radians(az)); ok, why = self.lim.valida(self.spm, R, self.spm.ik(R))
            self.assertTrue(ok, "%d° az %d: %s" % (tl, az, why))
        self.assertGreater(self.lim.mapa.consultar(0, 0, 0)[0], 15.0)                         # holgura mínima en la pose inicial (mm)


class RenderGPU(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from spm.render_gl import RenderGL
        cls.spm = SPM(); cls.gl = RenderGL.crear(cls.spm)
        if cls.gl is None:
            raise unittest.SkipTest("sin OpenGL/EGL o sin datos/malla_gpu.npz: %s" % getattr(RenderGL, "ultimo_error", ""))

    def test_imagen_con_contenido_y_fondo(self):
        img = self.gl.renderizar(np.eye(3), np.zeros(3), ancho=320, alto=260)
        self.assertEqual(img.shape, (260, 320, 3)); self.assertEqual(img.dtype, np.uint8)
        fondo = np.array([0x0d, 0x11, 0x17]); self.assertLess(np.abs(img[3, 3] - fondo).max(), 2)           # esquina = fondo
        self.assertGreater((np.abs(img.astype(int) - fondo).sum(-1) > 30).mean(), 0.08)                    # el modelo ocupa parte del cuadro

    def test_la_pose_cambia_la_imagen_y_ocultar_funciona(self):
        R = desde_inclinacion(math.radians(14), math.radians(30)); th = self.spm.ik(R)
        a = self.gl.renderizar(np.eye(3), np.zeros(3), ancho=240, alto=200); b = self.gl.renderizar(R, th, ancho=240, alto=200)
        self.assertGreater(np.abs(a.astype(int) - b).mean(), 0.5)
        c = self.gl.renderizar(np.eye(3), np.zeros(3), ancho=240, alto=200, grupos_ocultos={"fijo"})
        self.assertGreater(np.abs(a.astype(int) - c).mean(), 0.5)

    def test_rapido(self):
        import time
        self.gl.renderizar(np.eye(3), np.zeros(3), ancho=640, alto=480); t = time.time()
        for _ in range(10): self.gl.renderizar(np.eye(3), np.zeros(3), ancho=640, alto=480)
        self.assertLess((time.time() - t) / 10, 0.1)                                                       # <100 ms por fotograma (en la práctica ~8 ms)


class ProgramaCaja(unittest.TestCase):
    def test_cuatro_caras_esperas_y_volcado(self):
        from spm import cargar_config, programa_caja, expandir_esperas, planificar_con_paradas
        spm = SPM(); trans, lim = cargar_config()
        lim.motor_min_deg = lim.motor_max_deg = (None, None, None)                                          # estas pruebas validan colisión/singularidad, no los topes medidos del motor
        filas, tmax = programa_caja(spm, lim)
        self.assertEqual([f[2] for f in filas], [0, 90, 180, 270, 270, 270, 360])                           # 4 caras, volcado, vuelta a nivel, cierre de la vuelta
        self.assertGreater(tmax, 20.0)                                                                       # volcado a una inclinación grande
        Rs = [desde_inclinacion(*np.radians(f[:3])) for f in filas]; Rx, Tx = expandir_esperas(Rs, [f[4] for f in filas], [f[3] for f in filas[1:]])
        tr = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.02)
        self.assertEqual(len(tr.violaciones), 0); self.assertAlmostEqual(tr.duracion, 34.5, 1)
        self.assertLess(np.degrees(np.abs(np.diff(tr.motor, axis=0)).max()), 6.0)                          # sin saltos de vuelta entre tramos
        for t0, tors in ((2.5, 0), (9.5, 90), (16.5, 180)):                                                  # en cada cara el plato está quieto
            k = int(np.searchsorted(tr.t, t0)); self.assertLess(np.abs(tr.motor_vel[k]).max(), 1e-6)
            self.assertAlmostEqual(np.degrees(tr.theta[k][0]), tors, 3)                                      # los NNR acumulan 90° por cara (multi-vuelta)
        self.assertAlmostEqual(np.degrees(tr.theta[-1][0]), 360.0, 3)
        self.assertGreater(np.degrees(tr.inclinacion).max(), tmax - 0.6)

    def test_cajas_reducidas_sin_violaciones_y_con_menos_recorrido(self):
        from spm import cargar_config, programa_caja_reducida, expandir_esperas, planificar_con_paradas
        spm = SPM(); trans, lim = cargar_config()
        lim.motor_min_deg = lim.motor_max_deg = (None, None, None)                                          # estas pruebas validan colisión/singularidad, no los topes medidos del motor
        for paso, incl in ((5.0, 4.0), (10.0, 6.0), (15.0, 8.0), (30.0, 10.0)):
            filas, tilt = programa_caja_reducida(spm, lim, paso, incl)
            self.assertEqual([f[2] for f in filas], [0, paso, 2 * paso, 3 * paso, 3 * paso, 3 * paso, 0])
            self.assertGreater(tilt, 0.0); self.assertLessEqual(tilt, incl)
            Rs = [desde_inclinacion(*np.radians(f[:3])) for f in filas]; Rx, Tx = expandir_esperas(Rs, [f[4] for f in filas], [f[3] for f in filas[1:]])
            tr = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.02)
            self.assertEqual(len(tr.violaciones), 0)
            th = np.degrees(tr.theta[:, 0]); self.assertGreaterEqual(th.max(), 3 * paso - 1e-3); self.assertLess(th.max(), 3 * paso + 12.0)   # el NNR llega a 3·paso (más el volcado) y vuelve
            self.assertAlmostEqual(th[-1], 0.0, 3)


class MapaDeterminista(unittest.TestCase):
    def test_consulta_estable_ante_ruido_de_flotante(self):
        from spm import cargar_config
        _, lim = cargar_config()
        if lim.mapa is None: self.skipTest("sin mapa")
        a = lim.mapa.consultar(24.0, 30.0, -90.0)[0]; b = lim.mapa.consultar(24.0 + 1e-12, 30.0 - 1e-12, -90.0 - 1e-12)[0]
        self.assertAlmostEqual(a, b, 6)
        self.assertTrue(math.isnan(lim.mapa.consultar(30.0, 0.0, 200.0)[0]))                                # por simetría no se extrapola por encima de 26°


class Transmisiones(unittest.TestCase):
    def test_relacion_y_signo(self):
        t = Transmision()
        self.assertAlmostEqual(t.relacion, 37 / 17)
        th = np.array([0.1, -0.2, 0.3])
        self.assertLess(np.abs(t.a_nnr(t.a_motor(th)) - th).max(), 1e-12)
        self.assertLess(float(t.a_motor(0.1)[0]), 0)        # sentido contrario al del NNR

    def test_topes_de_motor(self):
        lim = Limites(motor_min_deg=(-85.0, None, None), motor_margen_deg=5.0)
        self.assertTrue(lim.valida_motor(np.radians([-79.0, 500.0, -500.0]))[0])      # solo el motor 1 tiene tope y el lado negativo
        ok, why = lim.valida_motor(np.radians([-81.0, 0.0, 0.0]))
        self.assertFalse(ok); self.assertIn("motor 1", why)
        self.assertTrue(Limites().valida_motor(np.radians([-900.0, 900.0, 900.0]))[0])  # sin topes definidos no restringe

    def test_signo_por_pata(self):
        base = Transmision()
        inv = Transmision(signos=(1, -1, -1))                # motor 1 invertido respecto al valor por defecto
        th = np.array([0.1, -0.2, 0.3])
        a, b = base.a_motor(th), inv.a_motor(th)
        self.assertAlmostEqual(float(b[0]), -float(a[0]), 12)
        self.assertAlmostEqual(float(b[1]), float(a[1]), 12)
        self.assertAlmostEqual(float(b[2]), float(a[2]), 12)
        self.assertLess(np.abs(inv.a_nnr(inv.a_motor(th)) - th).max(), 1e-12)
        trayectoria = np.random.default_rng(0).normal(size=(5, 3))   # (N, 3): el signo actúa sobre la última dimensión
        self.assertLess(np.abs(inv.a_nnr(inv.a_motor(trayectoria)) - trayectoria).max(), 1e-12)
        with self.assertRaises(ValueError):
            Transmision(signos=(1, 2, -1)).a_motor(th)


if __name__ == "__main__":
    unittest.main()


class OscilacionMotores(unittest.TestCase):
    def test_un_motor_a_la_vez_con_los_otros_en_cero(self):
        from spm import cargar_config, programa_oscilacion_motores, planificar_articular
        spm = SPM(); trans, lim = cargar_config()
        filas = programa_oscilacion_motores(spm, trans, 70.0, 3.0, (0, 1, 2))
        self.assertEqual(len(filas), 1 + 4 * 3)                                                              # punto inicial + 4 tramos por motor
        Rs = [desde_inclinacion(*np.radians(f[:3])) for f in filas]
        tr = planificar_articular(spm, trans, lim, Rs, [f[3] for f in filas[1:]], 0.02, ajustar_tiempo=False)
        m = np.degrees(tr.motor)
        self.assertAlmostEqual(m.max(), 70.0, 1); self.assertAlmostEqual(m.min(), -70.0, 1)
        self.assertEqual(len(tr.violaciones), 0)                                                              # ±70° queda dentro de ±84° con margen de 5°
        for j, (a, b) in enumerate(((0, 4), (4, 8), (8, 12))):                                               # tramos de cada motor: los otros dos no se mueven
            k0 = int(np.searchsorted(tr.t, 3.0 * a)); k1 = int(np.searchsorted(tr.t, 3.0 * b)) + 1
            otros = [i for i in range(3) if i != j]
            self.assertLess(np.abs(m[k0:k1, otros]).max(), 0.05)
            self.assertAlmostEqual(np.abs(m[k0:k1, j]).max(), 70.0, 1)
        self.assertLess(np.abs(m[-1]).max(), 0.05)                                                            # vuelve al origen
