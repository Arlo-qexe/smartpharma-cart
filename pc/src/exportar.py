"""
Exportación a CSV del inventario y del registro de eventos (PC-23, parte E).

Formato pensado para abrirse con Excel en español: UTF-8 con BOM (para las tildes) y «;» como separador.
Cualquier celda que empiece por = + - @ (o tabulador/retorno) se antepone con una comilla simple: lo que
llega del OCR o de un nombre escrito a mano NO debe poder ejecutarse como fórmula al abrir el archivo.
"""
import csv
import io
import json
from datetime import datetime

SEPARADOR = ";"
_PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")

COLUMNAS_INVENTARIO = (
    ("id", "Caja"), ("fecha", "Auditada"), ("captura_id", "Lote de fotos"), ("arranque", "Arranque"),
    ("origen", "Origen"), ("n_imagenes", "Imágenes"), ("resultado", "Resultado del reconocimiento"),
    ("destino", "Destino"), ("destino_por", "Destino fijado por"), ("turno_de", "Auxiliar"),
    ("medicamento", "Medicamento"), ("gramaje", "Gramaje"), ("numero_lote", "Lote del medicamento"),
    ("vencimiento", "Vencimiento"), ("dias_para_vencer", "Días para vencer"), ("confianza", "Confianza OCR"),
    ("motivo_ocr", "Motivo de revisión"), ("datos_origen", "Origen de los datos"), ("editada", "Editada"),
    ("eliminada", "Eliminada"), ("notas", "Notas"),
)
COLUMNAS_REGISTRO = (
    ("id", "#"), ("fecha", "Fecha y hora"), ("tipo", "Evento"), ("responsable", "Responsable"),
    ("detalle", "Detalle (JSON)"), ("hash", "Sello (hash)"),
)


def fecha_local(ms) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ms else ""


def _celda(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    texto = str(valor).replace("\r\n", "\n")
    return "'" + texto if texto.startswith(_PELIGROSOS) else texto


def a_csv(columnas, filas) -> bytes:
    """`columnas`: ((clave, encabezado), ...); `filas`: dicts. Devuelve los bytes UTF-8 con BOM."""
    salida = io.StringIO(newline="")
    w = csv.writer(salida, delimiter=SEPARADOR, lineterminator="\r\n")
    w.writerow([_celda(t) for _, t in columnas])
    for f in filas:
        w.writerow([_celda(f.get(k)) for k, _ in columnas])
    return salida.getvalue().encode("utf-8-sig")


def inventario_a_csv(cajas: list) -> bytes:
    return a_csv(COLUMNAS_INVENTARIO, ({**c, "fecha": fecha_local(c["creada_ms"])} for c in cajas))


def registro_a_csv(eventos: list) -> bytes:
    return a_csv(COLUMNAS_REGISTRO, (
        {**e, "fecha": fecha_local(e["ts_ms"]),
         "detalle": json.dumps(e["detalle"], ensure_ascii=False, separators=(",", ":"))} for e in eventos))
