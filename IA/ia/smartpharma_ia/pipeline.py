"""Pipeline completo: 5 JPEG en memoria -> ResultadoCaja.

Flujo (equivale al diagrama de la PC en docs/DISENO.md):
  por cada cara: decodificar -> detector (regiones) -> OCR por región -> parseo
  -> consolidar entre caras (validación) -> rango FEFO -> destino.

Todo en RAM; nada se escribe a disco. Cualquier fallo inesperado de una etapa se
convierte en ERROR_REVISION_MANUAL con su motivo, nunca en una excepción que
tumbe el servidor TCP.
"""
from __future__ import annotations

import io
import logging
from datetime import date

from PIL import Image, UnidentifiedImageError

from . import fefo, parseo, validacion
from .config import Config, cargar_config
from .detector import Detector, DetectorImagenCompleta
from .ocr import Ocr
from .tipos import (ETIQUETA_COMPLETA, LecturaCara, ResultadoCaja, resultado_error)

log = logging.getLogger("smartpharma_ia")


class Reconocedor:
    def __init__(self, ocr: Ocr, detector: Detector | None = None,
                 config: Config | None = None, hoy: date | None = None):
        self.ocr = ocr
        self.detector = detector or DetectorImagenCompleta()
        self.config = config or cargar_config()
        self._hoy_fijo = hoy  # inyectable para pruebas; None = fecha real

    @property
    def hoy(self) -> date:
        return self._hoy_fijo or date.today()

    def procesar(self, imagenes: list[bytes]) -> ResultadoCaja:
        if not imagenes:
            return resultado_error("lote_vacio")

        lecturas: list[LecturaCara] = []
        for i, datos in enumerate(imagenes):
            lecturas.append(self._leer_cara(i, datos))

        c = validacion.consolidar(lecturas, self.hoy, self.config)
        if c["errores"]:
            return resultado_error(";".join(c["errores"]), lecturas)

        venc = c["vencimiento"]
        if venc is None:  # campos_obligatorios sin 'vencimiento': no se puede clasificar FEFO
            return resultado_error("vencimiento:ausente", lecturas)

        dias = fefo.dias_para_vencer(venc, self.hoy)
        return ResultadoCaja(
            clasificacion=fefo.rango_para(dias, self.config).destino,
            nombre=c["nombre"], gramaje=c["gramaje"], lote=c["lote"],
            vencimiento=venc, confianza=c["confianza"],
            dias_para_vencer=dias, lecturas=lecturas,
        )

    # ------------------------------------------------------------------
    def _leer_cara(self, indice: int, datos: bytes) -> LecturaCara:
        lectura = LecturaCara(indice=indice)
        try:
            imagen = Image.open(io.BytesIO(datos))
            imagen.load()
            imagen = imagen.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError):
            lectura.observaciones.append("imagen_ilegible")
            return lectura

        try:
            regiones = self.detector.detectar(imagen)
        except Exception:  # noqa: BLE001 - etapa externa/reemplazable
            log.exception("cara %d: falló el detector", indice)
            lectura.observaciones.append("detector_fallo")
            return lectura

        for region in regiones:
            x0, y0, x1, y1 = region.caja
            recorte = imagen.crop((x0, y0, x1, y1))
            try:
                texto, conf = self.ocr.leer(recorte)
            except Exception:  # noqa: BLE001
                log.exception("cara %d: falló el OCR en región %s", indice, region.etiqueta)
                lectura.observaciones.append(f"ocr_fallo:{region.etiqueta}")
                continue
            self._aplicar_texto(lectura, region.etiqueta, texto, conf)
        return lectura

    @staticmethod
    def _aplicar_texto(lectura: LecturaCara, etiqueta: str, texto: str, conf: float) -> None:
        if etiqueta == "lote":
            lectura.lote, lectura.confianza_lote = parseo.limpiar_lote(texto), conf
        elif etiqueta == "fecha":
            lectura.vencimiento, lectura.confianza_vencimiento = parseo.parsear_fecha(texto, tolerante=True), conf
        elif etiqueta == "nombre":
            lectura.nombre = " ".join(texto.split()) or None
        elif etiqueta == "gramaje":
            lectura.gramaje = " ".join(texto.split()) or None
        elif etiqueta == ETIQUETA_COMPLETA:
            lectura.lote = parseo.buscar_lote(texto)
            lectura.vencimiento = parseo.buscar_vencimiento(texto)
            lectura.confianza_lote = conf if lectura.lote else 0.0
            lectura.confianza_vencimiento = conf if lectura.vencimiento else 0.0
