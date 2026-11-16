#!/usr/bin/env python3
"""Generador reproducible de las trayectorias SmartPharma del SPM.

Lee datos/smartpharma_config.json (azimuts del entorno, offset de la base, inclinación de volcado, t_mov, t_estab, espera de DESCARTE_2...) y escribe:
  trayectorias_smartpharma/   trayectorias.json, puntos_de_paso.csv, muestras/*.csv, tabla_poses.md, graficas/*.png
  firmware/spm_esp32s3/spm_trayectorias.h   (tabla de tramos para el firmware; NO editar a mano)
Uso (desde simulador_spm):  python herramientas/generar_trayectorias_smartpharma.py [--config otro.json] [--descarte 2] [--sin-graficas]
Equivale a:  python -m spm smartpharma ...
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spm.smartpharma_salida import main

if __name__ == "__main__":
    raise SystemExit(main())
