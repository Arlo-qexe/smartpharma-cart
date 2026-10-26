"""
Contrato de entrada/salida con el módulo de reconocimiento (OCR + votación FEFO), que
desarrolla el equipo de IA (ver docs/arquitectura_comunicacion.md, sección 7).

Dos modos, elegidos con la variable de entorno PC_RECONOCIMIENTO:

  placeholder (por defecto)  clasifica AL AZAR entre TIPOS_PLACEHOLDER y no lee ningún dato. Sirve
                             para probar el transporte y la parte física sin el módulo de IA.
  ia                         usa el módulo de IA de `IA/` EN ESTE PROCESO (la PC lo ejecuta; nada
                             viaja por el cable a la Orange Pi): detector YOLO (`IA_MODELO`,
                             por defecto IA/modelos/v1_celular/best.onnx) + OCR + validación + FEFO.
                             Devuelve el destino Y lo que leyó (medicamento, gramaje, lote del
                             medicamento, vencimiento, confianza), que llena el inventario SIN pedir
                             confirmación; una persona puede corregirlo después (queda en el registro).

Ojo con el modo `ia`: hoy los rangos FEFO de IA/config/reconocimiento.json son PLACEHOLDER
(vencido -> DESCARTE, <=90 d -> TIPO_A, <=180 d -> TIPO_B, resto -> TIPO_C): el carrito
clasificará con ellos hasta que el equipo los confirme.

Contrato vigente (no cambia): `procesar_lote_ocr(imagenes: list[bytes]) -> str`, el destino o
RESULTADO_ERROR_REVISION_MANUAL. `reconocer_lote()` es la versión detallada que usa el servidor.
"""
import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import RESULTADO_ERROR_REVISION_MANUAL  # noqa: E402

log = logging.getLogger("reconocimiento")

MODO_PLACEHOLDER = "placeholder"
MODO_IA = "ia"
MODOS = (MODO_PLACEHOLDER, MODO_IA)

# Tipos que devuelve el placeholder, al azar, para poder ver en el panel y en el carrito
# varios destinos distintos mientras el equipo de IA no entregue el reconocimiento.
TIPOS_PLACEHOLDER = ("TIPO_A", "TIPO_B", "TIPO_C")

_RAIZ_REPO = Path(__file__).resolve().parents[2]
CARPETA_IA = _RAIZ_REPO / "IA"
MODELO_DEFECTO = CARPETA_IA / "modelos" / "v1_celular" / "best.onnx"

# Un lote a la vez: el flujo es secuencial, pero si la Orange Pi reintenta un lote lento no deben
# correr dos reconocimientos a la vez (compartirían el motor de OCR y el detector).
_candado = threading.Lock()
_ia = None            # módulo smartpharma_ia.adaptador_pc, importado la primera vez que se necesita


class ReconocimientoNoDisponible(RuntimeError):
    """Se pidió el modo `ia` y el módulo no puede arrancar (faltan pesos, paquetes, etc.)."""


@dataclass(frozen=True)
class ResultadoReconocimiento:
    clasificacion: str                       # destino (TIPO_X, DESCARTE...) o RESULTADO_ERROR_REVISION_MANUAL
    motivo: str | None = None                # por qué fue a revisión manual (códigos de la IA, separados por ";")
    medicamento: str | None = None
    gramaje: str | None = None
    numero_lote: str | None = None           # lote del MEDICAMENTO impreso en la caja
    vencimiento: str | None = None           # AAAA-MM-DD
    confianza: float | None = None           # 0-1, mínima entre los campos obligatorios aceptados
    origen: str = MODO_PLACEHOLDER           # qué modo lo produjo: "placeholder" | "ia"
    segundos: float | None = None            # lo que tardó el reconocimiento

    @property
    def tiene_datos(self) -> bool:
        return any((self.medicamento, self.gramaje, self.numero_lote, self.vencimiento))


def modo_reconocimiento() -> str:
    """El modo pedido en PC_RECONOCIMIENTO (placeholder si no se define). Un valor desconocido es un
    error: mejor no arrancar que clasificar al azar creyendo que se está leyendo."""
    modo = os.environ.get("PC_RECONOCIMIENTO", "").strip().lower() or MODO_PLACEHOLDER
    if modo not in MODOS:
        raise ReconocimientoNoDisponible(
            f"PC_RECONOCIMIENTO={modo!r} no es válido (use {' o '.join(MODOS)})")
    return modo


def _adaptador():
    """Importa el adaptador del módulo de IA (IA/smartpharma_ia) la primera vez."""
    global _ia
    if _ia is None:
        if not CARPETA_IA.is_dir():
            raise ReconocimientoNoDisponible(f"no existe la carpeta del módulo de IA: {CARPETA_IA}")
        if str(CARPETA_IA) not in sys.path:
            sys.path.append(str(CARPETA_IA))      # al final: no debe tapar nada de pc/
        # Ultralytics (el detector) por defecto instala paquetes que le faltan Y envía eventos de uso:
        # un servidor de producción no debe hacer ninguna de las dos cosas. Se pueden sobrescribir.
        os.environ.setdefault("YOLO_AUTOINSTALL", "False")
        os.environ.setdefault("YOLO_OFFLINE", "True")
        os.environ.setdefault("YOLO_VERBOSE", "False")
        try:
            from smartpharma_ia import adaptador_pc
        except ImportError as e:
            raise ReconocimientoNoDisponible(
                f"no se pudo importar el módulo de IA ({e}). Instala sus paquetes con "
                "`IA=1 bash scripts/setup_venv.sh` o `pip install -r requirements-ia.txt`") from e
        _ia = adaptador_pc
    return _ia


