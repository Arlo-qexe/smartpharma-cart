"""Pruebas de las trayectorias SmartPharma: geometría (orden de slots, poses de cámara), validez de todos los planes (sin colisión, límites, home al inicio/fin,
continuidad entre tramos), reproducibilidad de la cabecera del firmware, interfaz (ejemplos y paso a paso) y el firmware real en el PC (canal UART con checksum)."""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ.parent.parent / "shared"))

from spm import SPM, cargar_config                                   # noqa: E402
from spm import smartpharma as sp                                    # noqa: E402

try:
    import tkinter as tk
    _r = tk.Tk(); _r.destroy(); HAY_TK = True
except Exception:
    HAY_TK = False

_GEN = {}


def generador():
    if "g" not in _GEN:
        spm = SPM(); trans, lim = cargar_config()
        _GEN["g"] = sp.Generador(spm, trans, lim)
    return _GEN["g"]


class Geometria(unittest.TestCase):
    def test_orden_de_slots_izquierda_a_derecha(self):
        cfg = sp.cargar_config(); o = sp.orden_slots(cfg)
        self.assertEqual([s["tipo"] for s in o], ["TIPO_A", "TIPO_B", "TIPO_C"])
        self.assertEqual([s["slot"] for s in o], ["slot_3", "slot_2", "slot_1"])            # la numeración del CAD está invertida respecto a la de TIPO
        for s, az in zip(o, (-94.51, -178.64, 115.61)): self.assertAlmostEqual(s["az_deg"], az, 1)
        self.assertGreater(o[0]["ang_vista_deg"], o[1]["ang_vista_deg"]); self.assertGreater(o[1]["ang_vista_deg"], o[2]["ang_vista_deg"])
        self.assertAlmostEqual(o[0]["ang_vista_deg"], 98.2, 0)                               # TIPO_A a la izquierda de la dirección de movimiento

    def test_offset_de_la_base_mueve_los_slots_pero_no_el_orden(self):
        cfg = sp.cargar_config(); cfg["entorno"]["offset_azimut_base_deg"] = 30.0
        o = sp.orden_slots(cfg)
        self.assertEqual([s["tipo"] for s in o], ["TIPO_A", "TIPO_B", "TIPO_C"]); self.assertAlmostEqual(o[0]["az_deg"], -64.51, 1)
        t0 = [c["torsion_deg"] for c in sp.torsiones_camara(sp.cargar_config())]; t1 = [c["torsion_deg"] for c in sp.torsiones_camara(cfg)]
        np.testing.assert_allclose(t0, t1, atol=1e-9)                                          # un giro global de todo el entorno no cambia las torsiones de cámara

    def test_torsiones_de_camara(self):
        t = [c["torsion_deg"] for c in sp.torsiones_camara(sp.cargar_config())]
        np.testing.assert_allclose(t, [-7.02, -97.02, -187.02, -277.02], atol=0.05)
        self.assertTrue(all(x < 0 for x in t))                                                 # solo sentido negativo: el tope del motor 1 es -85°
        np.testing.assert_allclose(np.diff(t), -90.0, atol=1e-6)


