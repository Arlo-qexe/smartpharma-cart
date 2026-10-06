"""
Configuración de logging de la PC. Cada módulo usa su propio logger (`server`,
`decision`, `panel`, `mdns`, `alarma`) y solo `server.py` llama a
`configurar_logging()` al arrancar; así las pruebas y los demás módulos no
imponen un formato.

Nivel por entorno: PC_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR (por defecto INFO).
"""
import logging
import os
import sys


def configurar_logging():
    nivel = getattr(logging, os.environ.get("PC_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=nivel,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
