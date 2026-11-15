"""Pruebas de la telemetría (S-12, informe 4.9 y 5.6) y de --caras (S-14), sin hardware ni PC
real. Correr desde orange-pi/:  python -m unittest discover -s tests
"""
import json
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ.parent / "shared"))

import main  # noqa: E402
import monitor_telemetria  # noqa: E402
import telemetria  # noqa: E402
from network import decision_client as dc  # noqa: E402
from uart.esp_simulada import EnlaceSimulado  # noqa: E402
from uart.telemetria_esp import AlmacenTelemetria  # noqa: E402
from protocol_constants import (  # noqa: E402
    ACCION_SOLICITAR_TELEMETRIA,
    CARAS_POR_OBJETO_REDUCIDO,
    EVENTO_EN_POSICION,
    FASES_OPI,
    TELEM_NUM_ENCODERS,
    TELEM_NUM_PWM,
    empaquetar_mensaje_uart,
)

CAMPOS_ESP = {"t_ms", "estado", "alarma", "giro_dps", "corriente_ma", "enc", "pwm", "uart_ok",
              "uart_err", "heap", "temp_c"}
CAMPOS_OPI = {"t_s", "fase", "cpu_temp_c", "cpu_pct", "ram_pct", "disco_pct", "wifi_dbm", "rtt_pc_ms",
              "camara", "uart", "ciclos", "lotes_vacios", "ultimo_ciclo_s"}


class SerieFalso:
    """Puerto serie falso: `meter()` pone líneas que `readline()` entrega al hilo lector."""

    def __init__(self):
        self.escrito, self._lineas, self._cond = [], [], threading.Condition()

    def meter(self, mensaje: dict):
        with self._cond:
            self._lineas.append(empaquetar_mensaje_uart(mensaje))
            self._cond.notify_all()

    def readline(self):
        with self._cond:
            self._cond.wait_for(lambda: self._lineas, timeout=0.05)
            return self._lineas.pop(0) if self._lineas else b""

    def write(self, datos):
        self.escrito.append(datos)

    def close(self):
        pass


def enlace_con_serie():
    from uart.uart_link import EnlaceUART
    serie = SerieFalso()
    with mock.patch("uart.uart_link.serial") as modulo:
        modulo.Serial.return_value = serie
        return EnlaceUART("/dev/null"), serie


class Almacen(unittest.TestCase):
    def test_sin_datos_y_luego_la_ultima_sin_el_campo_evento(self):
        a = AlmacenTelemetria()
        self.assertEqual(a.ultima(), (None, None))
        a.guardar({"evento": "telemetria", "t_ms": 5})
        datos, edad = a.ultima()
        self.assertEqual(datos, {"t_ms": 5})
        self.assertGreaterEqual(edad, 0)

    def test_esperar_nueva(self):
        a = AlmacenTelemetria()
        self.assertFalse(a.esperar_nueva(a.contador(), 0.05))
        threading.Timer(0.05, a.guardar, ({"evento": "telemetria"},)).start()
        self.assertTrue(a.esperar_nueva(0, 1.0))


class LectorUART(unittest.TestCase):
    def test_la_telemetria_no_se_confunde_con_una_confirmacion(self):
        enlace, serie = enlace_con_serie()
        try:
            serie.meter({"evento": "telemetria", "t_ms": 1, "estado": "reposo"})   # llega ANTES de la confirmación
            serie.meter({"evento": EVENTO_EN_POSICION, "cara": 2})
            serie.meter({"evento": "telemetria", "t_ms": 2})                        # y también después
            self.assertEqual(enlace.recibir(timeout=1.0), {"evento": EVENTO_EN_POSICION, "cara": 2})
            self.assertIsNone(enlace.recibir(timeout=0.2))
            self.assertTrue(enlace.telemetria.esperar_nueva(0, 1.0))
            time.sleep(0.15)
            self.assertEqual(enlace.telemetria.ultima()[0]["t_ms"], 2)
        finally:
            enlace.close()

    def test_mensaje_con_checksum_malo_se_descarta(self):
        enlace, serie = enlace_con_serie()
        try:
            serie._lineas.append(b'{"evento": "en_posicion", "cara": 1}|00\n')
            self.assertIsNone(enlace.recibir(timeout=0.3))
        finally:
            enlace.close()

    def test_solicitar_telemetria_escribe_la_accion_sin_borrar_eventos_pendientes(self):
        enlace, serie = enlace_con_serie()
        try:
            enlace._eventos.append({"evento": EVENTO_EN_POSICION, "cara": 1})
            enlace.solicitar_telemetria()
            self.assertEqual(json.loads(serie.escrito[0].decode().split("|")[0]),
                             {"accion": ACCION_SOLICITAR_TELEMETRIA})
            self.assertEqual(enlace.recibir(timeout=0.1), {"evento": EVENTO_EN_POSICION, "cara": 1})
        finally:
            enlace.close()


