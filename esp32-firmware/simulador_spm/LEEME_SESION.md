# Sesion 2026-11-05/06: simulador del SPM, interfaz y firmware (DRV8833) con PID en cascada

Contenido: copia completa de `simulador_spm` (simulador, interfaz, herramientas, pruebas, datos) con todos los cambios de la sesion. Sin cachés ni entorno virtual.
Entorno Python usado: ~/Documents/simulador_spm/.venv (numpy, matplotlib, tkinter, pyserial). Instalar con requirements.txt si se usa en otro equipo.

## Firmware de Arduino (ESP32-S3 N16R8)
- `firmware/spm_esp32s3/spm_esp32s3.ino`  -> puente H DRV8833 (no TB6612). Pines como #define al inicio:
  M1 enc 11/12, IN 9/10 | M2 enc 15/16, IN 17/18 | M3 enc 4/5, IN 6/7. DRIVER_DECAIMIENTO_LENTO = true. PIN_NSLEEP = PIN_NONE (EEP/nSLEEP en alto con el puente J1 del modulo).
- Respaldo del TB6612: `firmware/spm_esp32s3_tb6612.ino.bak`.
- Subir:  arduino-cli compile --upload -p /dev/ttyACM0 --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc firmware/spm_esp32s3   (core esp32 3.3.12)
- Configuracion guardada en la placa (cmd "save"): cascada en los 3 motores, pwm_max 0.9 / 1.0 / 0.9, 4532 cuentas/vuelta, enc_sign [1,-1,1].

## PID / ganancias (carpetas con JSON y CSV de las pruebas reales)
- `identificacion_drv8833_pwm09/`  ganancias en CASCADA vigentes (las guardadas en la placa) + CSV de la trayectoria de la caja a 60/100/130 °/s.
- `identificacion_drv8833_pid/`    PID de posicion solo (kp 0.10), para comparar. `identificacion_drv8833_b/` y `_c/`: modelos de planta y ganancias de la cascada (M2 en `_c`).
- `identificacion_drv8833/`, `_final/`, `_m2kick/`: corridas intermedias. `identificacion/`: modelos del TB6612 (originales de la entrega).

## Cambios en codigo
- `spm/motores_identificados.py` (nuevo) y comando `python -m spm caja_motores`: simula los 3 motores identificados con el lazo del firmware.
- `spm/trayectoria.py`: `programa_caja_reducida()`; `interfaz/pestana_trayectoria.py`: presets «Caja reducida 1-4» (caras cada 5/10/15/30°, volcado 4/6/8/10°).
- `datos/config.json` (nuevo): cuentas_por_vuelta_salida = 4532. Sin esto la pestaña Serial mandaba 1000 a la placa y el mecanismo se movia solo el 22 % de lo pedido.
- `tests/`: 109 pruebas OK (python -m unittest discover -s tests). Se ajusto test_interfaz para arrancar el emulador con los mismos pulsos/vuelta.

## Advertencias (nada de esto esta validado con el mecanismo completo bajo carga)
- Motor 2: punto duro en el motor/reductor; funciona solo con pwm_max 1.0. Pendiente cambiarlo.
- Con el mecanismo montado, el NNR/brazo de la pata 3 hizo contacto a ~-190° de motor (primera cara del programa de la caja): no repetir ese recorrido sin comprobar el tope.
- Relacion 37/17 y signo -1 de la transmision son SUPUESTOS (sentido de giro comprobado a ojo en las 3 patas).
- No se midio la corriente: con pwm_max 0.9-1.0 el DRV8833 (1.5 A continuos) tiene poco margen. Fuente ~10.1 V (maximo del modulo 10.8 V).
- Proceso: cerrar la interfaz antes de usar herramientas por linea de comandos (el puerto serie es de un solo programa).
