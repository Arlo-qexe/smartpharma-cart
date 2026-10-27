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
import time
from datetime import date

from PIL import Image, UnidentifiedImageError

from . import fefo, parseo, prefiltro, validacion
from .config import Config, cargar_config
from .detector import Detector, DetectorImagenCompleta
from .ocr import Ocr
from .tipos import (ETIQUETA_COMPLETA, LecturaCara, ResultadoCaja, resultado_error)

log = logging.getLogger("smartpharma_ia")

MOTIVO_TIEMPO_AGOTADO = "tiempo_agotado"


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

        # Tope de tiempo: la Orange Pi espera la respuesta TIMEOUT_RESPUESTA_RECONOCIMIENTO_S y,
        # si no llega, reenvía el lote entero (y acaba en alarma local). Mejor responder a tiempo
        # con lo leído que no responder.
        limite = (time.monotonic() + self.config.tiempo_maximo_s) if self.config.tiempo_maximo_s else None
        lecturas = [LecturaCara(indice=i) for i in range(len(imagenes))]
        # 1) Detector en TODAS las caras (barato, ≈0,3 s); 2) OCR solo de lote/fecha, de la región más
        # confiable a la menos; 3) nombre/gramaje una sola vez, de la mejor cara (no son obligatorios).
        caras = [self._detectar_cara(lec, datos, limite) for lec, datos in zip(lecturas, imagenes)]
        self._leer_criticas(caras, lecturas, limite)
        self._leer_secundarias(caras, lecturas, limite)

        c = validacion.consolidar(lecturas, self.hoy, self.config)
        if c["errores"]:
            motivo = ";".join(c["errores"])
            if any(MOTIVO_TIEMPO_AGOTADO in l.observaciones for l in lecturas):
                motivo += ";" + MOTIVO_TIEMPO_AGOTADO
            return resultado_error(motivo, lecturas)

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
    @staticmethod
    def _sin_tiempo(limite: float | None) -> bool:
        return limite is not None and time.monotonic() > limite

    def _detectar_cara(self, lectura: LecturaCara, datos: bytes, limite: float | None) -> tuple[Image.Image | None, list]:
        """Decodifica y detecta regiones de una cara. Devuelve (imagen, regiones); (None, []) si falla."""
        if self._sin_tiempo(limite):
            lectura.observaciones.append(MOTIVO_TIEMPO_AGOTADO)
            return None, []
        try:
            imagen = Image.open(io.BytesIO(datos))
            imagen.load()
            imagen = imagen.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError):
            lectura.observaciones.append("imagen_ilegible")
            return None, []
        try:
            return imagen, self.detector.detectar(imagen)
        except Exception:  # noqa: BLE001 - etapa externa/reemplazable
            log.exception("cara %d: falló el detector", lectura.indice)
            lectura.observaciones.append("detector_fallo")
            return None, []

    def _leer_region(self, lectura: LecturaCara, imagen: Image.Image, region, limite: float | None) -> None:
        x0, y0, x1, y1 = region.caja
        recorte = imagen.crop((x0, y0, x1, y1))
        try:
            # Foto completa: una sola pasada (ver OcrRapid.leer). Recorte del detector: exhaustivo.
            texto, conf = self.ocr.leer(recorte, exhaustivo=region.etiqueta != ETIQUETA_COMPLETA)
        except Exception:  # noqa: BLE001
            log.exception("cara %d: falló el OCR en región %s", lectura.indice, region.etiqueta)
            lectura.observaciones.append(f"ocr_fallo:{region.etiqueta}")
            return
        if region.etiqueta in ("lote", "fecha") and not self._valido(region.etiqueta, texto):
            texto, conf = self._reintentar(recorte, region.etiqueta, texto, conf, limite)
        self._aplicar_texto(lectura, region.etiqueta, texto, conf)

    def _leer_criticas(self, caras, lecturas: list[LecturaCara], limite: float | None) -> None:
        """OCR de lote/fecha (o de la foto completa si no hay detector), de mayor a menor confianza del detector."""
        tareas = sorted(((i, r) for i, (_, regs) in enumerate(caras) for r in regs
                         if r.etiqueta in ("lote", "fecha", ETIQUETA_COMPLETA)),
                        key=lambda t: -t[1].confianza)
        for n, (i, region) in enumerate(tareas):
            if self._sin_tiempo(limite):
                for j, _ in tareas[n:]:
                    if MOTIVO_TIEMPO_AGOTADO not in lecturas[j].observaciones:
                        lecturas[j].observaciones.append(MOTIVO_TIEMPO_AGOTADO)
                return
            self._leer_region(lecturas[i], caras[i][0], region, limite)
            if self.config.corte_temprano and self._completo(lecturas):
                return

    def _leer_secundarias(self, caras, lecturas: list[LecturaCara], limite: float | None) -> None:
        """Nombre y gramaje (no deciden el destino): una sola lectura de la región más confiable."""
        for etiqueta in ("nombre", "gramaje"):
            candidatas = [(i, r) for i, (_, regs) in enumerate(caras) for r in regs if r.etiqueta == etiqueta]
            if not candidatas or self._sin_tiempo(limite):
                continue
            i, region = max(candidatas, key=lambda t: t[1].confianza)
            self._leer_region(lecturas[i], caras[i][0], region, limite)

    def _completo(self, lecturas: list[LecturaCara]) -> bool:
        c = validacion.consolidar(lecturas, self.hoy, self.config)
        return not c["errores"] and c["lote"] is not None and c["vencimiento"] is not None

    @staticmethod
    def _valido(etiqueta: str, texto: str) -> bool:
        if etiqueta == "lote":
            return parseo.lote_de_region(texto) is not None
        return parseo.parsear_fecha(texto, tolerante=True) is not None

    def _reintentar(self, recorte: Image.Image, etiqueta: str, texto: str, conf: float,
                    limite: float | None) -> tuple[str, float]:
        """Texto troquelado/sin contraste: si la lectura directa no dio un valor válido, prueba las
        variantes del prefiltro (config.prefiltro_variantes) y se queda con la primera que sí lo da."""
        for nombre in self.config.prefiltro_variantes:
            if self._sin_tiempo(limite):
                break
            try:
                t, c = self.ocr.leer(prefiltro.VARIANTES[nombre](recorte), exhaustivo=True)
            except Exception:  # noqa: BLE001
                log.exception("prefiltro %s falló en región %s", nombre, etiqueta)
                continue
            if self._valido(etiqueta, t):
                return t, c
        return texto, conf

    @staticmethod
    def _aplicar_texto(lectura: LecturaCara, etiqueta: str, texto: str, conf: float) -> None:
        if etiqueta == "lote":
            lectura.lote, lectura.confianza_lote = parseo.lote_de_region(texto), conf
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