class EspSimuladaTelemetria(unittest.TestCase):
    def test_responde_con_todos_los_campos_del_contrato(self):
        esp = EnlaceSimulado(0)
        esp.solicitar_telemetria()
        datos, _ = esp.telemetria.ultima()
        self.assertEqual(set(datos), CAMPOS_ESP)
        self.assertEqual(len(datos["enc"]), TELEM_NUM_ENCODERS)
        self.assertEqual(len(datos["pwm"]), TELEM_NUM_PWM)


class DescubridorFalso:
    def __init__(self, ip="127.0.0.1"):
        self.ip, self.invalidaciones = ip, 0

    def invalidar(self):
        self.invalidaciones += 1


class Servicio(unittest.TestCase):
    def servicio(self, enlace, descubridor, enviar, tmp):
        return telemetria.ServicioTelemetria(
            enlace, descubridor, telemetria.EstadoOrangePi(), enviar=enviar, hay_camara=lambda: True,
            ruta_instantanea=Path(tmp), intervalo_esp=0.05, intervalo_pc=0.05, plazo_esp=0.2)

    def correr(self, servicio, segundos=0.5):
        servicio.iniciar()
        time.sleep(segundos)
        servicio.detener()

    def test_envia_a_la_pc_ambas_telemetrias_y_deja_la_instantanea(self):
        import tempfile
        enviados = []
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "t.json"
            s = self.servicio(EnlaceSimulado(0), DescubridorFalso(),
                              lambda ip, opi, esp, edad: enviados.append((ip, opi, esp, edad)) or True, ruta)
            s._estado.fase = "capturando"
            servicio_instantanea = []
            s.iniciar()
            time.sleep(0.5)
            servicio_instantanea.append(json.loads(ruta.read_text()))
            s.detener()
            self.assertFalse(ruta.exists())   # al detener se limpia la instantánea
        self.assertTrue(enviados)
        ip, opi, esp, edad = enviados[-1]
        self.assertEqual(ip, "127.0.0.1")
        self.assertEqual(set(opi), CAMPOS_OPI)
        self.assertIn(opi["fase"], FASES_OPI)
        self.assertEqual(opi["uart"], "ok")
        self.assertEqual(opi["camara"], "disponible")
        self.assertEqual(set(esp), CAMPOS_ESP)
        self.assertIsNotNone(edad)
        self.assertEqual(set(servicio_instantanea[0]) , {"orange_pi", "esp32", "esp32_edad_s", "t_unix"})

    def test_la_esp_que_no_responde_marca_sin_respuesta_y_envia_null(self):
        import tempfile

        class EspMuda:
            telemetria = AlmacenTelemetria()

            def solicitar_telemetria(self):
                pass
        enviados = []
        with tempfile.TemporaryDirectory() as d:
            s = self.servicio(EspMuda(), DescubridorFalso(),
                              lambda ip, opi, esp, edad: enviados.append((opi, esp, edad)) or True, Path(d) / "t.json")
            self.correr(s)
        opi, esp, edad = enviados[-1]
        self.assertEqual(opi["uart"], "sin_respuesta")
        self.assertIsNone(esp)
        self.assertIsNone(edad)

    def test_si_la_pc_falla_se_descarta_sin_reintento_ni_invalidar_la_cache(self):
        import tempfile
        intentos, desc = [], DescubridorFalso()

        def enviar(*a):
            intentos.append(1)
            raise ConnectionRefusedError("x")
        with tempfile.TemporaryDirectory() as d:
            s = self.servicio(EnlaceSimulado(0), desc, enviar, Path(d) / "t.json")
            self.correr(s, 0.4)
        self.assertGreaterEqual(len(intentos), 2)   # sigue enviando cada intervalo, sin explotar
        self.assertEqual(desc.invalidaciones, 0)
        self.assertIsNone(s._rtt_pc_ms)

    def test_sin_pc_descubierta_no_envia_nada(self):
        import tempfile
        enviados = []
        with tempfile.TemporaryDirectory() as d:
            s = self.servicio(EnlaceSimulado(0), DescubridorFalso(ip=None),
                              lambda *a: enviados.append(1) or True, Path(d) / "t.json")
            self.correr(s, 0.3)
        self.assertEqual(enviados, [])

    def test_si_el_enlace_revienta_la_telemetria_no_tumba_nada(self):
        import tempfile

        class EnlaceRoto:
            telemetria = AlmacenTelemetria()

            def solicitar_telemetria(self):
                raise OSError("puerto caído")
        with tempfile.TemporaryDirectory() as d:
            s = self.servicio(EnlaceRoto(), DescubridorFalso(), lambda *a: True, Path(d) / "t.json")
            self.correr(s, 0.3)   # no lanza


