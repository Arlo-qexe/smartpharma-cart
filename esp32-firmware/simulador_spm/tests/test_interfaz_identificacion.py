"""Pruebas de la interfaz de diagnóstico/identificación (herramientas/interfaz_identificacion.py) con la placa simulada. Ventana oculta; sin hardware."""
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "herramientas"))
import interfaz_identificacion as ii  # noqa: E402


def bombear(root, hasta, tope_s=40.0):
    fin = time.time() + tope_s
    while time.time() < fin:
        root.update()
        if hasta():
            return True
        time.sleep(0.01)
    return False


class TestEvaluarCadena(unittest.TestCase):
    def test_ok(self):
        n, t, _ = ii.evaluar_cadena({"+": (2000, 500), "-": (-1900, -500)}, 0.5)
        self.assertEqual((n, "funciona" in t), ("ok", True))

    def test_sin_giro_es_el_caso_del_usuario(self):
        n, t, lineas = ii.evaluar_cadena({"+": (0, 500), "-": (1, -500)}, 0.5)
        self.assertEqual(n, "error")
        self.assertIn("NO gira", t)
        self.assertTrue(any("STBY" in x for x in lineas) and any("AIN1" in x for x in lineas))

    def test_un_solo_sentido(self):
        n, t, l = ii.evaluar_cadena({"+": (1500, 500), "-": (2, -500)}, 0.5)
        self.assertEqual((n, "positivo" in t, any("AIN2" in x for x in l)), ("aviso", True, True))
        n, t, l = ii.evaluar_cadena({"+": (1, 500), "-": (-1500, -500)}, 0.5)
        self.assertEqual((n, "negativo" in t, any("AIN1" in x for x in l)), ("aviso", True, True))

    def test_encoder_invertido(self):
        n, t, _ = ii.evaluar_cadena({"+": (-2000, 500), "-": (1900, -500)}, 0.5)
        self.assertEqual((n, "al revés" in t), ("aviso", True))

    def test_encoder_que_no_distingue_sentido(self):
        n, t, _ = ii.evaluar_cadena({"+": (2000, 500), "-": (1900, -500)}, 0.5)
        self.assertEqual(n, "aviso")

    def test_la_placa_no_aplica_el_pwm(self):
        n, t, _ = ii.evaluar_cadena({"+": (0, 0), "-": (0, 0)}, 0.5)
        self.assertEqual((n, "NO está aplicando" in t), ("error", True))


    def test_en_idle_el_veredicto_explica_el_estado(self):
        n, t, l = ii.evaluar_cadena({"+": (0, 0), "-": (0, 0)}, 0.5, "idle")
        self.assertTrue(any("idle" in x and "Reset" in x for x in l))


    def test_evalua_con_el_duty_de_cada_intento(self):
        n, t, l = ii.evaluar_cadena({"+": (900, 800, 0.8), "-": (-800, -1000, -1.0)}, 0.5)
        self.assertEqual(n, "ok")
        self.assertTrue(any("80 %" in y and "100 %" in y for y in l))


