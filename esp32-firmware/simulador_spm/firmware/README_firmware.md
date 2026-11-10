# Firmware ESP32-S3 del SPM (`spm_esp32s3/spm_esp32s3.ino`)

Controla los **3 motores DC con encoder del mecanismo** y responde a la pestaña «Serial / ESP32» de la interfaz del simulador. Estructura tomada de `esp32_z_casc_PID_LGR_DB.ino` (PCNT + LEDC + timer de hardware + tareas FreeRTOS fijadas a núcleo) y pensada para convivir con `smartpharma-cart` (ver «Integración con smartpharma-cart»).

## Hardware usado

| Motor | Encoder A/B (PCNT) | PWM F/R (LEDC, 20 kHz, 10 bit) |
|---|---|---|
| M1 (NNR1) | GPIO 11 / 12 | 9 / 10 |
| M2 (NNR2) | GPIO 15 / 16 | 17 / 18 |
| M3 (NNR3) | GPIO 4 / 5 | 6 / 7 |

- **PWM F = RPWM** (sentido «positivo») y **PWM R = LPWM** del BTS7960; el primer pin de cada pareja del mapa se tomó como RPWM. Si queda al revés: `{"cfg":"pwm_sign","val":-1}`.
- Los BTS7960 necesitan **R_EN y L_EN a nivel alto** (no hay pin asignado en el mapa: cablearlos a 3.3/5 V).
- **TB6612FNG** (el driver que hay en el plato): `pwmF`/`pwmR` van a **IN1/IN2** de cada canal (el PWM entra por las entradas de dirección) y los pines **PWMx** y **STBY** a 3.3 V (o su GPIO en `PINS[i].en` / `PIN_STBY`). 1.2 A continuos por canal: con 3 motores hacen falta 2 módulos y hay que medir la corriente de bloqueo. Detalle en `IDENTIFICACION.md`, sección 1.
- Encoders con pull-up interno (`ENC_PULLUP`), filtro de glitch de 1 µs, decodificación X4 por hardware (las dos canales del PCNT cuentan los flancos de A y B).
- Motores 4 y 5, servo (8), UART2 (13/14), I²C (47/48), UART0 (43/44), USB (19/20), flash/PSRAM y pines de arranque no se tocan.

## Tiempo real (por qué el muestreo es limpio)

1. **Timer de hardware a 1 kHz** (`timerBegin/timerAlarm`) → la ISR solo hace `vTaskNotifyGiveFromISR`.
2. **Tarea de control** (núcleo 1, prioridad `configMAX_PRIORITIES-2`): lee los 3 PCNT, genera la referencia, corre el lazo y escribe el PWM. Mide el instante real de cada muestreo con `esp_timer` y **usa el periodo real** para la velocidad (con jitter del ±25 % la estimación sigue dentro del 6 %). Los ciclos perdidos se cuentan (`miss` en la telemetría) y el jitter máximo por segundo se reporta (`jit`, µs). Si el timer deja de disparar, los motores se apagan a los 50 ms.
3. **Desbordamiento del PCNT (16 bits)** resuelto por muestreo, sin ISR: entre dos lecturas el avance es minúsculo, así que de `{d, d±30000, d±60000}` se toma el de menor módulo (funciona tanto si el contador se reinicia a 0 como si salta al límite opuesto). La posición acumulada es de 32 bits.
4. **Comandos** y **telemetría** (20 Hz) en el núcleo 0, con mutex para el puerto serie y spinlocks cortos para el estado compartido.

## Control

- **Referencia**: consigna suelta → perfil con `vmax`/`amax` y frenado parabólico continuo (sin sobreimpulso); consignas seguidas a menos de 250 ms (lo que hace la interfaz al transmitir una trayectoria) → rampa lineal hasta cada consigna en ese intervalo: se reproduce la trayectoria exacta con un retraso de un periodo y sin escalones.
- **Controladores por motor** (`{"cfg":"ctrl","val":"pid"|"casc"|"open"}`):
  - `pid`: PID de posición + avance de velocidad `kff`, derivada sobre el error de velocidad, anti-windup condicional y zona de integración (±10°).
  - `casc`: P de posición → velocidad pedida → PI de velocidad con avance (`kpp`, `kpv`, `kiv`, `kffv`).
  - `open`: ciclo de trabajo fijo `{"duty":N,"val":-1..1}` para identificar la planta.