class EnvioPorSocket(unittest.TestCase):
    def servir(self, respuesta):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        visto = {}

        def atender():
            c, _ = srv.accept()
            (n,) = struct.unpack("!I", c.recv(4))
            visto["msg"] = json.loads(c.recv(n))
            r = json.dumps(respuesta).encode()
            c.sendall(struct.pack("!I", len(r)) + r)
            c.close()
        threading.Thread(target=atender, daemon=True).start()
        return srv, visto

    def test_el_mensaje_lleva_la_consulta_y_los_tres_campos(self):
        srv, visto = self.servir({"ok": True})
        ok = dc.enviar_telemetria("127.0.0.1", {"t_s": 1}, {"t_ms": 2}, 0.5, puerto=srv.getsockname()[1])
        self.assertTrue(ok)
        self.assertEqual(visto["msg"], {"consulta": "telemetria", "orange_pi": {"t_s": 1},
                                        "esp32": {"t_ms": 2}, "esp32_edad_s": 0.5})

    def test_una_pc_que_aun_no_atiende_la_telemetria_devuelve_false(self):
        srv, _ = self.servir({"error": "consulta_invalida"})
        self.assertFalse(dc.enviar_telemetria("127.0.0.1", {}, None, None, puerto=srv.getsockname()[1]))


class Caras(unittest.TestCase):
    def capturar(self, caras):
        pedidas = []

        class Enlace:
            def enviar(self, m):
                pedidas.append(m["cara"])
                self._cara = m["cara"]

            def recibir(self, timeout=None):
                return {"evento": EVENTO_EN_POSICION, "cara": self._cara}

        class Camaras:
            def __enter__(self):
                return object()

            def __exit__(self, *a):
                return False
        with mock.patch.object(main, "capturar_con_autoenfoque", return_value=b"f"), \
                mock.patch.object(main, "comprimir_jpeg", return_value=b"j"), \
                mock.patch.object(main, "PAUSA_ESTABILIZACION_MECANICA_S", 0):
            return main.capturar_lote_completo(Enlace(), Camaras(), caras), pedidas

    def test_con_4_caras_se_piden_las_caras_1_a_4_y_el_lote_lleva_4_imagenes(self):
        imagenes, pedidas = self.capturar(CARAS_POR_OBJETO_REDUCIDO)
        self.assertEqual(pedidas, [1, 2, 3, 4])
        self.assertEqual(len(imagenes), 4)

    def test_por_defecto_son_5(self):
        imagenes, pedidas = self.capturar(5)
        self.assertEqual(pedidas, [1, 2, 3, 4, 5])
        self.assertEqual(len(imagenes), 5)

    def test_main_pasa_caras_al_ciclo_y_rechaza_valores_invalidos(self):
        for argv, esperado in (([], 5), (["--caras", "4"], 4), (["--caras", "5"], 5)):
            with mock.patch.object(main, "EnlaceSimulado"), \
                    mock.patch.object(main, "buscar_camara_por_nombre"), \
                    mock.patch.object(main, "DescubridorPC"), \
                    mock.patch.object(main, "ServicioTelemetria"), \
                    mock.patch.object(main, "ciclo_de_un_objeto", side_effect=KeyboardInterrupt) as ciclo:
                main.main(["--sin-orden", "--simular-esp"] + argv)
            self.assertEqual(ciclo.call_args.args[4], esperado)
        with self.assertRaises(SystemExit):
            main.main(["--caras", "3"])


class Monitor(unittest.TestCase):
    def test_formatea_ambas_telemetrias(self):
        datos = {"t_unix": 1000.0, "esp32_edad_s": 1.0,
                 "orange_pi": {"t_s": 5, "fase": "capturando", "cpu_pct": 12.0, "cpu_temp_c": None},
                 "esp32": {"estado": "reposo", "alarma": False, "enc": [1, 2, 3, 4, 5]}}
        texto = monitor_telemetria.formatear(datos, 1002.0)
        for esperado in ("Orange Pi", "capturando", "ESP32-S3", "reposo", "[1, 2, 3, 4, 5]"):
            self.assertIn(esperado, texto)

    def test_sin_esp_y_con_instantanea_vieja(self):
        base = {"t_unix": 1000.0, "orange_pi": {}, "esp32": None}
        self.assertIn("sin datos", monitor_telemetria.formatear(base, 1001.0))
        self.assertIn("Sin datos recientes", monitor_telemetria.formatear(base, 1100.0))


if __name__ == "__main__":
    unittest.main()
