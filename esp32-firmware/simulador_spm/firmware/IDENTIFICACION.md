# Identificación de los motores del SPM (JGA25-370 12 V, 60 rpm)

> **CAMBIO DE DRIVER (2026-11-05): TB6612FNG → DRV8833.** Los pines de los 3 motores del SPM **no cambian** (M1 enc 11/12, IN 9/10; M2 enc 15/16, IN 17/18; M3 enc 4/5, IN 6/7).
> La sección 1 de abajo describe el TB6612FNG y queda **obsoleta** en lo eléctrico; lo vigente es:
> - Cada canal del DRV8833 tiene solo `xIN1` (← pwmF) y `xIN2` (← pwmR); **no hay PWMx ni STBY**. `nSLEEP` a 3V3 (o a un GPIO: `PIN_NSLEEP`). `nFAULT` sin usar.
> - Motor: cable rojo → `xOUT1`, blanco → `xOUT2`. 2 módulos: A-canal A = M1, A-canal B = M2, B-canal A = M3. GND común.
> - **VM máximo del DRV8833 = 10.8 V** (no conectar 12 V). 1.5 A continuos por puente (2 A pico), mejor que el TB6612 (1.2 A).
> - La lógica de PWM no cambió (decaimiento lento: (1,0) mueve, (1,1) frena, (0,0) libre), por eso los modelos identificados siguen siendo una buena aproximación; **conviene repetir la identificación con el DRV8833** (otra caída de tensión del puente).
> - El «0 cuentas» en lazo abierto con los 3 motores cableados (ver LEEME_ENTREGA.md) era con el TB6612: los pines PWMx/STBY sueltos son una causa probable; el DRV8833 los elimina.

**Para qué:** hoy el firmware no conoce tus motores (sus ganancias son marcadores de posición). Esta guía mide, para cada motor, un modelo
`τ·ω̇ + ω = K·(u − u0·signo(u))` (ganancia `K`, constante de tiempo `τ`, zona muerta `u0`, ciclo de trabajo de arranque). Con ese modelo se
simula el lazo y se sintoniza PID de posición o cascada **con datos**, en vez de a ciegas.

**Estado de lo que se probó (sin la placa):** el firmware con el modo de captura se compiló y probó en el PC (`test_host`, 104 comprobaciones), y el
ajuste recupera los parámetros de una planta simulada con ~1 % de error (K 899 vs 900, τ 59 vs 60 ms, u0 0.080 vs 0.080). **No se ha ejecutado en
la ESP32-S3 ni con tus motores**: la capa de hardware (PCNT, LEDC, pines del TB6612FNG) compila en el PC solo con ganchos simulados.

## 1. Eléctrica: antes de energizar (TB6612FNG)

- **Un TB6612FNG da 1.2 A continuos por canal (3.2 A pico).** La publicación del motor de Zamux (la de 60 RPM, la variante que se usa ahora) dice ~1.4 A de corriente de bloqueo (≈ 8.6 Ω a 12 V) y ~130 mA en vacío: **mídela** (motor bloqueado 1 s con `pwm_max` ≈ 0.3, amperímetro en serie en la
  alimentación del motor). Si pasa de ~1.2 A, el driver se sobrecalienta al sostener el plato: limita `pwm_max` (≤ 0.6) o usa otro driver.
