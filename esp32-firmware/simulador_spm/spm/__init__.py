"""Simulador cinemático del manipulador esférico paralelo (SPM) de 3 patas."""
from .geometria import Geometria
from .cinematica import SPM, FueraDeEspacioDeTrabajo
from .transmision import Transmision, Limites, cargar_config
from .rot import desde_inclinacion, a_inclinacion
from .trayectoria import (planificar, planificar_con_paradas, planificar_articular, planificar_jacobiano, a_csv, desde_csv, expandir_esperas, inclinacion_maxima, programa_caja, programa_caja_reducida, programa_oscilacion_motores, CaminoSlerp, CaminoPrecesion, CaminoInclinacion, Trayectoria)
from .control import seguir_trayectoria, MotoresSimulados, InterfazMotores

__all__ = ["Geometria", "SPM", "FueraDeEspacioDeTrabajo", "Transmision", "Limites", "cargar_config", "desde_inclinacion", "a_inclinacion",
           "planificar", "planificar_con_paradas", "planificar_articular", "planificar_jacobiano", "a_csv", "desde_csv", "expandir_esperas", "inclinacion_maxima", "programa_caja", "programa_caja_reducida", "programa_oscilacion_motores", "CaminoSlerp", "CaminoPrecesion", "CaminoInclinacion", "Trayectoria",
           "seguir_trayectoria", "MotoresSimulados", "InterfazMotores"]
