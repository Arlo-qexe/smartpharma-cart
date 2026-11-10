"""Ejemplo mínimo: orientación deseada -> ángulos de motor, trayectoria y seguimiento simulado.  Ejecutar desde simulador_spm/:  python ejemplos/ejemplo_basico.py"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spm import (SPM, cargar_config, desde_inclinacion, a_inclinacion, planificar, CaminoSlerp, seguir_trayectoria)

spm = SPM()                                  # geometría extraída del CAD
trans, lim = cargar_config()                 # relación de engranes y límites (ver datos/config.json o los valores por defecto)

# 1) cinemática inversa: ¿qué ángulos de motor dan una inclinación de 10° hacia el azimut 60° y 5° de torsión?
R = desde_inclinacion(*np.radians([10, 60, 5]))
theta = spm.ik(R)                            # ángulos de los engranes NNR1..3 (rad)
print("NNR   (°):", np.degrees(theta).round(2))
print("motor (°):", np.degrees(trans.a_motor(theta)).round(2))
print("¿dentro de límites?:", lim.valida(spm, R, theta))

# 2) cinemática directa: leer los motores y estimar la orientación de la plataforma
R_est = spm.fk(trans.a_nnr(trans.a_motor(theta)))
t, az, tor = a_inclinacion(R_est)
print("estimada: inclinación %.2f°, azimut %.1f°, torsión %.2f°" % (math.degrees(t), math.degrees(az), math.degrees(tor)))

# 3) trayectoria suave de la pose de ensamblaje a esa orientación (el tiempo se alarga solo si los motores no llegan)
tray = planificar(spm, trans, lim, CaminoSlerp([np.eye(3), R]), T=2.0)
print("duración %.2f s (x%.2f), pico de velocidad de motor %.1f rpm" % (tray.duracion, tray.factor_tiempo, np.abs(tray.motor_vel).max() * 60 / (2 * math.pi)))

# 4) lazo de control simulado: motores con retardo, saturación y encoder cuantizado
reg = seguir_trayectoria(spm, trans, lim, tray)
print("error de orientación del seguimiento: máx %.2f°" % reg.error_deg.max())