class PlanesValidos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = generador(); cls.g.todos()

    def test_poses_de_camara_libres_y_lejos_de_singularidades(self):
        g = self.g
        self.assertEqual(len(g.poses_cam), 5)
        np.testing.assert_allclose(g.poses_cam[0].theta, 0, atol=1e-9)                         # home = motores a 0
        for p in g.poses_cam:
            ok, why = g.lim.valida(g.spm, p.R, p.theta); self.assertTrue(ok, "%s: %s" % (p.nombre, why))
            self.assertGreaterEqual(g.lim.mapa.consultar(p.incl, p.az, p.tors)[0], 1.5)
            s, b = g.spm.indice_singularidad(p.R, p.theta); self.assertGreater(s, 0.3); self.assertGreater(b, 0.3)

    def test_todos_los_planes_sin_violaciones_y_holgura(self):
        for nombre, pl in self.g.planes.items():
            for t in pl.tramos:
                self.assertEqual(t.metricas.get("violaciones", 0), 0, "%s/%s: %s" % (nombre, t.nombre, t.metricas.get("primera_violacion")))
                self.assertGreaterEqual(t.metricas["holgura_min_mm"], 1.5, "%s/%s" % (nombre, t.nombre))

    def test_continuidad_y_home_al_inicio_y_al_fin(self):
        g = self.g
        for nombre, pl in g.planes.items():
            for a, b in zip(pl.tramos[:-1], pl.tramos[1:]):
                np.testing.assert_allclose(a.q1, b.q0, atol=1e-6, err_msg="%s: %s -> %s" % (nombre, a.nombre, b.nombre))
            if nombre.startswith(("CLAS", "DESC2")):
                np.testing.assert_allclose(pl.tramos[-1].q1, 0, atol=1e-6, err_msg=nombre)     # termina en home (motores a 0)
        np.testing.assert_allclose(g.planes["CAM_1"].tramos[0].q0, 0, atol=1e-6)                 # la cámara parte de home
        for k in range(1, 4):
            np.testing.assert_allclose(g.planes["CAM_%d" % k].tramos[0].q1, g.planes["CAM_%d" % (k + 1)].tramos[0].q0, atol=1e-6)   # un tramo empieza donde acabó el anterior
        for tipo in ("TIPO_A", "TIPO_B", "TIPO_C"):
            np.testing.assert_allclose(g.planes["CLAS_%s_E4" % tipo].tramos[0].q0, g.planes["CAM_4"].tramos[0].q1, atol=1e-6)   # clasificar parte de la última cara
        for e in range(1, 5):
            np.testing.assert_allclose(g.planes["DESC2_E%d" % e].tramos[0].q0, g.poses_cam[e].theta * 0 + np.degrees(g.trans.a_motor(g.poses_cam[e].theta)), atol=1e-6)

    def test_tiempos_y_limites_de_motor(self):
        g = self.g; t = g.cfg["tiempos"]
        for k in range(1, 5):
            tr = g.planes["CAM_%d" % k].tramos[0]
            self.assertAlmostEqual(tr.t_mov, t["t_mov_s"], 2); self.assertAlmostEqual(tr.t_hold, t["t_estab_s"], 2)      # 2,5 s + 0,5 s = 3 s por cara
        for nombre, pl in g.planes.items():
            for tr in pl.tramos:
                self.assertGreaterEqual(tr.t_mov + 1e-9, t["t_mov_s"] if tr.t_mov > 0 else 0)
                self.assertLessEqual(tr.metricas.get("vel_pico_dps", 0), g.cfg["limites"]["vel_motor_max_dps"] * 1.03, "%s/%s" % (nombre, tr.nombre))
                q = np.vstack([tr.q0, tr.q1]); self.assertGreater(q[:, 0].min(), -80.0 + 1e-6, "tope del motor 1 (-85° con 5° de margen)")

    def test_volcado_a_la_inclinacion_pedida_con_holgura(self):
        g = self.g; n = 4
        for s in g.slots:
            V = g.planes["CLAS_%s_E%d" % (s["tipo"], n)].tramos[1].b
            self.assertAlmostEqual(V.incl, g.cfg["clasificacion"]["inclinacion_volcado_deg"], 6)
            self.assertAlmostEqual(V.az, s["az_deg"], 6)
            self.assertGreaterEqual(g.lim.mapa.consultar(V.incl, V.az, V.tors)[0], 5.5)         # holgura de volcado >= 5,5 mm

    def test_descarte(self):
        g = self.g
        self.assertEqual(g.cfg["descarte"]["variante_por_defecto"], 1)
        self.assertIs(g.tipo_destino("DESCARTE"), g.planes["CLAS_TIPO_A_E4"])                  # DESCARTE_1 = TIPO_A
        self.assertIs(g.tipo_destino("DESCARTE_1"), g.planes["CLAS_TIPO_A_E4"])
        d2 = g.tipo_destino("DESCARTE", 2); self.assertEqual(d2.tramos[0].t_hold, 60.0); self.assertEqual(d2.tramos[0].t_mov, 0.0)
        self.assertGreater(d2.duracion, 60.0)

    def test_ciclo_completo(self):
        pl = self.g.ciclo_completo("TIPO_B"); self.assertEqual(len(pl.tramos), 4 + 4)
        filas, th = sp.programa_interfaz(self.g, pl)
        self.assertEqual(len(th), len(filas) + sum(1 for f in filas if f[4] > 0))