- Compensación de fricción estática (`pwm_min`), banda de parada (`band`), saturación (`pwm_max`) y límites de consigna (`limit`).
- **Falla de seguridad** (cada `fault_ms`, 500 ms): con esfuerzo alto sostenido, velocidad media **contraria** a la orden (polaridad invertida → realimentación positiva) o **sin movimiento** con error > 3° (atasco, motor sin energía, encoder desconectado) → todos los motores a 0 y `{"err":"fault"}`; se sale con `{"cmd":"reset"}`.

## Protocolo (una línea JSON por mensaje, ángulos del **eje del motor** en grados)

| PC → ESP32 | Efecto |
|---|---|
| `{"m1":a,"m2":b,"m3":c}` (subconjunto) · `{"motor":N,"pos":x}` | consigna(s) de posición |
| `{"cmd":"reset"}` | quita fallas, objetivo = posición actual (frena) |
| `{"cmd":"stop"}` / `{"cmd":"enable"}` | motores libres / reactivar |
| `{"zero":N}` · `{"zero":"all"}` | la posición actual pasa a ser 0 |
| `{"cmd":"polarity"}` (+`"apply":1`) | pulso corto en cada motor: dice si el encoder cuenta al revés (`invertir_encoder`) y con `apply` lo corrige |
| `{"cmd":"capture","motor":N,"duty":x,"ms":T,…}` | **captura para identificar la planta**: ciclo de trabajo en lazo abierto sobre un motor (`mode`: `step`, `ramp` 0→duty o `tri` 0→duty→0; `pre_ms`, `post_ms`, `dec`, `max_deg`, `hold`) mientras registra tiempo real, cuentas y ciclo de trabajo a 1 kHz (4000 muestras). Limitado por `pwm_max`; corta sola por `max_deg`, `stop`/`reset` o falla. Avisa con `{"ack":"capture_done"}` |
| `{"cmd":"dump"}` | envía la última captura: `{"cap":{…}}` + líneas `{"c":[t_us,pos,duty‰,…]}` + `{"ack":"dump"}` (la telemetría calla mientras tanto) |
| `{"cfg":"<nombre>","motor":N,"val":x}` | ajuste en vivo (`motor` omitido = los 3; `val` puede ser un arreglo de 3). Nombres: `ppr`, `enc_sign`, `pwm_sign`, `ctrl`, `kp`,`ki`,`kd`,`kff`, `kpp`,`kpv`,`kiv`,`kffv`, `vmax`,`amax`, `pwm_min`,`pwm_max`, `tol`,`band`,`limit`, `telem_hz`,`fault_ms` |
| `{"cmd":"save"}` / `load` / `defaults` / `status` | guardar en NVS, cargar, valores de fábrica, estado |

| ESP32 → PC | |
|---|---|
| `{"t":ms,"st":"run","m1":{"posRef":pulsos,"pos":pulsos,"ok":true,"u":‰,"e":°},"m2":…,"m3":…,"jit":µs,"miss":n}` | telemetría 20 Hz (la interfaz usa `posRef`, `pos`, `ok`; `posRef` = consigna) |
| `{"ack":…}` · `{"err":…}` · `{"info":…}` | confirmaciones, errores, información |

**Pulsos por vuelta (`ppr`)**: son las cuentas X4 en el eje de salida (p. ej. 11 pulsos × 4 × relación de la caja). Debe coincidir con el campo «pulsos/vuelta» de la interfaz; la interfaz lo manda sola al conectar y al pulsar «Aplicar pulsos/vuelta». El valor por defecto (1000) es un marcador.

## Puesta en marcha (en este orden, con el mecanismo libre y los motores a poca potencia)

