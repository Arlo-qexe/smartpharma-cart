"""Pruebas de la interfaz (necesitan una pantalla; se omiten si Tk no está disponible). La ventana permanece oculta."""
import math
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

import numpy as np
from spm import desde_inclinacion

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

try:
    import tkinter as tk
    _r = tk.Tk(); _r.destroy(); HAY_TK = True
except Exception:
    HAY_TK = False


@unittest.skipUnless(HAY_TK, "sin Tk/pantalla")
class Interfaz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from interfaz.app import AppSPM
        cls.app = AppSPM(); cls.app.withdraw(); cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_ik_desde_deslizadores_y_fk_inversa(self):
        from spm.rot import angulo_entre
        from spm import desde_inclinacion
        p = self.app.pestanas["pose"]; self.app.ir_a("pose")
        for k, v in (("tilt", 10.0), ("az", 60.0), ("tor", 5.0)): p.sl[k][0].set(v)
        p._on_orient()
        R = desde_inclinacion(*np.radians([10, 60, 5]))
        self.assertLess(math.degrees(angulo_entre(self.app.R, R)), 1e-6)
        m = np.degrees(self.app.trans.a_motor(self.app.theta))
        p.modo.set("fk")
        for j in range(3): p.sm[j][0].set(m[j])
        p._on_motor()
        self.assertLess(math.degrees(angulo_entre(self.app.R, R)), 1e-4)
        p.modo.set("ik"); p.reset()
        self.assertLess(np.abs(self.app.theta).max(), 1e-9)

    def test_deslizadores_con_valores_negativos_y_torsion_amplia(self):
        p = self.app.pestanas["pose"]; self.app.ir_a("pose")
        for tilt, az, tor in ((-20, 100, 0), (25, -200, 10), (-30, -360, -45), (0, 0, 90)):
            for k, v in (("tilt", tilt), ("az", az), ("tor", tor)): p.sl[k][0].set(v)
            p._on_orient()
            self.assertEqual((p.sl["tilt"][0].get(), p.sl["az"][0].get(), p.sl["tor"][0].get()), (tilt, az, tor))          # el deslizador conserva el valor negativo
            R = desde_inclinacion(*np.radians([tilt, az, tor]))
            from spm.rot import angulo_entre
            self.assertLess(math.degrees(angulo_entre(self.app.R, R)), 1e-6)
        self.assertLess(np.abs(np.degrees(self.app.theta) - 90).max(), 1e-6)                                            # torsión pura 90°: los tres NNR a 90°, sin saltar de rama
        p.reset()

    def test_planificar_los_tres_modos_y_simular_control(self):
        t = self.app.pestanas["trayectoria"]; c = self.app.pestanas["control"]
        t.wps = [[0, 0, 0, 1], [8, 0, 0, 1.0], [8, 120, 5, 1.0], [0, 0, 0, 1.0]]; t._llenar_tabla()
        for modo in ("slerp", "art", "jac"):
            t.modo.set(modo); tr = t.planificar()
            self.assertIsNotNone(tr, modo); self.assertEqual(len(tr.violaciones), 0, modo)
            self.assertLess(np.abs(np.degrees(tr.motor_vel)).max() / 6, 60.5)
        c.simular(); self.assertIn("error de orientación", c.res.cget("text"))
        t._mostrar_k(len(t.tray.t) // 2)

    def test_preset_de_la_caja(self):
        from interfaz.pestana_trayectoria import CAJA
        t = self.app.pestanas["trayectoria"]; t.cb_preset.set(CAJA); t._cargar_preset()
        self.assertEqual(len(t.wps), 7); self.assertEqual([w[4] for w in t.wps[:4]], [5.0] * 4)
        tr = t.planificar(); self.assertIsNotNone(tr); self.assertEqual(len(tr.violaciones), 0); self.assertAlmostEqual(tr.duracion, 34.5, 1)

    def test_trayectoria_fuera_de_limites_se_marca(self):
        t = self.app.pestanas["trayectoria"]
        t.wps = [[0, 0, 0, 1], [20, 45, 0, 2.0], [0, 0, 0, 2.0]]; t._llenar_tabla(); t.modo.set("slerp")
        self.assertEqual(len(t.planificar().violaciones), 0)                                   # desde el plato horizontal 20° es seguro según el CAD
        self.app.lim.holgura_min_mm = 15.0                                                      # exigir 15 mm de holgura (a 20° hay ~13 mm): ahora se marca
        try:
            self.assertGreater(len(t.planificar().violaciones), 0)
        finally:
            self.app.lim.holgura_min_mm = 1.5

    def test_mapa_espacio_y_clic(self):
        e = self.app.pestanas["espacio"]; e.calcular(); self.assertIn("permitido", e.info.cget("text"))

    def test_csv_ida_y_vuelta(self):
        from spm import a_csv, desde_csv
        t = self.app.pestanas["trayectoria"]; t.wps = [[0, 0, 0, 1], [8, 30, 0, 1.0]]; t._llenar_tabla(); t.modo.set("slerp"); tr = t.planificar()
        f = RAIZ / "salida" / "_test.csv"; a_csv(tr, f); tr2 = desde_csv(self.app.spm, self.app.trans, self.app.lim, f); f.unlink()
        self.assertEqual(len(tr.t), len(tr2.t)); self.assertLess(np.abs(tr.motor - tr2.motor).max(), math.radians(0.01))


@unittest.skipUnless(HAY_TK and sys.platform.startswith("linux"), "necesita Tk y pty (Linux)")
class SerialContraEsp32Simulado(unittest.TestCase):
    def test_conexion_consigna_telemetria_y_bloqueo(self):
        from interfaz.app import AppSPM
        from spm import desde_inclinacion
        from spm.rot import angulo_entre
        from spm import cargar_config
        cpr = cargar_config()[0].cuentas_por_vuelta_salida                                   # el emulador ignora {"cfg":"ppr"}: arrancarlo con los mismos pulsos/vuelta que la interfaz
        emu = subprocess.Popen([sys.executable, str(RAIZ / "herramientas" / "esp32_simulado.py"), "--ppr", ",".join(["%g" % cpr] * 3)], stdout=subprocess.PIPE, text=True)
        try:
            puerto = emu.stdout.readline().split("PUERTO:")[1].strip()
            app = AppSPM(); app.withdraw(); s = app.pestanas["serial"]
            def bombear(t):
                t0 = time.time()
                while time.time() - t0 < t: app.update(); time.sleep(0.02)
            s.puerto.set(puerto); s.alternar(); bombear(0.6); self.assertIsNotNone(s.ser); self.assertGreater(len(s.tel_t), 5)
            R = desde_inclinacion(*np.radians([10, 60, 5])); th = app.spm.ik(R); app.fijar_pose(R, th)
            s.enviar_pose(app.trans.a_motor(th)); bombear(2.5)
            self.assertLess(max(abs(s.tel_pos[j][-1] - np.degrees(app.trans.a_motor(th))[j]) for j in range(3)), 0.5)
            self.assertLess(math.degrees(angulo_entre(app.R, R)), 0.5)                      # el espejo sigue al robot
            app.lim.holgura_min_mm = 15.0                                                    # con 15 mm exigidos, 22° (≈11 mm de holgura) queda bloqueado
            Rb = desde_inclinacion(*np.radians([22, 60, 0])); n = len(s.tel_t)
            s.enviar_pose(app.trans.a_motor(app.spm.ik(Rb))); bombear(0.4)
            self.assertIn("bloqueado", s.log.get("1.0", "end"))                              # fuera de límites: no se envía
            s._desconectar(); app.destroy()
        finally:
            emu.kill(); emu.wait(); emu.stdout.close()


@unittest.skipUnless(HAY_TK and sys.platform.startswith("linux") and shutil.which("g++"), "necesita Tk, pty (Linux) y g++")
class InterfazContraElFirmwareReal(unittest.TestCase):
    """La interfaz conectada al MISMO código de control/JSON/telemetría del firmware (spm_esp32s3.ino) ejecutándose en el PC con una planta simulada."""
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.mkdtemp(); cls.exe = os.path.join(cls.tmp, "esp32_host")
        d = RAIZ / "firmware" / "test_host"
        r = subprocess.run(["g++", "-std=c++17", "-O2", "-DHOST_TEST", "-I", str(d), str(d / "esp32_host.cpp"), "-o", cls.exe], capture_output=True, text=True)
        if r.returncode: raise unittest.SkipTest("no compila: " + r.stderr[-300:])

    def test_conexion_ppr_consigna_streaming_bloqueo_y_reset(self):
        from interfaz.app import AppSPM
        from spm import desde_inclinacion
        from spm.rot import angulo_entre
        emu = subprocess.Popen([self.exe, "--ppr", "1000,1000,1000"], stdout=subprocess.PIPE, text=True)
        try:
            puerto = emu.stdout.readline().split("PUERTO:")[1].strip()
            app = AppSPM(); app.withdraw(); s = app.pestanas["serial"]
            def bombear(t):
                t0 = time.time()
                while time.time() - t0 < t: app.update(); time.sleep(0.02)
            s.puerto.set(puerto); s.alternar(); bombear(2.0)
            self.assertIsNotNone(s.ser); self.assertGreater(len(s.tel_t), 10)
            self.assertIn("ack", s.log.get("1.0", "end"))                                  # la interfaz sincronizó pulsos/vuelta y el firmware confirmó
            R = desde_inclinacion(*np.radians([10, 60, 5])); th = app.spm.ik(R); app.fijar_pose(R, th)
            m = np.degrees(app.trans.a_motor(th)); s.enviar_pose(app.trans.a_motor(th)); bombear(2.5)
            self.assertLess(max(abs(s.tel_pos[j][-1] - m[j]) for j in range(3)), 0.6)       # el firmware llegó a la consigna (telemetría en pulsos → °)
            self.assertLess(math.degrees(angulo_entre(app.R, R)), 0.6)                     # espejo: el CAD sigue al «robot»
            tr = app.pestanas["trayectoria"]; tr.wps = [[0, 0, 0, 1], [8, 0, 0, 1.0], [8, 120, 0, 1.0], [0, 0, 0, 1.0]]; tr._llenar_tabla()
            trj = tr.planificar(); s.transmitir(trj); bombear(trj.duracion + 1.5)
            self.assertLess(max(abs(s.tel_pos[j][-1] - np.degrees(trj.motor[-1][j])) for j in range(3)), 1.0)
            app.lim.holgura_min_mm = 15.0; Rb = desde_inclinacion(*np.radians([22, 60, 0]))
            s.enviar_pose(app.trans.a_motor(app.spm.ik(Rb))); bombear(0.3)
            self.assertIn("bloqueado", s.log.get("1.0", "end"))
            s._json({"cmd": "status"}); bombear(0.3); self.assertIn("\"st\":\"run\"", s.log.get("1.0", "end"))
            s._desconectar(); app.destroy()
        finally:
            emu.kill(); emu.wait(); emu.stdout.close()


if __name__ == "__main__":
    unittest.main()
