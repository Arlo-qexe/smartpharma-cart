"""Prefiltro para texto troquelado / en relieve (lote y fecha casi sin contraste).

El troquelado no tiene tinta: solo se ve por el relieve (sombras finas). El OCR lee mal una
imagen así; estas variantes realzan el contraste local y los bordes ANTES de leer. Se usa como
reintento (ver `Reconocedor`): primero se lee el recorte tal cual y solo si no se obtuvo un
valor válido se prueban las variantes, para no gastar el presupuesto de 5 s por caja.

Cada variante es `PIL.Image -> PIL.Image` RGB. Usa OpenCV si está instalado; si no, un
equivalente más simple con Pillow (Pillow es la única dependencia obligatoria del núcleo).
"""
from __future__ import annotations

from typing import Callable

from PIL import Image, ImageFilter, ImageOps

ALTO_MIN_PX = 96   # recortes más bajos que esto se amplían: el OCR pierde trazos finos en texto chico


def _ampliar(img: Image.Image) -> Image.Image:
    if img.height >= ALTO_MIN_PX:
        return img
    k = ALTO_MIN_PX / img.height
    return img.resize((max(1, round(img.width * k)), ALTO_MIN_PX), Image.LANCZOS)


def _cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except ImportError:
        return None


def contraste_local(img: Image.Image) -> Image.Image:
    """CLAHE sobre gris: sube el contraste por zonas (sirve con brillo desigual y tinta tenue)."""
    img = _ampliar(img.convert("RGB"))
    cv2 = _cv2()
    if cv2 is None:
        return ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=2).convert("RGB")
    import numpy as np
    gris = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    gris = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(gris)
    return Image.fromarray(cv2.cvtColor(gris, cv2.COLOR_GRAY2RGB))


def relieve(img: Image.Image) -> Image.Image:
    """Resalta el relieve: resta el fondo (desenfoque grande) y estira el residuo + CLAHE.
    El troquelado queda como trazo oscuro/claro sobre gris uniforme."""
    img = _ampliar(img.convert("RGB"))
    cv2 = _cv2()
    if cv2 is None:
        gris = ImageOps.grayscale(img)
        fondo = gris.filter(ImageFilter.GaussianBlur(max(3, img.height // 8)))
        from PIL import ImageChops
        dif = ImageChops.subtract(ImageChops.add(gris, Image.new("L", gris.size, 128)), fondo)
        return ImageOps.autocontrast(dif, cutoff=1).convert("RGB")
    import numpy as np
    gris = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY).astype("float32")
    sigma = max(3.0, img.height / 8)
    resid = gris - cv2.GaussianBlur(gris, (0, 0), sigma)
    resid = cv2.normalize(resid, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
    resid = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4)).apply(resid)
    return Image.fromarray(cv2.cvtColor(resid, cv2.COLOR_GRAY2RGB))


def nitidez(img: Image.Image) -> Image.Image:
    """Contraste local + máscara de enfoque (borde fino del grabado)."""
    base = contraste_local(img)
    return base.filter(ImageFilter.UnsharpMask(radius=2, percent=200, threshold=2))


# Medido con 62 recortes reales de lote y 62 de fecha (Arducam, GT de Roboflow): la lectura directa acierta
# 28/62 lotes y 29/62 fechas; con estas variantes como reintento sube a 31 y 32. Solo ampliar el recorte
# (sin filtro) EMPEORA (14 y 9 aciertos) y por eso no existe como variante.
# Orden = costo/utilidad esperados; el pipeline prueba hasta que una variante da un valor válido.
VARIANTES: dict[str, Callable[[Image.Image], Image.Image]] = {
    "contraste": contraste_local,
    "relieve": relieve,
    "nitidez": nitidez,
}
