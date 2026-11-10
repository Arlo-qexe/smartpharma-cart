# simulador_spm

Simulador cinemático en Python del manipulador esférico paralelo (SPM) de 3 patas de Arlo. Usa solo `numpy` (y `matplotlib`/`Pillow`/`ffmpeg` para gráficas y vídeo); no necesita FreeCAD abierto. Todo sale del CAD (`Ensamblaje2510_trabajo.FCStd`): los ejes cinemáticos en `datos/geometria.json`, las **mallas 3D** en `datos/malla_cuerpos.npz` y el **mapa de colisiones** en `datos/mapa_colision.npz`.

## Qué hace

| Módulo | Contenido |
|---|---|
| `spm/cinematica.py` | Cinemática **inversa** exacta (orientación → ángulos de los 3 engranes NNR), cinemática **directa** numérica (Newton), ángulos pasivos del Brazov, jacobianos y medidas de singularidad |
| `spm/transmision.py` | Relación de engranes motor↔NNR (37/17, signo −1), cuantización del encoder y `Limites` (inclinación máxima, velocidad/aceleración de motor, márgenes a singularidad) |
| `spm/trayectoria.py` | Caminos de orientación (SLERP entre poses, precesión, interpolación inclinación/azimut/torsión), perfiles quíntico y trapezoidal, conversión a ángulos de motor y alargado automático del tiempo para respetar límites |
| `spm/control.py` | Lazo de seguimiento: IK → consigna de motor (avance + P) → motor simulado → encoder → FK → error de orientación. `InterfazMotores` es el contrato para conectar hardware |
| `spm/render_gl.py` | **Render por GPU** del CAD (EGL + OpenGL 3.3, `datos/malla_gpu.npz`): imagen de una pose (`python -m spm modelo`) y vídeo mp4 por ffmpeg (`python -m spm demo3d`, 3 s en vez de 90 s) |
| `spm/modelo3d.py` | Cuerpos rígidos del CAD (8) con su rotación alrededor de O, nubes de puntos para colisiones y **render por software** de respaldo (matplotlib, malla decimada a 55 mil triángulos) |
| `spm/colisiones.py` | **Mapa de colisiones** (inclinación × azimut × torsión) calculado con nubes de puntos de las piezas del CAD; `Limites.valida` lo usa como zona prohibida |
| `spm/visual.py` | Gráficas de trayectoria/seguimiento, animación del mecanismo (GIF) y mapa del espacio de trabajo |
| `spm/rot.py` | Rotaciones, cuaterniones, SLERP, parametrización inclinación/azimut/torsión |
| `spm/identificacion.py` · `spm/identificacion_serial.py` | **Identificación de los motores**: lee las capturas del firmware, ajusta `K`, `τ`, zona muerta y arranque por sentido, sugiere `pwm_min`/`kff`; el cliente serie ejecuta el plan (`herramientas/identificar_motor.py`, guía en `firmware/IDENTIFICACION.md`) |

## Interfaz gráfica (estilo del brazo 3DOF, con el CAD dentro)

`./startspm.sh` (o `python3 interfaz_spm.py`) abre una ventana Tkinter + matplotlib con la misma paleta oscura y estructura por pestañas que `abdul3dof_gui.py` / `brazo_integrado.py` del proyecto 3DOF-Arm-Robotics-project-UMNG, pero para este mecanismo y con el **modelo 3D del CAD** moviéndose en la interfaz renderizado **por GPU** (OpenGL sin ventana vía EGL + PyOpenGL, ya instalado en el sistema): malla fina del STEP sin decimar (~1.2 M triángulos, normales suaves con aristas vivas, z-buffer real, antialiasing 4×), ~8 ms por fotograma → cambiar de pose o animar es fluido. Izquierdo = girar, derecho = desplazar, rueda = zoom, doble clic = restablecer. Si OpenGL no está disponible, la interfaz cae sola al render por software de matplotlib (más lento y con la malla decimada); se puede forzar con `SPM_RENDER=mpl`.

