"""Pruebas del módulo de reconocimiento. No requieren motor OCR ni GPU.

Ejecutar desde IA/:  python -m unittest discover -s tests -t .
"""
import io
import unittest
from datetime import date

from PIL import Image

from smartpharma_ia import fefo, parseo, validacion
from smartpharma_ia.config import cargar_config
from smartpharma_ia.ocr import OcrGuionado
from smartpharma_ia.pipeline import Reconocedor
from smartpharma_ia.tipos import RESULTADO_ERROR_REVISION_MANUAL as ERR, LecturaCara

HOY = date(2026, 10, 16)


def jpeg(color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


def cinco(*_):
    return [jpeg() for _ in range(5)]


class TestParseo(unittest.TestCase):
    def test_vencimiento_formatos(self):
        casos = {
            "FV: 08/2027": date(2027, 8, 31),
            "F.V. 03/27": date(2027, 3, 31),
            "VENCE 2028-02": date(2028, 2, 29),          # bisiesto
            "Fecha de vencimiento: 15/09/2027": date(2027, 9, 15),
            "EXP AGO 2027": date(2027, 8, 31),
            "EXP DATE: JAN.2028": date(2028, 1, 31),
            "Vto. 12-2026": date(2026, 12, 31),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parseo.buscar_vencimiento(texto), esperado)

    def test_ignora_fecha_de_fabricacion(self):
        self.assertEqual(
            parseo.buscar_vencimiento("FAB 01/2025\nVENC 01/2028"), date(2028, 1, 31))
        self.assertIsNone(parseo.buscar_vencimiento("FAB 01/2025 MFG 01/2025"))

    def test_vencimiento_en_ingles(self):
        self.assertEqual(parseo.buscar_vencimiento("USE BEFORE 09/2026"), date(2026, 9, 30))
        self.assertEqual(parseo.buscar_vencimiento("Use by: 03/2028"), date(2028, 3, 31))
        self.assertEqual(parseo.buscar_vencimiento("BEST BEFORE 12/2027"), date(2027, 12, 31))

    def test_cada_no_es_caducidad(self):
        """"CAD" es el inicio de "CADA": una posología con una fecha cerca no es un vencimiento."""
        self.assertIsNone(parseo.buscar_vencimiento("TOMAR CADA 8 HORAS. 15/06/2025"))
        self.assertIsNone(parseo.buscar_vencimiento("Una tableta cada 12 horas 08/2027"))
        self.assertEqual(parseo.buscar_vencimiento("CAD. 08/2027"), date(2027, 8, 31))
        self.assertEqual(parseo.buscar_vencimiento("CAD: 08/2027"), date(2027, 8, 31))
        self.assertEqual(parseo.buscar_vencimiento("CADUCIDAD 08/2027"), date(2027, 8, 31))

    def test_fecha_invalida(self):
        self.assertIsNone(parseo.buscar_vencimiento("VENC 13/2027"))
        self.assertIsNone(parseo.parsear_fecha("31/02/2027"))

    def test_lote(self):
        self.assertEqual(parseo.buscar_lote("Lote: AB1234"), "AB1234")
        self.assertEqual(parseo.buscar_lote("LOT 2403A FV 08/2027"), "2403A")
        self.assertEqual(parseo.buscar_lote("B.N. X-99/7"), "X-99/7")
        self.assertIsNone(parseo.buscar_lote("sin datos"))
        self.assertEqual(parseo.limpiar_lote("lote no. ab 12 34"), "AB1234")

    def test_lote_con_numero_abreviado(self):
        """"Lote No./Nº/N°" es muy común en empaques colombianos; antes buscar_lote devolvía None."""
        casos = {
            "Lote No. 12345": "12345", "LOTE NO: 12345": "12345", "LOTE NO 12345": "12345",
            "Lote Nº 12345": "12345", "LOTE N° 12345": "12345", "LOTE N°12345": "12345",
            "Lote № 12345": "12345", "LOTE: NO 12345": "12345", "Lote N. 12345": "12345",
            "LOTE # 12345": "12345", "LOTE: AB1234": "AB1234",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parseo.buscar_lote(texto), esperado)

    def test_lote_que_empieza_por_no_o_n_no_pierde_sus_letras(self):
        self.assertEqual(parseo.buscar_lote("LOTE NO123"), "NO123")
        self.assertEqual(parseo.buscar_lote("LOTE NO-123"), "NO-123")
        self.assertEqual(parseo.buscar_lote("LOTE N-12345"), "N-12345")

    def test_lote_demasiado_largo_no_se_corta_en_silencio(self):
        self.assertEqual(parseo.buscar_lote("LOTE ABCDEFGHIJKLMN1"), "ABCDEFGHIJKLMN1")   # 15: el máximo
        self.assertIsNone(parseo.buscar_lote("LOTE ABCDEFGHIJKLMNO1"))                   # 16: no es un lote
        self.assertIsNone(parseo.buscar_lote("LOTE 123456789012345678"))


class TestConfigFefo(unittest.TestCase):
    def setUp(self):
        self.cfg = cargar_config()

    def test_config_defecto_valida_y_marca_placeholder(self):
        self.assertEqual(self.cfg.estado_rangos, "PLACEHOLDER_SIN_CONFIRMAR")

    def test_rangos(self):
        d = lambda v: fefo.destino_para(v, HOY, self.cfg)
        self.assertEqual(d(date(2026, 10, 15)), "DESCARTE")   # vencido ayer
        self.assertEqual(d(date(2026, 10, 16)), "TIPO_A")     # vence hoy: aún vigente
        self.assertEqual(d(date(2027, 1, 14)), "TIPO_A")      # 90 días
        self.assertEqual(d(date(2027, 1, 15)), "TIPO_B")      # 91 días
        self.assertEqual(d(date(2030, 1, 1)), "TIPO_C")

    def test_config_invalida(self):
        import json, tempfile, pathlib
        malo = {"rangos_fefo": [{"hasta_dias": None, "destino": "A"}, {"hasta_dias": 5, "destino": "B"}]}
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "c.json"
            p.write_text(json.dumps(malo))
            with self.assertRaises(ValueError):
                cargar_config(p)


class TestConsolidacion(unittest.TestCase):
    def setUp(self):
        self.cfg = cargar_config()

    def _l(self, i, lote=None, venc=None, c=0.9):
        return LecturaCara(i, lote=lote, vencimiento=venc, confianza_lote=c, confianza_vencimiento=c)

    def test_combina_campos_de_caras_distintas(self):
        r = validacion.consolidar(
            [self._l(0, lote="AB123"), self._l(1, venc=date(2027, 8, 31)), self._l(2), self._l(3), self._l(4)],
            HOY, self.cfg)
        self.assertEqual((r["lote"], r["vencimiento"], r["errores"]), ("AB123", date(2027, 8, 31), []))

    def test_conflicto_entre_fotos(self):
        r = validacion.consolidar(
            [self._l(0, lote="AB123", venc=date(2027, 8, 31)), self._l(1, lote="ZZ999", venc=date(2027, 8, 31))],
            HOY, self.cfg)
        self.assertIn("lote:conflicto_entre_fotos", r["errores"])

    def test_gana_la_mayoria_estricta(self):
        r = validacion.consolidar(
            [self._l(0, lote="AB123"), self._l(1, lote="AB123"), self._l(2, lote="AB128"),
             self._l(3, venc=date(2027, 8, 31))], HOY, self.cfg)
        self.assertEqual(r["lote"], "AB123")

    def test_confianza_baja_y_fecha_implausible(self):
        r = validacion.consolidar([self._l(0, lote="AB123", venc=date(2027, 8, 31), c=0.3)], HOY, self.cfg)
        self.assertIn("lote:confianza_baja", r["errores"])
        r = validacion.consolidar([self._l(0, lote="AB123", venc=date(2099, 1, 1))], HOY, self.cfg)
        self.assertIn("vencimiento:ausente", r["errores"])

    def test_lote_formato_invalido(self):
        r = validacion.consolidar([self._l(0, lote="ABCDEF", venc=date(2027, 8, 31))], HOY, self.cfg)
        self.assertIn("lote:ausente", r["errores"])  # sin dígitos -> no es un lote válido


class TestPipeline(unittest.TestCase):
    def _rec(self, respuestas):
        return Reconocedor(OcrGuionado(respuestas), config=cargar_config(), hoy=HOY)

    def test_lote_vacio(self):
        r = self._rec([]).procesar([])
        self.assertEqual((r.clasificacion, r.motivo), (ERR, "lote_vacio"))

    def test_caso_feliz_lote_y_fecha_en_caras_distintas(self):
        rec = self._rec([("", 0.0), ("LOTE: AB1234", 0.95), ("FV 08/2027", 0.9), ("", 0.0), ("", 0.0)])
        r = rec.procesar(cinco())
        self.assertEqual(r.clasificacion, "TIPO_C")
        self.assertEqual((r.lote, r.vencimiento), ("AB1234", date(2027, 8, 31)))
        self.assertEqual(r.dias_para_vencer, 319)
        self.assertAlmostEqual(r.confianza, 0.9)

    def test_vencido_va_a_descarte(self):
        rec = self._rec([("Lote AB1234 VENC 01/2026", 0.9)] + [("", 0.0)] * 4)
        self.assertEqual(rec.procesar(cinco()).clasificacion, "DESCARTE")

    def test_nada_legible_va_a_revision(self):
        r = self._rec([("ruido", 0.2)] * 5).procesar(cinco())
        self.assertEqual(r.clasificacion, ERR)
        self.assertIn("lote:ausente", r.motivo)

    def test_imagen_corrupta_no_tumba_el_pipeline(self):
        rec = self._rec([("Lote AB1234 VENC 08/2027", 0.9)])
        r = rec.procesar([jpeg(), b"no soy una imagen", jpeg(), jpeg(), jpeg()])
        self.assertEqual(r.clasificacion, "TIPO_C")
        self.assertIn("imagen_ilegible", r.lecturas[1].observaciones)

    def test_ocr_que_lanza_excepcion(self):
        class Roto:
            def leer(self, _, exhaustivo=True):
                raise RuntimeError("boom")
        r = Reconocedor(Roto(), config=cargar_config(), hoy=HOY).procesar(cinco())
        self.assertEqual(r.clasificacion, ERR)

    def test_regiones_etiquetadas(self):
        from smartpharma_ia.tipos import Region

        class Det:
            def detectar(self, img):
                return [Region("lote", (0, 0, 10, 10)), Region("fecha", (10, 10, 30, 30)),
                        Region("nombre", (0, 20, 30, 40))]
        rec = Reconocedor(OcrGuionado([("lote ab-77", 0.9), ("03/2028", 0.9), ("Acetaminofen", 0.9)]),
                          detector=Det(), config=cargar_config(), hoy=HOY)
        r = rec.procesar([jpeg()])
        self.assertEqual((r.lote, r.vencimiento, r.nombre), ("AB-77", date(2028, 3, 31), "Acetaminofen"))
        self.assertEqual(r.clasificacion, "TIPO_C")

    def test_estrategia_mayoria_exige_acuerdo(self):
        import dataclasses
        cfg = dataclasses.replace(cargar_config(), estrategia="mayoria")
        rec = Reconocedor(OcrGuionado([("Lote AB1234 VENC 08/2027", 0.9)] + [("", 0.0)] * 4),
                          config=cfg, hoy=HOY)
        self.assertEqual(rec.procesar(cinco()).clasificacion, ERR)  # 1 de 5 no es mayoría


class TestTiempo(unittest.TestCase):
    """Presupuesto de tiempo por caja: la Orange Pi espera 10 s y luego reenvía el lote."""

    @staticmethod
    def _cfg(**cambios):
        import dataclasses
        return dataclasses.replace(cargar_config(), **cambios)

    def test_foto_completa_usa_una_sola_pasada_y_el_recorte_del_detector_no(self):
        from smartpharma_ia.tipos import Region

        class Espia:
            def __init__(self):
                self.exhaustivo = []

            def leer(self, imagen, exhaustivo=True):
                self.exhaustivo.append(exhaustivo)
                return "", 0.0

        espia = Espia()
        Reconocedor(espia, config=cargar_config(), hoy=HOY).procesar(cinco())
        self.assertEqual(espia.exhaustivo, [False] * 5)          # foto completa: 1 pasada por cara

        class Det:
            def detectar(self, img):
                return [Region("lote", (0, 0, 10, 10))]
        espia = Espia()
        Reconocedor(espia, detector=Det(), config=cargar_config(), hoy=HOY).procesar([jpeg()])
        self.assertEqual(espia.exhaustivo, [True])               # recorte ajustado: se prueba más

    def test_ocr_rapid_hace_una_llamada_en_modo_simple_y_varias_en_exhaustivo(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy no instalado (viene con el motor OCR)")
        from smartpharma_ia.ocr import OcrRapid

        class Motor:
            llamadas = 0

            def __call__(self, _imagen):
                Motor.llamadas += 1
                return None, None
        ocr = OcrRapid.__new__(OcrRapid)          # sin cargar el modelo real
        ocr._motor = Motor()
        ancha, alta = Image.new("RGB", (1920, 1080)), Image.new("RGB", (100, 400))
        for imagen, simple, completo in ((ancha, 1, 4), (alta, 1, 6)):
            Motor.llamadas = 0
            ocr.leer(imagen, exhaustivo=False)
            self.assertEqual(Motor.llamadas, simple)
            Motor.llamadas = 0
            ocr.leer(imagen)
            self.assertEqual(Motor.llamadas, completo)

    def _con_reloj(self, config, segundos_por_llamada):
        """Pipeline con un OCR lento y un reloj falso: cada llamada al OCR avanza el reloj."""
        from unittest import mock
        reloj = [0.0]
        llamadas = []

        class Lento:
            def leer(self, imagen, exhaustivo=True):
                llamadas.append(1)
                reloj[0] += segundos_por_llamada
                return "", 0.0
        with mock.patch("smartpharma_ia.pipeline.time.monotonic", side_effect=lambda: reloj[0]):
            r = Reconocedor(Lento(), config=config, hoy=HOY).procesar(cinco())
        return r, len(llamadas)

    def test_tope_de_tiempo_responde_con_lo_leido_y_lo_marca(self):
        r, llamadas = self._con_reloj(self._cfg(tiempo_maximo_s=7.0), 3.0)
        self.assertEqual(llamadas, 3)                           # t=0, 3, 6 leen; t=9 ya no
        self.assertEqual(r.clasificacion, ERR)
        self.assertIn("tiempo_agotado", r.motivo)
        self.assertIn("lote:ausente", r.motivo)
        self.assertEqual([("tiempo_agotado" in l.observaciones) for l in r.lecturas],
                         [False, False, False, True, True])

    def test_sin_tope_se_leen_todas_las_caras(self):
        r, llamadas = self._con_reloj(self._cfg(tiempo_maximo_s=None), 100.0)
        self.assertEqual(llamadas, 5)
        self.assertNotIn("tiempo_agotado", r.motivo)

    def test_si_alcanza_el_tiempo_no_se_marca_agotado(self):
        r, llamadas = self._con_reloj(self._cfg(tiempo_maximo_s=7.0), 0.5)
        self.assertEqual(llamadas, 5)
        self.assertNotIn("tiempo_agotado", r.motivo)

    def test_el_tope_viene_de_la_config(self):
        import json
        import tempfile
        from pathlib import Path
        from smartpharma_ia.config import RUTA_CONFIG_DEFECTO
        self.assertEqual(cargar_config().tiempo_maximo_s, 7.0)
        base = json.loads(RUTA_CONFIG_DEFECTO.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as carpeta:
            def con_tope(valor):
                ruta = Path(carpeta) / "cfg.json"
                ruta.write_text(json.dumps({**base, "tiempo_maximo_s": valor}), encoding="utf-8")
                return cargar_config(ruta)
            for valor, esperado in ((0, None), (None, None), (4.5, 4.5)):
                self.assertEqual(con_tope(valor).tiempo_maximo_s, esperado)
            for malo in (-1, "rápido", True):
                with self.assertRaises(ValueError):
                    con_tope(malo)


class TestAdaptador(unittest.TestCase):
    def test_sin_motor_ocr_devuelve_revision_manual(self):
        import os
        from unittest import mock
        from smartpharma_ia import adaptador_pc
        adaptador_pc._reconocedor = None
        with mock.patch.dict(os.environ, {"IA_OCR": "rapidocr"}), \
             mock.patch("smartpharma_ia.adaptador_pc.crear_ocr",
                        side_effect=__import__("smartpharma_ia.ocr", fromlist=["x"]).OcrNoDisponible("x")):
            self.assertEqual(adaptador_pc.procesar_lote_ocr(cinco()), ERR)
        adaptador_pc._reconocedor = None


if __name__ == "__main__":
    unittest.main()