class TestInterfaz(unittest.TestCase):
    def crear(self, falla="ninguna"):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.app = ii.App(self.root, simulado=True, falla=falla, salida=Path(self.tmp.name))
        self.app.factor_sim = 10
        self.app.dormir_plan = lambda s: None
        self.app.conectar()
        self.assertTrue(bombear(self.root, lambda: len(self.app.marcas) >= 10, 10), "no llegó telemetría")

    def tearDown(self):
        try:
            self.root.after_cancel(self.app._id_ciclo)
            self.app.enlace.cerrar()
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.tmp.cleanup()

    def probar(self):
        self.app.iniciar_prueba()
        self.assertTrue(bombear(self.root, lambda: self.app.prueba is None and "Probando" not in self.app.res.cget("text")), "la prueba no terminó")
        return self.app.res.cget("text")

    def test_telemetria_y_lecturas_en_vivo(self):
        self.crear()
        self.app.sl.set(50)
        self.app.aplicar_manual()
        self.assertTrue(bombear(self.root, lambda: (self.app.ult[0] or (0, 0))[1] == 500, 5), "u no llegó a 500‰")
        self.assertTrue(bombear(self.root, lambda: abs(self.app.ult[0][0]) > 100, 10), "el motor simulado no giró")
        bombear(self.root, lambda: False, 0.6)
        self.assertEqual(self.app.t_u.v.cget("text"), "500")
        self.assertIn("Hz", self.app.t_hz.v.cget("text"))

    def test_prueba_de_la_cadena_ok(self):
        self.crear()
        self.assertIn("funciona", self.probar())

    def test_prueba_detecta_el_motor_que_no_gira(self):
        self.crear("sin_giro")
        t = self.probar()
        self.assertIn("NO gira", t)
        self.assertIn("STBY", self.app.det.cget("text"))

    def test_prueba_detecta_un_solo_sentido(self):
        self.crear("solo_positivo")
        self.assertIn("positivo", self.probar())

    def test_prueba_detecta_encoder_invertido(self):
        self.crear("encoder_invertido")
        self.assertIn("al revés", self.probar())

    def test_el_test_sale_solo_del_estado_idle(self):
        self.crear()
        self.app.parar()                                         # PARAR deja la placa en «idle», donde el firmware ignora el duty
        self.assertTrue(bombear(self.root, lambda: self.app.estado == "idle", 5))
        self.assertIn("funciona", self.probar())
        self.assertEqual(self.app.estado, "run")

    def test_manejo_manual_tambien_sale_de_idle(self):
        self.crear()
        self.app.parar()
        bombear(self.root, lambda: self.app.estado == "idle", 5)
        self.app.sl.set(50)
        self.app.aplicar_manual()
        self.assertTrue(bombear(self.root, lambda: (self.app.ult[0] or (0, 0))[1] == 500, 8), "con la placa en idle el duty no se aplicó")

    def test_motor_con_zona_muerta_grande_arranca_en_un_nivel_mayor(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.app = ii.App(self.root, simulado=True, salida=Path(self.tmp.name))
        self.app.factor_sim, self.app.u0_sim = 10, 0.6               # no se mueve al 50 %, sí al 65 %
        self.app.conectar()
        bombear(self.root, lambda: len(self.app.marcas) >= 10, 10)
        self.app.enviar({"cfg": "pwm_max", "val": 1.0})              # como el usuario: subir pwm_max en la placa (la zona muerta es de 0.6)
        self.app.enviar({"cmd": "status"})
        self.assertTrue(bombear(self.root, lambda: self.app.pwm_max[0] == 1.0, 10), "la placa no aceptó pwm_max 1.0")
        t = self.probar()
        self.assertIn("funciona", t)
        self.assertIn("65", self.app.det.cget("text"))
        self.assertIn("más del 50", self.app.det.cget("text"))

    def test_si_pwm_max_es_bajo_el_error_sugiere_subirlo(self):
        self.crear("sin_giro")
        self.app.pwm_max = [0.6] * 3
        self.probar()
        self.assertIn("pwm_max", self.app.det.cget("text"))

    def test_la_prueba_deja_el_duty_en_cero(self):
        self.crear()
        self.probar()
        bombear(self.root, lambda: self.app.ult[0][1] == 0, 5)
        self.assertEqual(self.app.ult[0][1], 0)

    def test_sin_conexion_no_hace_nada(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.app = ii.App(self.root, simulado=True, salida=Path(self.tmp.name))
        with mock.patch.object(ii.messagebox, "showinfo") as m:
            self.app.iniciar_prueba()
        self.assertTrue(m.called)
        self.app.enlace = mock.Mock()

    def test_plan_de_identificacion_recupera_el_modelo(self):
        self.crear()
        with mock.patch.object(ii.messagebox, "askyesno", return_value=True):
            self.app.ejecutar_plan()
        self.assertTrue(bombear(self.root, lambda: not self.app.ocupado, 60), "el plan no terminó")
        txt = self.app.txt_inf.get("1.0", "end")
        self.assertIn("Motor 1", txt)
        self.assertIn("K [°/s]", txt)
        self.assertTrue(any(f.startswith("modelo_motor1") for f in os.listdir(self.tmp.name)))
        self.assertGreaterEqual(len([f for f in os.listdir(self.tmp.name) if f.endswith(".csv")]), 10)

    def test_plan_con_motor_que_no_gira_da_error_claro(self):
        self.crear("sin_giro")
        with mock.patch.object(ii.messagebox, "askyesno", return_value=True):
            self.app.ejecutar_plan()
        self.assertTrue(bombear(self.root, lambda: not self.app.ocupado, 60))
        self.assertIn("no gira", self.app.lbl_plan.cget("text"))

    def _escribir_ganancias(self, ctrl="casc"):
        from spm.sintonia import Ganancias, Planta, a_json, conservadora
        g = Ganancias(ctrl=ctrl, pwm_max=0.7)
        a_json(conservadora(g), Planta(), {}, 1, Path(self.tmp.name) / "ganancias_motor1.json", optimizadas=g, metr_opt={})

    def test_cargar_ganancias_envia_las_ordenes_al_motor(self):
        self.crear()
        self._escribir_ganancias("casc")
        self.app.cargar_ganancias()
        self.assertTrue(bombear(self.root, lambda: self.app.enlace.p.ctrl[0] == "casc", 5))
        nombres = {e.get("cfg") for e in self.app.enlace.p.enviados}
        self.assertTrue({"kpp", "kpv", "kiv", "kffv", "pwm_min", "band"} <= nombres)
        self.assertEqual(self.app.ctrl_cargado[0], "casc")

    def test_cargar_ganancias_sin_archivo_avisa(self):
        self.crear()
        with mock.patch.object(ii.messagebox, "showerror") as m:
            self.app.cargar_ganancias()
        self.assertTrue(m.called)

    def test_ir_a_posicion_llega_y_muestra_objetivo_y_error(self):
        self.crear()
        self._escribir_ganancias("pid")
        self.app.cargar_ganancias()
        self.app.var_destino.set("90")
        self.app.ir_a()
        self.assertTrue(bombear(self.root, lambda: abs(self.app.ult[0][0] * 360 / self.app.ppr[0] - 90) < 2.0, 25), "no llegó a 90°")
        bombear(self.root, lambda: False, 0.5)
        self.assertEqual(self.app.t_obj.v.cget("text"), "90.0")
        self.assertLess(abs(float(self.app.t_err.v.cget("text"))), 2.0)

    def test_ir_a_fuera_de_rango_se_rechaza(self):
        self.crear()
        self.app.var_destino.set("900")
        with mock.patch.object(ii.messagebox, "showwarning") as w:
            self.app.ir_a()
        self.assertTrue(w.called)

    def test_ir_a_sin_ganancias_pide_confirmacion(self):
        self.crear()
        with mock.patch.object(ii.messagebox, "askyesno", return_value=False) as q:
            self.app.ir_a()
        self.assertTrue(q.called)
        self.assertNotIn("pos", [k for e in self.app.enlace.p.enviados for k in e])

    def test_guardar_telemetria_escribe_un_csv(self):
        self.crear()
        bombear(self.root, lambda: len(self.app.datos[0]) >= 20, 10)
        self.app.guardar_telemetria()
        archivos = [f for f in os.listdir(self.tmp.name) if f.startswith("telemetria_motor1_")]
        self.assertEqual(len(archivos), 1)
        txt = (Path(self.tmp.name) / archivos[0]).read_text(encoding="utf-8")
        self.assertIn("t_s,pos_cuentas,u_permil,objetivo_cuentas,error_deg", txt)
        self.assertGreater(len(txt.splitlines()), 10)

    def test_aplicar_ppr_escrito_a_mano(self):
        self.crear()
        self.app.var_ppr.set("4532")
        self.app.aplicar_ppr()
        self.assertTrue(bombear(self.root, lambda: self.app.ppr[0] == 4532.0, 5), "la placa no aceptó el ppr")
        self.assertEqual(self.app.enlace.p.ppr[0], 4532.0)

    def test_aplicar_ppr_invalido_no_envia_nada(self):
        self.crear()
        self.app.var_ppr.set("abc")
        self.app.aplicar_ppr()
        bombear(self.root, lambda: False, 0.3)
        self.assertEqual(self.app.enlace.p.ppr[0], 1540.0)

    def test_medir_cuentas_por_vuelta(self):
        self.crear()
        self.app.sp_vueltas.set("2")
        self.app.medir_vuelta()
        self.assertEqual(self.app.vueltas, 2)
        self.app.enlace.enviar({"cfg": "ctrl", "motor": 1, "val": "open"})
        self.app.enlace.enviar({"duty": 1, "val": 0.3})
        bombear(self.root, lambda: abs(self.app.ult[0][0] - self.app.ppr_medida) > 300, 10)
        self.app.enlace.enviar({"duty": 1, "val": 0})
        bombear(self.root, lambda: False, 0.8)
        antes = self.app.ppr_medida
        total = abs(self.app.ult[0][0] - antes)
        self.app.medir_vuelta()
        self.assertAlmostEqual(self.app.ppr_listo, round(total / 2, 1), delta=40)      # reparte las cuentas entre las 2 vueltas
        self.assertGreater(self.app.ppr_listo, 150)


if __name__ == "__main__":
    unittest.main()