| Pestaña | Qué hace |
|---|---|
| **Vista 3D CAD** | Deslizadores de inclinación (−30…30°), azimut (−360…360°) y torsión (−90…90°) → IK → motores (los valores negativos son válidos: inclinación negativa = inclinar hacia el azimut opuesto; si el deslizador salta de golpe se elige la rama de la IK más cercana a la pose inicial), o deslizadores de motor → FK → orientación. Estado en vivo (límites, colisión con holgura en mm, singularidad), mostrar/ocultar grupos de piezas, vistas predefinidas, «Enviar al robot» |
| **Motores + control** | Lazo de seguimiento simulado de la trayectoria planificada (Kp, avance de velocidad, τ del motor, encoder cuantizado, desvío) con gráficas de error; guarda límites en `datos/config.json` |
| **Trayectoria** | Tabla de puntos de paso (inclinación, azimut, torsión, tiempo por tramo, **espera**), ejemplos (incluido «Caja: 4 caras (5 s) + volcado a la máxima inclinación»; también `python -m spm caja` → CSV + vídeo), 3 interpolaciones —**Orientación (SLERP)**, **Articular (motores)**, **Control cinemático (Jacobiano)**—, perfil quíntico/trapezoidal, alargado automático por límites de motor, gráficas (motores, plataforma, velocidad, holgura de colisión del CAD, aceleración, recorrido), animación del CAD, importar/exportar CSV |
| **Serial / ESP32** | Mismo protocolo JSON que el brazo 3DOF (`{"m1":a,"m2":b,"m3":c}`, `{"motor":N,"pos":deg}`, `{"cmd":"reset"}`, `{"zero":N}`; telemetría `{"m1":{"posRef","pos","ok"},…}` en pulsos), mover motores, transmitir la trayectoria a Hz fijos, consola TX/RX, telemetría y **espejo en vivo** (la telemetría mueve el CAD vía FK). Bloquea por defecto los comandos que el CAD marca fuera de límites |
| **Espacio de trabajo** | Mapa polar (inclinación × azimut) de lo permitido según el CAD; clic = lleva el modelo a esa pose |

Probar la pestaña Serial sin hardware: `python3 herramientas/esp32_simulado.py` imprime un puerto virtual (`/dev/pts/N`) que se puede elegir en la interfaz.

## Entorno reproducible (.venv) y Windows

El simulador corre en un entorno virtual propio (`.venv/`, no se copia entre equipos: se crea en cada PC).

- **Linux/macOS:** `./startspm.sh` crea `.venv` la primera vez e instala `requirements.txt`. `./startspm.sh --lock` instala las versiones exactas de `requirements-lock.txt` (las del PC donde se desarrolló: Python 3.14, numpy 2.5.4, matplotlib 3.11.2…).
- **Windows:** instalar Python 3.11 o superior desde python.org (marcar «tcl/tk and IDLE» y «py launcher»), y ejecutar `startspm.bat` (crea `.venv`, instala y abre la interfaz); `startspm.bat --test` corre las pruebas; `--lock` usa las versiones exactas (solo si el Python es de la misma serie 3.14; si no, sin `--lock`).
- Lo que cambia en Windows: el render por GPU usa GLFW (ventana oculta + OpenGL 3.3 de tu tarjeta) en vez de EGL; si el controlador no ofrece OpenGL 3.3 cae al render por software de matplotlib. Sin `ffmpeg` los vídeos salen como GIF. La fuente es Consolas. Los puertos serie son `COMx` (pyserial los lista solo). Las pruebas que usan pty (`esp32_simulado.py`, firmware en el PC) se omiten en Windows.
- Los archivos de texto se leen/escriben en UTF-8 explícito; `startspm.bat` fija `PYTHONUTF8=1`.

## Uso rápido

```bash
cd ~/Documents/simulador_spm
python -m unittest discover -s tests          # 62 pruebas (la interfaz y el serial se prueban con la ventana oculta y un ESP32 simulado; la identificación, con capturas sintéticas y un firmware falso)
python ejemplos/ejemplo_basico.py
python -m spm ik --inclinacion 12 --azimut 40 --torsion 5     # ángulos de NNR y de motor
python -m spm fk 10 -20 5                                      # grados de motor -> orientación
python -m spm demo                                             # trayectorias + GIF + gráficas en salida/
python -m spm mapa                                             # mapa del espacio de trabajo
python -m spm modelo --inclinacion 12 --azimut 40              # imagen del mecanismo CAD en esa pose (indica si hay colisión)
python -m spm demo3d                                           # vídeo del modelo CAD en precesión (salida/modelo3d_precesion.mp4, ~90 s)
python -m spm caja                                             # programa «4 caras de una caja + volcado»: salida/caja_trayectoria.csv y .mp4
python -m spm colisiones                                       # recalcula el mapa de colisiones (~9 min)
```