class Reproducibilidad(unittest.TestCase):
    def test_la_cabecera_del_firmware_es_la_que_genera_el_generador(self):
        from spm.smartpharma_salida import generar
        with tempfile.TemporaryDirectory() as d:
            generar(Path(d) / "s", Path(d) / "h.h", graficas=False, verbose=False)
            self.assertEqual((Path(d) / "h.h").read_text(encoding="utf-8"), (RAIZ / "firmware" / "spm_esp32s3" / "spm_trayectorias.h").read_text(encoding="utf-8"),
                             "spm_trayectorias.h no está al día: python herramientas/generar_trayectorias_smartpharma.py")
            j = json.loads((Path(d) / "s" / "trayectorias.json").read_text(encoding="utf-8"))
            self.assertEqual(j["slots_izquierda_a_derecha"][0]["tipo"], "TIPO_A"); self.assertIn("CLAS_TIPO_C_E4", j["planes"])
            self.assertTrue((Path(d) / "s" / "puntos_de_paso.csv").exists())
            self.assertEqual(j["planes"]["CAM_1"]["tramos"][0]["q1_pulsos"][0], round(j["planes"]["CAM_1"]["tramos"][0]["q1_deg"][0] / 360 * 4532))


@unittest.skipUnless(HAY_TK, "sin Tk/pantalla")
class InterfazSmartPharma(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from interfaz.app import AppSPM
        cls.app = AppSPM(); cls.app.withdraw(); cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_ejemplos_cargan_planifican_y_respetan_los_tiempos(self):
        from interfaz.pestana_trayectoria import SMARTPHARMA
        t = self.app.pestanas["trayectoria"]
        self.assertIn("SmartPharma · Cámara: cara 1 (home→1)", SMARTPHARMA); self.assertIn("SmartPharma · DESCARTE_2 (espera y home)", SMARTPHARMA)
        for nombre in ("SmartPharma · Cámara: cara 1 (home→1)", "SmartPharma · Cámara: cara 4 (3→4)", "SmartPharma · Clasificar TIPO_C (desde la cara 4)",
                       "SmartPharma · DESCARTE_1 (= TIPO_A)", "SmartPharma · Ciclo completo: 4 caras + TIPO_B"):
            t.cb_preset.set(nombre); t._cargar_preset(); tr = t.planificar(); self.assertIsNotNone(tr, nombre)
            self.assertEqual(len(tr.violaciones), 0, nombre)
            if "Clasificar" in nombre or "DESCARTE_1" in nombre:
                self.assertAlmostEqual(np.degrees(tr.motor[0])[0], 602.926, 1, nombre)             # parte de la cara 4 (motores acumulados), no de la rama equivalente
            if "Clasificar" in nombre or "DESCARTE_1" in nombre or "Ciclo" in nombre:
                np.testing.assert_allclose(np.degrees(tr.motor[-1]), 0, atol=0.05, err_msg=nombre)   # termina en home
        t.cb_preset.set("SmartPharma · Cámara: cara 2 (1→2)"); t._cargar_preset(); tr = t.planificar(); self.assertAlmostEqual(tr.duracion, 3.0, 1)

    def test_camara_paso_a_paso_transmite_un_tramo_por_clic(self):
        t = self.app.pestanas["trayectoria"]
        t.cb_preset.set("SmartPharma · Cámara: 4 caras paso a paso (botón «Siguiente paso»)"); t._cargar_preset(); tr = t.planificar()
        c = t._cortes(); self.assertEqual(len(c), 5); np.testing.assert_allclose(np.diff(c), 3.0, atol=0.02)
        enviados = []
        class SerialFalso:
            def transmitir(self_, sub): enviados.append(sub)
        original = self.app.pestanas["serial"]; self.app.pestanas["serial"] = SerialFalso()
        try:
            for k in range(4): t.siguiente_paso()
            t.siguiente_paso()
        finally:
            self.app.pestanas["serial"] = original
        self.assertEqual(len(enviados), 4)                                                        # un clic = un tramo; el quinto no envía nada
        for k, sub in enumerate(enviados):
            self.assertAlmostEqual(sub.duracion, 3.0, 1)
            np.testing.assert_allclose(np.degrees(sub.motor[0]), sp_pose(k), atol=0.3); np.testing.assert_allclose(np.degrees(sub.motor[-1]), sp_pose(k + 1), atol=0.3)


def sp_pose(e):
    g = generador(); return np.degrees(g.trans.a_motor(g.poses_cam[e].theta))


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("g++"), "necesita pty (Linux) y g++")
class FirmwareRealEnElPC(unittest.TestCase):
    """El firmware real (spm_esp32s3.ino con planta simulada) con el canal UART de la Orange Pi, hablado con las funciones de shared/protocol_constants.py."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.exe = os.path.join(cls.tmp, "esp32_host")
        d = RAIZ / "firmware" / "test_host"
        r = subprocess.run(["g++", "-std=c++17", "-O2", "-DHOST_TEST", "-I", str(d), str(d / "esp32_host.cpp"), "-o", cls.exe], capture_output=True, text=True)
        if r.returncode: raise unittest.SkipTest("no compila: " + r.stderr[-300:])

    def _abrir(self, ppr="4532,4532,4532"):
        emu = subprocess.Popen([self.exe, "--ppr", ppr], stdout=subprocess.PIPE, text=True)
        usb = emu.stdout.readline().split("PUERTO:")[1].strip(); uart = emu.stdout.readline().split("PUERTO_UART:")[1].strip()
        import serial
        return emu, serial.Serial(usb, 115200, timeout=0.05), serial.Serial(uart, 115200, timeout=0.05)

    @staticmethod
    def _leer(ser, buf, t):
        fin = time.time() + t; out = []
        while time.time() < fin:
            buf[0] += ser.read(512)
            while b"\n" in buf[0]:
                l, buf[0] = buf[0].split(b"\n", 1); out.append(l.decode(errors="replace").strip())
        return out

    def test_orange_pi_camara_cuatro_giros_y_clasificar(self):
        import protocol_constants as pc
        emu, usb, uart = self._abrir()
        try:
            bu, bd = [b""], [b""]
            def esperar_evento(nombre, t=6.0, **campos):
                fin = time.time() + t
                while time.time() < fin:
                    for l in self._leer(uart, bd, 0.05):
                        m = pc.desempaquetar_mensaje_uart(l)
                        self.assertIsNotNone(m, "checksum inválido en lo que envía la ESP32: %r" % l)
                        if m.get("evento") == nombre and all(m.get(k) == v for k, v in campos.items()): return m
                return None
            time.sleep(0.3)
            for cara in range(1, 5):
                t0 = time.time(); uart.write(pc.empaquetar_mensaje_uart({"accion": pc.ACCION_GIRAR_POSICION, "cara": cara}))
                m = esperar_evento(pc.EVENTO_EN_POSICION, cara=cara); dt = time.time() - t0
                self.assertIsNotNone(m, "sin en_posicion de la cara %d" % cara); self.assertLess(dt, 4.5)       # cabe en el plazo de 5 s de la Orange Pi
                self.assertGreater(dt, 2.7)
            uart.write(b'{"accion": "girar_posicion", "cara": 1}|00\n'); self.assertIsNone(esperar_evento(pc.EVENTO_EN_POSICION, 1.0), "checksum malo: no debe actuar")
            uart.write(pc.empaquetar_mensaje_uart({"accion": pc.ACCION_CLASIFICAR, "destino": "TIPO_B"})); time.sleep(0.5)
            usb.write(b'{"cmd":"status"}\n'); st = [l for l in self._leer(usb, bu, 1.5) if "\"traj\"" in l]
            self.assertTrue(any("\"traj\":1" in l for l in st), st)                                       # clasificando
            fin = time.time() + 16; est = None
            while time.time() < fin:
                usb.write(b'{"cmd":"status"}\n')
                for l in self._leer(usb, bu, 0.5):
                    if "\"pose\"" in l: est = l
                if est and "\"traj\":0" in est: break
            self.assertIn("\"pose\":0", est); self.assertIn("\"traj\":0", est)                            # terminó en home
        finally:
            usb.close(); uart.close(); emu.kill(); emu.wait(); emu.stdout.close()

    @unittest.skipUnless(HAY_TK, "sin Tk/pantalla")
    def test_interfaz_cara_a_cara_contra_el_firmware(self):
        from interfaz.app import AppSPM
        emu = subprocess.Popen([self.exe, "--ppr", "4532,4532,4532"], stdout=subprocess.PIPE, text=True)
        try:
            puerto = emu.stdout.readline().split("PUERTO:")[1].strip()
            app = AppSPM(); app.withdraw(); s = app.pestanas["serial"]
            def bombear(t):
                t0 = time.time()
                while time.time() - t0 < t: app.update(); time.sleep(0.02)
            s.puerto.set(puerto); s.alternar(); bombear(2.0)
            tray = app.pestanas["trayectoria"]
            tray.cb_preset.set("SmartPharma · Cámara: 4 caras paso a paso (botón «Siguiente paso»)"); tray._cargar_preset()
            tray.siguiente_paso(); bombear(4.0)                                                           # streaming del tramo 1 al firmware
            self.assertLess(max(abs(s.tel_pos[j][-1] - sp_pose(1)[j]) for j in range(3)), 1.0)
            s._json({"cmd": "cam", "cara": 2}); bombear(4.0)                                              # tabla del firmware (botón «Cara 2»)
            self.assertLess(max(abs(s.tel_pos[j][-1] - sp_pose(2)[j]) for j in range(3)), 1.5)
            self.assertIn("traj_done", s.log.get("1.0", "end"))
            s._desconectar(); app.destroy()
        finally:
            emu.kill(); emu.wait(); emu.stdout.close()


@unittest.skipUnless(sys.platform.startswith("linux"), "necesita pty (Linux)")
class Esp32SimuladoPython(unittest.TestCase):
    def test_cam_y_clas_con_el_esp32_simulado(self):
        import serial
        d = RAIZ / "trayectorias_smartpharma" / "trayectorias.json"
        if not d.exists(): self.skipTest("falta trayectorias_smartpharma/trayectorias.json (generar primero)")
        emu = subprocess.Popen([sys.executable, str(RAIZ / "herramientas" / "esp32_simulado.py"), "--ppr", "4532,4532,4532", "--tau", "0.02", "--vmax", "720"], stdout=subprocess.PIPE, text=True)
        try:
            ser = serial.Serial(emu.stdout.readline().split("PUERTO:")[1].strip(), 115200, timeout=0.1)
            def hasta(sub, t):
                fin = time.time() + t; buf = ""
                while time.time() < fin:
                    buf += ser.read(512).decode(errors="replace")
                    if sub in buf: return buf
                return None
            ser.write(b'{"cmd":"cam","cara":2}\n'); self.assertIn("fuera_de_secuencia", hasta("fuera_de_secuencia", 2) or "")
            ser.write(b'{"cmd":"cam","cara":1}\n'); self.assertIsNotNone(hasta("traj_done", 5))
            ser.write(b'{"cmd":"cam","cara":5}\n'); self.assertIn("cara_no_implementada", hasta("cara_no_implementada", 2) or "")
        finally:
            emu.kill(); emu.wait(); emu.stdout.close()


if __name__ == "__main__":
    unittest.main()