def _configurar_pesos() -> str | None:
    """Ruta de los pesos del detector que usará la IA (IA_MODELO). Sin definir, el de por defecto;
    definida y vacía, SIN detector (lee la foto completa: menos fiable). Devuelve la ruta o None."""
    definido = os.environ.get("IA_MODELO")
    if definido is None:
        ruta = MODELO_DEFECTO
        if not ruta.is_file():
            raise ReconocimientoNoDisponible(
                f"faltan los pesos del detector: {ruta}. Defínelos con IA_MODELO=/ruta/best.onnx, "
                "o usa IA_MODELO= (vacío) para leer la foto completa sin detector, "
                "o PC_RECONOCIMIENTO=placeholder")
        os.environ["IA_MODELO"] = str(ruta)
        return str(ruta)
    if definido.strip() == "":
        os.environ.pop("IA_MODELO", None)         # el adaptador usa DetectorImagenCompleta si no hay variable
        return None
    if not Path(definido).is_file():
        raise ReconocimientoNoDisponible(f"IA_MODELO={definido!r} no existe")
    return definido


def _jpeg_en_blanco() -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (255, 255, 255)).save(buf, "JPEG")
    return buf.getvalue()


def preparar() -> str:
    """Se llama UNA vez al arrancar el servidor. En modo `ia` carga el detector y el OCR y hace una
    lectura de prueba: así el primer lote real no paga la carga (la Orange Pi solo espera unos
    segundos y reenviaría el lote). Devuelve una descripción para el banner. Lanza
    ReconocimientoNoDisponible si no se puede arrancar."""
    modo = modo_reconocimiento()
    if modo == MODO_PLACEHOLDER:
        return "placeholder (clasifica al azar entre TIPO_A, TIPO_B y TIPO_C; no lee datos)"
    pesos = _configurar_pesos()
    adaptador = _adaptador()
    t0 = time.monotonic()
    try:
        with _candado:
            adaptador.procesar_lote_detallado([_jpeg_en_blanco()])
    except ReconocimientoNoDisponible:
        raise
    except Exception as e:
        raise ReconocimientoNoDisponible(f"el módulo de IA no arrancó: {type(e).__name__}: {e}") from e
    segundos = time.monotonic() - t0
    log.info("Módulo de IA cargado en %.1f s", segundos)
    return f"ia ({Path(pesos).name if pesos else 'sin detector: foto completa'}), cargado en {segundos:.1f} s"


def _desde_ia(r, segundos: float) -> ResultadoReconocimiento:
    """Convierte el ResultadoCaja de la IA al formato de la PC."""
    ok = not r.requiere_revision
    return ResultadoReconocimiento(
        clasificacion=r.clasificacion, motivo=r.motivo,
        medicamento=r.nombre, gramaje=r.gramaje, numero_lote=r.lote,
        vencimiento=r.vencimiento.isoformat() if r.vencimiento else None,
        confianza=round(r.confianza, 4) if ok and r.confianza else None,
        origen=MODO_IA, segundos=round(segundos, 2))


def reconocer_lote(imagenes: list) -> ResultadoReconocimiento:
    """Reconoce un lote de fotos (JPEG en memoria): destino y datos leídos. Nunca lanza una
    excepción hacia el servidor: cualquier fallo es ERROR_REVISION_MANUAL con su motivo."""
    if not imagenes:
        return ResultadoReconocimiento(RESULTADO_ERROR_REVISION_MANUAL, motivo="lote_vacio",
                                       origen=modo_reconocimiento())
    if modo_reconocimiento() == MODO_PLACEHOLDER:
        print(f"[ocr_interface] PLACEHOLDER: simulando reconocimiento sobre {len(imagenes)} imágenes")
        return ResultadoReconocimiento(random.choice(TIPOS_PLACEHOLDER))

    t0 = time.monotonic()
    try:
        with _candado:
            espera = time.monotonic() - t0
            if espera > 0.5:
                log.warning("el lote esperó %.1f s a que terminara otro reconocimiento", espera)
            resultado = _adaptador().procesar_lote_detallado(imagenes)
    except Exception:
        log.exception("fallo inesperado en el reconocimiento: el lote va a revisión manual")
        return ResultadoReconocimiento(RESULTADO_ERROR_REVISION_MANUAL, motivo="fallo_reconocimiento",
                                       origen=MODO_IA, segundos=round(time.monotonic() - t0, 2))
    r = _desde_ia(resultado, time.monotonic() - t0)
    log.info("Reconocimiento en %.1f s: %s%s", r.segundos, r.clasificacion,
             f" (revisión manual: {r.motivo})" if r.motivo else
             f" · lote {r.numero_lote or '—'} · vence {r.vencimiento or '—'} · confianza {r.confianza}")
    return r


def procesar_lote_ocr(imagenes: list) -> str:
    """CONTRATO VIGENTE (no modificar la firma sin actualizar shared/ y avisar al equipo de IA):

    Entrada: imagenes: list[bytes] — JPEGs de las 5 caras del objeto, en memoria.
    Salida: str — el destino (p. ej. "TIPO_A") o RESULTADO_ERROR_REVISION_MANUAL si no hay consenso
    suficiente o si `imagenes` está vacía.
    """
    return reconocer_lote(imagenes).clasificacion