Convención de la orientación `R` de la plataforma: **`R = I` y `theta = 0` es la pose inicial con el disco de la plataforma HORIZONTAL** (los motores a 0 ahí). En el CAD de ensamblaje el disco está inclinado 16.0°; `SPM` guarda esa diferencia (`R_h`, `th_h`: en la pose horizontal los NNR están 10.5°, 3.0° y −15.4° respecto a la pose de ensamblaje) y convierte al marco del CAD solo por dentro (`SPM(horizontal=False)` da el marco original del CAD). `desde_inclinacion(tilt, az, torsion)` inclina la normal de la plataforma `tilt` hacia el azimut `az` (desde +X hacia +Y) y después gira `torsion` sobre su propio eje (ángulos en rad). Motor *k* ↔ engrane NNR*k*.

## Modelo

Cada pata gira con su engrane NNR un ángulo `theta_i` alrededor del eje central. El eje de la unión Brasop–Brazov queda en `u_i = Rz(theta_i)·a1_i`; el de la unión Brazov–platosupv, fijo en la plataforma, en `w_i = R·a2_i`. Como el Brazov es rígido, `u_i·w_i = a1_i·a2_i`. De ahí sale la IK en forma cerrada (dos ramas por pata; se elige la más cercana a la anterior para no saltar entre soluciones) y la FK por Newton. Los 6 ejes concurren en O = (7.978, 3.674, 1174.275) mm, sobre el eje central (desvío 0.02 mm).

## Validación

- Pose inicial horizontal → `theta = 0` (y el disco queda a 0.000° de la horizontal en el CAD); torsión pura → los tres motores giran lo mismo.
- IK→FK ida y vuelta (200 orientaciones aleatorias), cierre exacto del Brazov, jacobiano contra diferencias finitas.
- Valores de referencia del script original que generó la animación contra el CAD (la animación se revisó visualmente en FreeCAD y el cierre del lazo daba 1e-15).

## Supuestos que hay que confirmar (marcados como tales en el código)

1. **Relación de engranes**: 37 dientes (NNR) / 17 (piñón) y signo −1. Falta confirmar el sentido de giro real y el cero de cada motor (`Transmision.offset_motor_rad`).
2. **Límites de motor** (60 rpm, 30 rad/s²) y **resolución del encoder** (1000 cuentas/vuelta de salida) son marcadores de posición, no datos del JGA25-370 que Arlo usa.
3. **Colisiones**: el mapa del CAD (malla de 2° de inclinación × 15° de azimut × torsión de −90° a +90° cada 15°, hasta 40° de inclinación; `herramientas/extender_mapa_torsion.py` amplía las torsiones) está calculado **desde el plato horizontal**. Resultado: sin ninguna colisión hasta ~28° en todas las direcciones (≥5.5 mm de holgura; a 30° quedan 1.9 mm), y desde ~32° el NNR1 roza los Brazov (en azimuts 45–75° y 285–315°); más allá de 40° la cinemática entra en singularidades. La pose inicial tiene 23 mm de holgura mínima. Precisión del muestreo ~0.5 mm, por eso `Limites.holgura_min_mm = 1.5`. No detecta penetraciones profundas (solo distancias) ni cubre torsiones fuera de ±90° (ahí se marca «fuera del rango del mapa»). Sin `mapa_colision.npz` se usa un cono uniforme de 28°. (Antes, con la pose de ensamblaje del CAD como referencia —disco ya inclinado 16°—, la colisión aparecía a 16° de inclinación y el mapa era asimétrico; `datos/mapa_colision_marco_cad.npz` conserva ese mapa.)
4. Es **solo cinemático**: sin dinámica de la plataforma, sin holguras ni elasticidad de las uniones, sin fricción de los pines snap-fit.
5. **Sin probar con hardware**: la pestaña Serial solo se probó contra `herramientas/esp32_simulado.py`. Se supone que el firmware del SPM usa el protocolo JSON del brazo 3DOF y que los pulsos/vuelta se configuran en la propia pestaña (el valor por defecto, 1000, es un marcador). `InterfazMotores` define el contrato para otros controladores.

