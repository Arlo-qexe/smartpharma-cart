"""Del texto crudo del OCR a campos: lote y fecha de vencimiento.

Supuestos (documentados en docs/DISENO.md, a confirmar con el equipo):
  - Fechas con día van en orden DÍA/MES/AÑO (convención colombiana).
  - Una fecha de solo mes/año vence el ÚLTIMO día de ese mes (práctica farmacéutica).
  - Sin región etiquetada "fecha", solo se acepta una fecha precedida de una
    palabra clave de vencimiento, para no confundirla con la de fabricación.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date

_MESES = {
    "ENE": 1, "ENERO": 1, "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRERO": 2, "FEBRUARY": 2,
    "MAR": 3, "MARZO": 3, "MARCH": 3,
    "ABR": 4, "ABRIL": 4, "APR": 4, "APRIL": 4,
    "MAY": 5, "MAYO": 5,
    "JUN": 6, "JUNIO": 6, "JUNE": 6,
    "JUL": 7, "JULIO": 7, "JULY": 7,
    "AGO": 8, "AGOSTO": 8, "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SET": 9, "SEPTIEMBRE": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTUBRE": 10, "OCTOBER": 10,
    "NOV": 11, "NOVIEMBRE": 11, "NOVEMBER": 11,
    "DIC": 12, "DICIEMBRE": 12, "DEC": 12, "DECEMBER": 12,
}

_SEP = r"[/\-. ]"
_CLAVE_VENC = (
    r"(?:\bF\.?\s?V\.?|\bVENC(?:E|IMIENTO)?\.?|\bVTO\.?|\bEXP(?:IRA|IRY)?\.?(?:\s?DATE)?"
    r"|\bCAD(?:UCIDAD)?(?![A-Z])\.?|\bUSE\s+ANTES\s+DE|\bUSAR\s+ANTES\s+DE|\bBEST\s+BEFORE"
    r"|\bUSE\s+BEFORE|\bUSE\s+BY"
    r"|\b[VU]\s?:|\bV\.(?=\s*\d))"   # "V:07/28" (a veces el OCR lee la V como U); "V." solo si sigue un número
)
_CLAVE_LOTE = r"(?:\bLOTE|\bLOT|\bBATCH|\bB\.?\s?N\.?|\bL\s?:)"

# Prefijo opcional tras LOTE: "NO." / "NO:" / "NO " / "N°" / "N." / "#". Como _normalizar pasa a
# mayúsculas y NFKC convierte "º" y "№" en "O", "Nº 123" llega como "NO 123". "NO" solo cuenta como
# prefijo si lo sigue un espacio o ":" (así un lote "NO123" no pierde sus letras).
# El (?!...) final evita que un lote más largo de lo que admite el patrón se corte y pase por otro.
_RE_LOTE = re.compile(
    _CLAVE_LOTE + r"\s*[:.\-]?\s*(?:N°|NO\.|NO(?=[\s:])|N\.|#)?\s*[:.\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]{2,14})(?![A-Z0-9\-/])"
)

# Patrones de fecha, de más específico a menos (el orden importa).
_RE_DMA = re.compile(r"(\d{1,2})" + _SEP + r"(\d{1,2})" + _SEP + r"(\d{4}|\d{2})\b")
_RE_AMD = re.compile(r"\b(\d{4})" + _SEP + r"(\d{1,2})\b")
_RE_MA = re.compile(r"\b(\d{1,2})(?:\s*[/\-.]\s*|\s)(\d{4}|\d{2})\b")  # admite "05 - 2027"
_RE_DMESA = re.compile(r"\b(\d{1,2})\s*[/\-. ]?\s*([A-Z]{3,10})\.?[/\-. ]*(\d{4}|\d{2})\b")  # "12 SEP 2027", "26 JUN 28"
_RE_NOMBRE_A = re.compile(r"\b([A-Z]{3,10})\.?" + r"[/\-. ]*" + r"(\d{4}|\d{2})\b")


def _normalizar(texto: str) -> str:
    """Mayúsculas y sin acentos; conserva saltos de línea como espacios."""
    # NFKC primero: el OCR a veces devuelve signos de ancho completo (por ejemplo "：" en vez de ":")
    texto = unicodedata.normalize("NFKC", texto)
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos.upper()).strip()


def _anio(a: str) -> int:
    n = int(a)
    return n + 2000 if n < 100 else n


def _fin_de_mes(anio: int, mes: int) -> date | None:
    if not (1 <= mes <= 12) or not (1900 <= anio <= 2200):
        return None
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def _mes_aproximado(token: str) -> int | None:
    """Mes por nombre tolerando 1 carácter mal leído (J0L -> JUL, 0CT -> OCT). None si hay ambigüedad."""
    tok = token.replace("0", "O")
    if tok in _MESES:
        return _MESES[tok]
    cand = {
        v for k, v in _MESES.items()
        if len(k) == len(tok) and sum(a != b for a, b in zip(k, tok)) == 1
    }
    return cand.pop() if len(cand) == 1 else None


def _fecha_tolerante(t: str) -> date | None:
    """Último recurso para texto de una región 'fecha' con separadores/dígitos mal leídos."""
    # DD/MES/AA con el 0 leído como D/O y la barra leída como dígito: "D6/0CT/28", "29/J0L727"
    if m := re.search(r"\b([0-9DO]{1,2})\W*([A-Z0]{3})\W*(\d{2,4})\b", t):
        dia = int(m[1].replace("D", "0").replace("O", "0"))
        mes = _mes_aproximado(m[2])
        d = m[3]
        anio = _anio(d[-2:] if len(d) == 3 else d)
        if mes and 2020 <= anio <= 2045:
            try:
                return date(anio, mes, dia)
            except ValueError:
                return _fin_de_mes(anio, mes)
    # Solo dígitos: "07728" (barra leída como dígito), "0727", "102027", "082027"
    d = re.sub(r"\D", "", t)
    if len(d) == 5:
        mes, anio = int(d[:2]), _anio(d[3:])
    elif len(d) == 7 and d[3:5] == "20":  # "0672027": la barra de 06/2027 leída como un dígito
        mes, anio = int(d[:2]), int(d[3:])
    elif len(d) == 4:
        mes, anio = int(d[:2]), _anio(d[2:])
    elif len(d) == 6 and d[2:4] == "20":
        mes, anio = int(d[:2]), int(d[2:])
    else:
        return None
    return _fin_de_mes(anio, mes) if 2020 <= anio <= 2045 else None


def parsear_fecha(texto: str, tolerante: bool = False) -> date | None:
    """Interpreta una fecha SUELTA (sin palabra clave). None si no es una fecha válida.

    tolerante=True: si no encaja ningún patrón, intenta corregir errores típicos del OCR
    (solo para texto de una región 'fecha', nunca para texto libre).
    """
    t = _normalizar(texto)
    if m := _RE_DMA.search(t):
        d, mes, a = int(m[1]), int(m[2]), _anio(m[3])
        try:
            return date(a, mes, d)
        except ValueError:
            if d != 0:
                return None  # "31/02/2027" es una fecha imposible: no se rescata un "02/2027" de adentro
            # día 0 = ruido del OCR delante de un mes/año ("0.03-2028"): se prueban los otros patrones
    for m in _RE_DMESA.finditer(t):  # con día y mes en letras: conserva el día
        mes = _MESES.get(m[2])
        if mes:
            try:
                return date(_anio(m[3]), mes, int(m[1]))
            except ValueError:
                return _fin_de_mes(_anio(m[3]), mes)
    if m := _RE_AMD.search(t):
        return _fin_de_mes(int(m[1]), int(m[2]))
    for i in range(len(t)):  # mes/año: se prueba desde cada posición ("0.03-2028" empieza con un "0." sobrante)
        if (m := _RE_MA.match(t, i)) and (f := _fin_de_mes(_anio(m[2]), int(m[1]))):
            return f
    for m in _RE_NOMBRE_A.finditer(t):
        mes = _MESES.get(m[1])
        if mes:
            return _fin_de_mes(_anio(m[2]), mes)
    return _fecha_tolerante(t) if tolerante else None


def buscar_vencimiento(texto: str) -> date | None:
    """Busca una fecha precedida de una palabra clave de vencimiento."""
    t = _normalizar(texto)
    for clave in re.finditer(_CLAVE_VENC, t):
        resto = t[clave.end():clave.end() + 24]
        resto = re.sub(r"^[\s:.\-]+", "", resto)
        f = parsear_fecha(resto)
        if f:
            return f
    return None


def limpiar_lote(texto: str) -> str | None:
    """Normaliza un texto que ya se sabe que es un lote (región etiquetada 'lote')."""
    t = _normalizar(texto)
    t = re.sub(r"^" + _CLAVE_LOTE + r"\s*(?:NO\.?|N°|#)?\s*[:.\-]?\s*", "", t)
    t = re.sub(r"^L\s*[:.]\s*", "", t)  # abreviatura "L:" / "L." (sin separador no se toca: "L123" es un lote)
    t = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", t)  # puntuación suelta en los bordes
    t = re.sub(r"\s+", "", t)
    return t or None


def lote_de_region(texto: str) -> str | None:
    """Lote de una región etiquetada 'lote'. Si el OCR devolvió varias líneas y ninguna lleva la palabra
    LOTE, no se sabe cuál es el lote (podría ser una fecha de fabricación, un registro sanitario...):
    mejor None (-> revisión manual) que unir las líneas y adivinar."""
    if sum(bool(l.strip()) for l in texto.splitlines()) > 1:
        return buscar_lote(texto)
    return limpiar_lote(texto)


def buscar_lote(texto: str) -> str | None:
    """Busca un lote precedido de LOTE/LOT/BATCH/B.N. en texto libre."""
    m = _RE_LOTE.search(_normalizar(texto))
    return m[1] if m else None
