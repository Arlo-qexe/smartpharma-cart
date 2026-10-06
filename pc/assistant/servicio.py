"""
Servicio del asistente para el panel web (Fase 3): envuelve a `Assistant` con lo que
hace falta para usarlo desde varias peticiones HTTP, sin conocer HTTP.

- **Una conversación y una solicitud a la vez:** llama.cpp no es concurrente. Una
  segunda solicitud mientras hay una en curso lanza `AsistenteOcupado`.
- **Propuestas con identificador:** una acción que pide confirmación queda como
  "pendiente" con un id. Confirmar o cancelar con un id viejo lanza
  `PropuestaInexistente`, así una confirmación atrasada nunca ejecuta nada. Si el
  operador escribe otra cosa con una propuesta pendiente, esa propuesta se cancela.
- El panel funciona igual sin esto: el servicio es opcional (ver
  `crear_servicio_desde_entorno`).

Configuración por entorno al arrancar `src/server.py`:
    ASISTENTE_MODELO=/ruta/modelo.gguf   modelo real (llama-cpp-python)
    ASISTENTE_MODO=prueba                sin modelo: reglas fijas (llm_prueba.py)
    ASISTENTE_GPU_LAYERS=-1              capas en GPU con modelo real (0 = solo CPU)
Sin ninguna de las dos, el asistente queda desactivado.
"""
import itertools
import logging
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from asistente_api import (  # noqa: E402
    MAX_TEXTO_CARACTERES,
    AsistenteOcupado,
    PropuestaInexistente,
    TextoInvalido,
)

from assistant import Assistant, Proposal, make_docs_retriever  # noqa: E402
from comandos_panel import registrar_comandos_panel, resumen_estado  # noqa: E402
from llm_prueba import LLMDePrueba  # noqa: E402

log = logging.getLogger("asistente")

CONTEXTO_MD = Path(__file__).resolve().parent / "ASSISTANT_CONTEXT.md"


class ServicioAsistente:
    def __init__(self, bot: Assistant, estado, modo: str):
        self.bot = bot
        self.estado = estado
        self.modo = modo
        self._lock = threading.Lock()
        self._pendiente = None            # (id, Proposal)
        self._ids = itertools.count(1)

    @contextmanager
    def _turno(self):
        if not self._lock.acquire(blocking=False):
            raise AsistenteOcupado()
        try:
            yield
        finally:
            self._lock.release()

    def _resultado(self, salida) -> dict:
        if isinstance(salida, Proposal):
            pid = next(self._ids)
            self._pendiente = (pid, salida)
            return {"tipo": "propuesta", "id": pid, "resumen": salida.summary}
        return {"tipo": "respuesta", "texto": salida.text}

    def _tomar_pendiente(self, pid) -> Proposal:
        if self._pendiente is None or self._pendiente[0] != pid:
            raise PropuestaInexistente()
        propuesta = self._pendiente[1]
        self._pendiente = None
        return propuesta

    def mensaje(self, texto) -> dict:
        texto = texto.strip() if isinstance(texto, str) else ""
        if not texto or len(texto) > MAX_TEXTO_CARACTERES:
            raise TextoInvalido()
        with self._turno():
            if self._pendiente is not None:          # el operador siguió con otra cosa
                self.bot.cancel(self._pendiente[1])
                self._pendiente = None
            log.info("Mensaje del operador (%d caracteres)", len(texto))
            return self._resultado(self.bot.handle(texto, resumen_estado(self.estado)))

    def confirmar(self, pid) -> dict:
        with self._turno():
            propuesta = self._tomar_pendiente(pid)
            log.info("El operador confirmó %s", propuesta.name)
            return self._resultado(self.bot.confirm(propuesta, resumen_estado(self.estado)))

    def cancelar(self, pid) -> dict:
        with self._turno():
            propuesta = self._tomar_pendiente(pid)
            log.info("El operador canceló %s", propuesta.name)
            return self._resultado(self.bot.cancel(propuesta))

    def reiniciar(self) -> None:
        with self._turno():
            self._pendiente = None
            self.bot.history.clear()


def crear_servicio_desde_entorno(estado):
    """Devuelve un ServicioAsistente según el entorno, o None si está desactivado.
    Puede lanzar si el modelo no carga: el llamador decide qué hacer (server.py lo
    registra y sigue sin asistente)."""
    modelo = os.environ.get("ASISTENTE_MODELO")
    prueba = os.environ.get("ASISTENTE_MODO", "").lower() == "prueba"
    if not modelo and not prueba:
        return None
    registrar_comandos_panel(estado)
    docs = make_docs_retriever(str(CONTEXTO_MD))
    if modelo:
        bot = Assistant(
            model_path=modelo,
            n_gpu_layers=int(os.environ.get("ASISTENTE_GPU_LAYERS", "-1")),
            docs_retriever=docs,
        )
        return ServicioAsistente(bot, estado, "modelo")
    return ServicioAsistente(Assistant(llm=LLMDePrueba(), docs_retriever=docs), estado, "prueba")
