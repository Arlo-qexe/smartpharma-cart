# Entrega: identificación y control de los motores del SPM (estado al 2026-11-05)

Plato inclinable del carro SmartPharma = manipulador esférico paralelo (SPM) con 3 motorreductores JGA25-370 (12 V nominal, 60 rpm, reducción 103:1),
ESP32-S3-N16R8 + 2 × TB6612FNG. Todo con los motores LIBRES (sin el mecanismo) y alimentados a 9,6 V.

## Qué hay hecho
| Motor | Identificado | Ganancias sintonizadas | Probado en lazo cerrado en el motor real |
|---|---|---|---|
| 1 | sí | sí | sí (escalones de 75–90°, sin sobrepaso, error final 0,08–0,16°) |
| 2 | sí | sí (las del motor 1 con los parámetros del 2) | sí (igual de bien) |
| 3 | sí | sí (ídem) | sí (igual de bien) |

Cuentas por vuelta del eje de salida: **4532** (44 × 103), los tres motores. (Los CSV crudos del motor 3 dicen `ppr=4352`: fue un error de tecleo al
identificarlo; `modelo_motor3.json` ya está recalculado con 4532: `python herramientas/identificar_motor.py ajustar --carpeta identificacion --motor 3 --ppr 4532`.)

## Dónde está cada cosa
- `identificacion/modelo_motorN.json` — modelo identificado (K, τ, zona muerta u0, arranque/parada, pwm_min y kff sugeridos).
- `identificacion/ganancias_motorN.json` — órdenes `{"cfg":...}` para la placa (cascada P→PI, conservadoras; trae también las optimizadas).
  `ganancias_motor2_kpv_alto.json` = primera versión del motor 2 (kpv demasiado alto: vibraba; no usar).
- `identificacion/motorN_step_*.csv`, `motorN_tri_*.csv` — capturas crudas de la identificación (escalones y rampas triangulares).
- `identificacion/telemetria_motorN_*.csv` — pruebas de posición en lazo cerrado de cada motor.
- `identificacion/reproduccion_*.csv` — intentos del programa «4 caras + volcado» con los 3 motores (ver «Problema abierto»).
- `identificacion/_decaimiento_rapido_ppr1000/` — primera identificación del motor 1 con el firmware antiguo (decaimiento rápido; zona muerta 0,54). Solo histórico.
- `firmware/spm_esp32s3/spm_esp32s3.ino` — firmware (lazo 1 kHz, PID o cascada, captura para identificación, decaimiento lento del TB6612). `firmware/IDENTIFICACION.md` es la guía paso a paso (cableado, compilar/subir con arduino-cli en VS Code, interfaz, tabla de síntomas).
- `spm/identificacion.py`, `identificacion_serial.py`, `sintonia.py`, `reproduccion.py`, `placa_simulada.py` — ajuste del modelo, cliente de captura, sintonía por simulación, reproductor del programa, placa falsa para pruebas.
- `herramientas/interfaz_identificacion.py` — interfaz (diagnóstico, control de posición, identificación). `identificar_motor.py`, `sintonizar_motor.py`, `reproducir_programa.py` — versiones de línea de comandos.
- `tests/` — 103 pruebas (`python -m unittest discover -s tests`).

## Modelo identificado (9,6 V, motor libre, decaimiento lento)
| | Motor 1 | Motor 2 | Motor 3 |
|---|---|---|---|
| K (+/−) [°/s] | 274 / 267 | 264 / 275 | 278 / 272 |
| τ [ms] | 27 | 25 | 28 |
| u0 (+/−) | 0,041 / 0,030 | 0,026 / 0,027 | 0,048 / 0,044 |
Ganancias comunes (cascada): kpp 12, kpv 0,002411, kiv 0,121482, band 0,16; kffv 0,003833 / 0,003829 / 0,003826 y pwm_min 0,035 / 0,031 / 0,049 (motores 1/2/3). pwm_max operativo 0,7.

## Problema abierto (lo último que pasó)
Al reproducir el programa con los tres motores (`herramientas/reproducir_programa.py --puerto COMx --ensayo`), la placa corta por «atascado» ~1 s después de empezar: `u` llega a 700 ‰ y el eje no se mueve.
Probando cada motor solo (`--motores 1|2|3`) falla igual. Una prueba en lazo abierto al 50 % y al 100 %, ± y en los tres motores, dio **0 cuentas** con `u` aplicado = 500/1000 ‰.
Es decir, la placa manda PWM y no hay movimiento ni cuentas en ningún motor. Antes del cableado «de los tres a la vez» sí funcionaba cada motor por separado. Causa NO determinada. Pendiente:
medir VM–GND en cada módulo y A01–A02/B01–B02 con el multímetro mientras se aplica un 50 % (interfaz → manejo manual), revisar el botón de salida de la fuente, STBY/PWMx a 3V3 y los cables de encoder.
Al acabar mis pruebas la placa quedó con `ctrl` = lazo abierto (solo en RAM): se corrige con «Cargar ganancias sintonizadas» o reiniciando.

## Pendiente
- Resolver lo anterior y validar el programa con los tres motores libres; luego identificación y sintonía con el mecanismo acoplado (masa del plato+caja, par estático) — lo identificado vale solo para el motor libre.
- Comprobar el sentido de giro de cada motor respecto al modelo del plato (la polaridad solo garantiza «duty positivo ⇒ cuentas suben»).
- Medir la corriente de bloqueo (TB6612: 1,2 A continuos por canal; motor ~1,4 A parado según el vendedor).
- Cuenta guardada en la placa: ppr 4532 y pwm_max 0,7 (si se perdió: `{"cfg":"ppr","motor":N,"val":4532}`, `{"cfg":"pwm_max","motor":N,"val":0.7}`, `{"cmd":"save"}`).