- Con 3 motores hacen falta **2 módulos TB6612FNG** (cada uno tiene 2 canales).
- **Cableado que asume el firmware** (compruébalo contra tu placa; los pines están en la tabla `PINS` de `spm_esp32s3.ino`; en un N16R8 no uses GPIO 26–37):

  | Motor | Encoder A/B | IN1 / IN2 (reciben el PWM) |
  |---|---|---|
  | M1 | 11 / 12 | 9 / 10 |
  | M2 | 15 / 16 | 17 / 18 |
  | M3 | 4 / 5 | 6 / 7 |

  **Cables del motor JGA25-370** (diagrama del vendedor, verificado en la publicación; los seis cables tienen el mismo grosor, guíate por el color):

  | Cable | Función | Va a (motor 1) |
  |---|---|---|
  | Rojo | motor + | **A01** del TB6612 |
  | Blanco | motor − (**es de potencia: NO va a GND**) | **A02** (el orden solo invierte el sentido de giro) |
  | Azul | alimentación del encoder 3.3–5 V | **3V3** de la ESP32 (nunca 12 V ni 5 V) |
  | Negro | GND del encoder | GND común |
  | Amarillo | señal A (11 pulsos/vuelta del eje del motor) | GPIO **11** |
  | Verde | señal B | GPIO **12** (A y B intercambiadas solo cambian el signo) |

  **Terminales del módulo TB6612FNG** (serigrafía del módulo, vista por abajo): izquierda PWMA, AIN2, AIN1, STBY, BIN1, BIN2, PWMB, GND · derecha VM, VCC, GND, A01, A02, B02, B01, GND.
  Motor 1: **AIN1 ← GPIO 9, AIN2 ← GPIO 10**; PWMA y STBY a 3V3; VCC a 3V3 de la ESP32; VM a +12 V; GND de los tres (fuente, módulo, ESP32) unidos.
  Comprobación sin energía: entre el cable rojo y el blanco, el multímetro debe dar unos 8–9 Ω (estimación a partir de 12 V / 1.4 A, no un dato del fabricante).

  El pin **PWMx** de cada canal y **STBY** deben estar a nivel alto: cablearlos a 3.3 V, o poner su GPIO en `PINS[i].en` / `PIN_STBY` y el firmware los sube.
  Con `INx1 = INx2 = 0` el puente queda libre (decaimiento rápido).
- Masa común (GND) entre la fuente de 12 V, los drivers y la ESP32. Encoders Hall alimentados a 3.3 V (la ESP32 no tolera 5 V en sus entradas) con pull-up interno.
- **Paro de emergencia:** interruptor en la alimentación de 12 V de los motores, al alcance de la mano. `{"cmd":"stop"}` corta el PWM pero **el motor sigue por inercia** (no frena).

## 2. Cargar el firmware (VS Code + arduino-cli, sin Arduino IDE)

**Una sola vez** (ya hecho en este PC): `arduino-cli` en `C:\Users\<tú>\arduino-cli` y el core `esp32:esp32@3.3.12` (la versión con la que se probó; ocupa ~6.5 GB en
`C:\Users\<tú>\arduino-cli\data`, fuera de PlatformIO, que no se toca; **no** en `AppData`: la app de Claude escribe ahí en una carpeta privada que VS Code no ve, y por eso la tarea fallaba con «Platform 'esp32:esp32' not found»).
`arduino-cli` se usa siempre con `--config-file C:\Users\<tú>\arduino-cli\arduino-cli.yaml` (las tareas ya lo llevan). El firmware compila para ESP32-S3 N16R8 sin avisos con las dos opciones de puerto: 351 KB de flash (11 %), 63.5 KB de RAM (19 %).

Cada vez, en VS Code con la carpeta `simulador_spm` abierta: *Terminal → Ejecutar tarea…* y elige:

| Tarea | Para qué |
|---|---|
| `SPM: Compilar (puerto UART)` / `(USB nativo)` | solo compila (no necesita la placa) |
| `SPM: Compilar y SUBIR (puerto UART)` / `(USB nativo)` | compila y carga; te pide el COM (Administrador de dispositivos → Puertos) |
| `SPM: Monitor serie (115200)` | abre el monitor; escribe el JSON y Enter (envía salto de línea). Salir: `Ctrl+]` |

«UART» = puerto USB-C marcado UART/COM (chip CH340/CP210x); «USB nativo» = el marcado USB. Si al subir se queda en *Connecting…*, mantén pulsado **BOOT** de la placa.
**Cierra el monitor antes de subir** (el puerto no se comparte). Al terminar, pulsa RESET/EN con el monitor abierto: debe salir `{"info":"SPM ESP32-S3 listo"…}` y después líneas `{"t":…}`.

Sin VS Code, los mismos comandos: `arduino-cli --config-file C:\Users\<tú>\arduino-cli\arduino-cli.yaml compile --upload -p COM5 --fqbn esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=default firmware\spm_esp32s3`
y `python -m serial.tools.miniterm COM5 115200 --eol LF --echo` (requiere `pip install pyserial`).

**No uses PlatformIO para este firmware:** necesita arduino-esp32 3.x y el PlatformIO oficial trae la 2.x; instalar el fork pioarduino en el mismo PlatformIO rompe los otros proyectos.

### Interfaz gráfica de diagnóstico

