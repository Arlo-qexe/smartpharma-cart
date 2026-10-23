"""Pruebas del módulo de reconocimiento. No requieren motor OCR ni GPU.

Ejecutar desde ia/:  python -m unittest discover -s tests -t .
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

    def test_fecha_invalida(self):
        self.assertIsNone(parseo.buscar_vencimiento("VENC 13/2027"))
        self.assertIsNone(parseo.parsear_fecha("31/02/2027"))

    def test_lote(self):
        self.assertEqual(parseo.buscar_lote("Lote: AB1234"), "AB1234")
        self.assertEqual(parseo.buscar_lote("LOT 2403A FV 08/2027"), "2403A")
        self.assertEqual(parseo.buscar_lote("B.N. X-99/7"), "X-99/7")
        self.assertIsNone(parseo.buscar_lote("sin datos"))
        self.assertEqual(parseo.limpiar_lote("lote no. ab 12 34"), "AB1234")


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
            def leer(self, _):
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