## Siguientes pasos posibles

- Refinar el mapa de colisiones (malla más fina, torsión hasta ±30°, colisión exacta con FreeCAD solo en las celdas límite).
- Implementar `InterfazMotores` para el microcontrolador real (serie) y calibrar ceros/encoders.
- Estimación de la orientación con FK desde encoders y realimentación en espacio de la plataforma (no solo por motor).
- Planificación con puntos de paso sin parada (splines de cuaterniones) y evitación de singularidades.
- Visor interactivo (pyvista/vtk) si se instala; el render actual es estático/vídeo.

## Regenerar los datos desde el CAD (si cambia el modelo)

1. `FreeCADCmd herramientas/extraer_geometria_cad.py` mide ejes, emparejado de patas y punto O (escribe `cinem.json`, del que salió `datos/geometria.json`).
2. `FreeCADCmd herramientas/exportar_malla_cad.py` exporta las mallas decimadas y las nubes de puntos (`datos/malla_cuerpos.npz`) y `FreeCADCmd herramientas/exportar_malla_gpu.py` la malla fina con normales suaves para la GPU (`datos/malla_gpu.npz`, ~12 MB, 12 s); variables `SPM_CAD`/`SPM_DIR` para otras rutas.
3. `python -m spm colisiones` recalcula el mapa de colisiones.

## Programa «ver las 4 caras de una caja y volcarla»

Pose inicial (disco horizontal) 5 s → giro de 90° (2 s) y 5 s en cada cara (0°, 90°, 180°, 270°) → inclinación rápida (1.5 s) hasta la **máxima permitida** por el CAD con la torsión de la 4.ª cara (25.5° hacia azimut 0°; con otro azimut/torsión puede ser mayor: edita la fila 5), 3 s de pausa → vuelta a nivel (2 s) → 90° más que cierran la vuelta hasta la pose inicial (2 s). Dura 34.5 s, pico de 30.6 rpm en los motores, sin ninguna muestra fuera de límites. Los NNR acumulan las vueltas (los motores llegan a ~784° ≈ 2.2 vueltas): la IK sigue el camino sin saltos de 360° y `planificar_con_paradas` encadena los tramos con continuidad. Las esperas son tramos de duración fija entre dos copias del mismo punto (`expandir_esperas`) y solo se aplican con «parar en cada punto» o en modo articular. Para torsiones fuera de ±90° el mapa se extiende por la simetría de 120° del mecanismo con un margen (y no se extrapola por encima de 26° de inclinación).

## Firmware de la ESP32-S3 (`firmware/`)

`firmware/spm_esp32s3/spm_esp32s3.ino` controla los 3 motores DC con encoder del mecanismo y responde a la pestaña «Serial / ESP32»: PWM por hardware (LEDC 20 kHz), encoders en cuadratura X4 por hardware (PCNT), timer de hardware a 1 kHz que despierta una tarea de control en el núcleo 1 (periodo real medido, ciclos perdidos contados), referencia con perfil/streaming, PID de posición o cascada P→PI seleccionables por motor, y corte de seguridad por polaridad invertida/atasco. Detalles, pines, protocolo y puesta en marcha en `firmware/README_firmware.md`. **Compilado para ESP32-S3 con arduino-esp32 3.3.12 (343 KB de flash, 23 KB de RAM, sin avisos) y probado en el PC** (`firmware/test_host/run_tests.sh`: 75 comprobaciones con planta simulada; y la interfaz real contra el mismo código del firmware ejecutándose en el PC), **pero nunca ejecutado en la placa ni con motores reales**; las ganancias por defecto no están sintonizadas. La interfaz envía sola los «pulsos/vuelta» al firmware al conectar. Para sintonizar con datos hay un **modo de captura** (`capture`/`dump`, 104 comprobaciones en el PC) y `herramientas/identificar_motor.py`; el driver cableado es un **TB6612FNG** (ver `firmware/IDENTIFICACION.md`).