En vez del monitor serie puedes usar la interfaz (tarea `SPM: Interfaz de diagnóstico e identificación`, o `python herramientas/interfaz_identificacion.py`; con `--simulado` no necesita placa y con `--falla sin_giro` reproduce «PWM aplicado pero el eje no gira»). Pestaña **Diagnóstico**: lecturas en vivo, test automático de la cadena en los dos sentidos con veredicto y lista de qué revisar, manejo manual, polaridad y cuentas por vuelta. Pestaña **Identificación**: ejecuta el plan y dibuja el modelo. Cierra el monitor serie antes de conectar. Usa tkinter + matplotlib (no PyQt).

### Modo de PWM del TB6612FNG (decaimiento lento o rápido)

`DRIVER_DECAIMIENTO_LENTO` en `spm_esp32s3.ino` (por defecto `true`). Con el modo rápido (el que traía el firmware) la tensión media en el motor es (2·duty − 1)·VM y por debajo de 0,5 de ciclo de trabajo casi no hay par:
la identificación del motor 1 (JGA25-370 de 60 rpm) dio una zona muerta de **0,544**. Con decaimiento lento la tensión media es duty·VM y se espera una zona muerta cercana a la fricción (~0,1): **esa
previsión se verifica repitiendo la identificación tras cargar este firmware**. No requiere recablear (usa las mismas entradas IN1/IN2, con PWMx y STBY a 3,3 V). Para BTS7960 ponerlo en `false`.
Cada modo da un modelo distinto: **identifica con el modo con el que vas a controlar.**

### Datos medidos del motor 1 (JGA25-370, 60 rpm)

Cuentas por vuelta de salida medidas: **4532** (= 44 × 103, reducción de 103:1). Primera identificación (decaimiento rápido; recalculada con ese ppr): K ≈ 581/567 °/s, τ ≈ 27 ms, zona muerta 0,544/0,541,
arranque 0,534/0,536, velocidad libre ≈ 287 °/s (≈ 48 rpm). Los CSV de esa corrida se capturaron con ppr = 1000 y se recalculan con
`python herramientas/identificar_motor.py ajustar --carpeta identificacion --motor 1 --ppr 4532`.

### Sintonía del lazo de posición (por simulación)

Con el modelo identificado, `python herramientas/sintonizar_motor.py --motor 1 --ppr 4532 --tope-vel 130 --pwm-max 0.7` simula el lazo del firmware (PID de posición y cascada) siguiendo el programa «4 caras + volcado»
y escribe `identificacion/ganancias_motorN.json` con las órdenes `{"cfg":...}`. En la interfaz: **Cargar ganancias sintonizadas** → **Ir a [°]** (y **Guardar CSV** para analizar la respuesta real).
Resultados con el motor 1 (SIMULACIÓN, motor libre, 9.6 V): error máximo de seguimiento 13,3° con los valores de fábrica del firmware contra ~0,2° con los sintonizados. La búsqueda tiende al borde del rango de
ganancia, así que la primera prueba real usa la versión **conservadora** (kpp 12 en cascada, kp ≤ 0,1 en PID). El simulador no incluye holguras, elasticidad ni ruido eléctrico: súbela poco a poco con el motor a la vista.
La tensión de alimentación importa: el modelo vale para la que había al identificar (VM = 9,6 V); con 12 V la ganancia K sube ~25 % (las ganancias se validaron con K ±25 %).

## 3. Polaridad y cuentas por vuelta (cada motor, **libre**: sin el mecanismo)

1. `{"cfg":"pwm_max","val":0.3}` y `{"cmd":"polarity","apply":1}`: cada motor debe dar `"ok"` (o corrige solo `enc_sign`). `sin_movimiento` = revisa STBY/PWMx, 12 V, cableado.
2. **Cuentas por vuelta** del eje de salida (todo el modelo en °/s depende de este número):

   ```bash
   python herramientas/identificar_motor.py ppr --puerto COM5 --motor 1 --aplicar
   ```
   Gira el eje de salida **una vuelta exacta** a mano. Con 11 pulsos por vuelta del motor (X4 = 44) y una reducción de ~100:1 (6000 rpm del motor / 60 rpm de salida) esperarías ≈ 4400 — es una estimación sin confirmar;
   manda lo medido. Repite para los 3 motores y `{"cmd":"save"}`; pon el mismo valor en «pulsos/vuelta» de la interfaz.

