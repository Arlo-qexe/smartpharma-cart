"""Módulo de reconocimiento de SmartPharma Cart (OCR + validación + FEFO)."""
from .config import Config, cargar_config
from .pipeline import Reconocedor
from .tipos import ResultadoCaja

__all__ = ["Config", "cargar_config", "Reconocedor", "ResultadoCaja"]
