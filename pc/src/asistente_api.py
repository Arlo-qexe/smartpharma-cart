"""
Contrato mínimo entre el panel web (`panel_web.py`) y el servicio del asistente
(`assistant/servicio.py`). Vive en `src/` y no depende de pydantic ni de
llama-cpp: así el panel funciona igual sin el asistente.

El panel espera un objeto con este formato (duck typing):

    modo: str                     "modelo" | "prueba"
    mensaje(texto) -> dict        el operador escribe algo
    confirmar(id) -> dict         el operador pulsó "Ejecutar" en una propuesta
    cancelar(id) -> dict          el operador pulsó "Cancelar"
    reiniciar() -> None           nueva conversación

Cada llamada devuelve uno de:
    {"tipo": "respuesta", "texto": "..."}
    {"tipo": "propuesta", "id": N, "resumen": "..."}   (espera confirmación)
"""

MAX_TEXTO_CARACTERES = 1000


class AsistenteOcupado(Exception):
    """Ya hay otra solicitud en curso (el modelo atiende de a una)."""


class PropuestaInexistente(Exception):
    """La propuesta no existe o ya no está vigente."""


class TextoInvalido(Exception):
    """El mensaje está vacío o es demasiado largo."""