## 4. Identificación con el motor libre

Motor **sin el mecanismo**, 12 V encendida, mano en el interruptor, `pwm_max` ≥ 0.6 (`{"cfg":"pwm_max","motor":1,"val":0.6}`):

```bash
python herramientas/identificar_motor.py capturar --puerto COM5 --motor 1 --salida identificacion
```

Lo que hace (≈ 1 minuto por motor): `reset` → rampa triangular hasta +tope y hasta −tope (4 s cada una; halla a qué ciclo de trabajo arranca el eje en cada sentido) →
4 escalones por sentido por encima de esa zona muerta (500 ms) → ajuste del modelo. Guarda cada captura como `motorN_<modo>_<duty>.csv` y el resultado en `modelo_motorN.json`.
Cada captura corta sola si el eje gira más de `--max-deg` (720° por defecto en escalones; con el motor libre a 0.6 de ciclo de trabajo giran unos 250–300°). **Cortar el PWM no frena:** el eje sigue ~ω·τ más allá de ese límite.
`Ctrl+C` manda `stop`.

Ver el modelo otra vez sin la placa: `python herramientas/identificar_motor.py ajustar --carpeta identificacion --motor 1`.

## 5. Cómo leer el informe

```
Motor 1  (8 escalones, residuo 0.10° rms)
            sentido +      sentido -
  K [°/s]   899.2          899.2        ganancia: °/s por unidad de ciclo de trabajo
  τ [ms]    59.1           59.1         constante de tiempo efectiva
  u0        0.080          0.080        zona muerta cinética
  arranque  0.078          0.077        ciclo de trabajo al que el eje empieza a girar (estático)
  parada    0.079          0.079        al que se detiene al bajar
  sugerido para el firmware: pwm_min=0.077  kff=0.001205
```
(números de la planta simulada; los tuyos serán otros). Qué esperar y qué hacer si no:

| Síntoma | Qué significa | Qué hacer |
|---|---|---|
| `K ≤ 0` en un sentido | el encoder cuenta al revés del PWM | `{"cfg":"enc_sign","motor":N,"val":-1}` o `polarity` con `apply` |
| `K` muy distinta entre sentidos (> 40 %) | driver desequilibrado o carga asimétrica | revisar cableado; si es el mecanismo, es la gravedad |
| `R² < 0.98` | velocidad no lineal con el ciclo de trabajo | saturación o `pwm_max` mordiendo; baja el tope o revisa la fuente (caída de tensión con carga) |
| «ventana corta» | `τ` es largo respecto a la duración del escalón | `--ms 800` |
| residuo > ~2 cuentas (≈ 0.5°) | el modelo de 1.er orden no describe bien al motor | mándame los CSV: puede haber holgura, rebote o ruido de encoder |
| arranque ≫ `u0` | fricción estática mucho mayor que la cinética (típico de reductoras) | normal; `pwm_min` ≈ arranque |
| `el motor no gira ni con …` | sin energía, atasco, STBY/PWMx en bajo o `pwm_max` muy bajo | ver sección 1 |

`pwm_min` y `kff` sugeridos son **valores iniciales** (`{"cfg":"pwm_min","motor":N,"val":…}`). La sintonía de `kp/ki/kd` o de la cascada es el siguiente paso, con estos modelos.

## 6. Con el mecanismo acoplado (después, con mecánica)

El plato **cuelga de los motores**, así que con el mecanismo la carga cambia con la pose y el motor ya no es "libre": hay gravedad, rozamiento de las uniones y acoplamiento entre los tres motores.
Eso se identifica en una segunda pasada: `capturar … --hold --max-deg 10 --ms 300` (los otros dos sostienen su posición con el lazo normal y cada captura corta a los 10°). Antes hace falta de **mecánica**:
masa de la plataforma + caja, posición del centro de masa respecto a O y el par estático por pose, para comprobar que el motor tiene margen (sin esa cifra no sé si el JGA25-370 sostiene el plato con la caja inclinada a 25.5°).

## 7. Qué mandarme

La carpeta `identificacion/` (CSV + `modelo_motorN.json`) de cada motor, el `ppr` medido, la corriente de bloqueo medida y cualquier `AVISO` del informe.