1. Instalar *esp32 by Espressif* **3.x** en el IDE de Arduino, placa **ESP32S3 Dev Module**, 115200 baudios. Si `Serial` sale por el USB nativo, activar «USB CDC On Boot».
2. Cargar el sketch. Conectar desde la interfaz (pestaña Serial); debe aparecer `{"info":"SPM ESP32-S3 listo"…}` y la telemetría.
3. `{"cfg":"ppr","val":[…]}` con los pulsos reales (o desde la interfaz). Girar un eje a mano: `pos` cambia en la interfaz.
4. **Polaridad**: `{"cmd":"polarity"}` → cada motor debe dar `"ok"`; si da `invertir_encoder`, repetirlo con `"apply":1` (o `{"cfg":"enc_sign","motor":N,"val":-1}`). Si da `sin_movimiento`: revisar EN del BTS7960, alimentación y cableado.
5. **Antes de sintonizar, identifica los motores** (cuentas por vuelta, `K`, `τ`, zona muerta): `IDENTIFICACION.md` + `herramientas/identificar_motor.py`.
   Con `pwm_max` ≈ 0.3 (`{"cfg":"pwm_max","val":0.3}`) y movimientos pequeños (`{"motor":1,"pos":20}`), sintonizar:
   - Medir la velocidad libre: `{"cfg":"ctrl","motor":1,"val":"open"}` + `{"duty":1,"val":0.5}` y mirar cuánto avanza `pos` por segundo → `kff ≈ 0.5 / (°/s)`.
   - `ctrl = pid`: subir `kp` hasta que responda rápido sin oscilar; `kd` amortigua; `ki` pequeño solo para el error residual; `pwm_min` = el duty justo donde el eje empieza a moverse.
   - Probar también `casc` (más fácil de sintonizar si la velocidad es limpia).
   - `vmax`/`amax` limitan los movimientos sueltos (la trayectoria transmitida ya viene planificada).
6. `{"cmd":"save"}` guarda todo en la memoria no volátil (se carga solo al arrancar).
7. Subir `pwm_max` hacia 1.0 y comprobar con trayectorias reales desde la pestaña «Trayectoria» → «Transmitir al robot».

**Los valores por defecto (ganancias, `vmax`, `amax`, `pwm_max` = 0.6…) NO están sintonizados para vuestros motores**: son un punto de partida razonable para un motor de 12 V ~900 °/s a duty 1 (el del banco de pruebas).

## Pruebas en el PC (sin placa)

`firmware/test_host/` compila el mismo `.ino` con `-DHOST_TEST` y una planta de motor DC simulada (fricción estática, constante de tiempo, contador de 16 bits con reinicio, jitter):

```bash
cd firmware/test_host && g++ -std=c++17 -Wall -Wextra -DHOST_TEST -I. test_firmware.cpp -o test_firmware && ./test_firmware
# Windows sin g++: `pip install ziglang` y `python -m ziglang c++ -std=c++17 -DHOST_TEST -D_USE_MATH_DEFINES -I. test_firmware.cpp -o test_firmware.exe`
```

104 comprobaciones (75 del lazo, el JSON y la seguridad, más 29 de la captura de identificación: perfiles, dump, abortos por giro/`stop`/falla y errores de uso): JSON y comandos, desbordamiento del contador (ambos modos), perfil de referencia (límites de velocidad/aceleración, sin sobreimpulso), lazo cerrado PID y cascada (asentamiento, sobreimpulso, error final), streaming a 25 y 10 Hz, estimación de velocidad con jitter, detección de polaridad invertida / atasco / encoder muerto sin falsas fallas en movimientos grandes, comandos y formato de telemetría. `esp32_simulado.py` (la planta del lado de la interfaz) sigue sirviendo para probar la interfaz sin placa.

## Integración con `smartpharma-cart`

El repositorio define que la ESP32-S3 recibe órdenes de la Orange Pi por UART con **suma de verificación XOR** (`shared/protocol_constants.h`) y que los 3 motores del manipulador viven en `esp32-firmware/src/motores.*`. Este firmware habla el protocolo JSON de la *interfaz del simulador* (USB/UART0, sin checksum), pensado para puesta a punto, sintonía y pruebas. Cuando se integre con la Orange Pi, la parte reutilizable es el núcleo de control (secciones 6 de `spm_esp32s3.ino`: referencia, lazo, falla, desbordamiento del PCNT) y `handleLine`, que se puede llamar desde el despachador del UART2 con las órdenes `girar_posicion` / `clasificar` traducidas a consignas en grados, manteniendo la regla del repositorio de que el firmware **no reanuda nada por su cuenta**.

## Limitaciones

- Sin probar con motores reales: compilado para ESP32-S3 (ver abajo) y probado en el PC con una planta simulada, pero nunca ejecutado en la placa.
- Sin medición de corriente (la protección es por movimiento, no por corriente); un motor bloqueado con poca energía puede calentar el BTS7960 hasta que salte la falla (≤ 0.5 s).
- Un solo conjunto de ganancias por motor; no hay planificación de ganancias ni compensación de la carga (el mecanismo cuelga de los motores por su propio peso).
