# Informe general de avances — SmartPharma Cart
### Lado PC y lado Orange Pi (con el estado de la ESP32-S3 y del módulo de IA)

**Fecha de corte:** 2026-11-03 · **Redactado desde el lado PC** (con Claude Code). Complementa a
`shared/checklist.md` (estado detallado e IDs), `docs/CHANGELOG_protocolo.md` (historial del contrato) y
`docs/arquitectura_comunicacion.md` (el "por qué" de cada decisión). Aquí va la visión de conjunto.

> **Qué está verificado y qué no.** Las cifras de la PC y de la Orange Pi (pruebas automáticas) se
> corrieron el día del corte. Lo que se dice de pruebas con hardware de la Orange Pi viene de su propia sección del
> checklist (última prueba real: 2026-10-23); **hoy la Orange Pi no estaba conectada a la red**, así que no se
> re-probó. La ESP32-S3 real **no existe todavía en el repo** (solo un esqueleto).

---

## 1. Resumen ejecutivo

| Frente | Estado | En una línea |
|---|---|---|
| **PC** (servidor, panel, inventario, usuarios, IA conectada) | **Avanzado y estable** | Todo lo planeado del lado PC está hecho y probado salvo la pestaña de telemetría (contrato ya definido). |
| **Orange Pi** (gateway de captura y red) | **Completo para el ciclo con ESP32-S3 simulada** | Ciclo de auditoría de punta a punta probado con la PC real; falta ESP32-S3 real y lo nuevo de telemetría. |
| **ESP32-S3** (control físico) | **Sin implementar** | Solo existe un esqueleto con `TODO`. Es el principal bloqueo para la prueba con hardware completo. |
| **Módulo de IA** (OCR/FEFO, equipo de IA) | **Conectado, con un retroceso por resolver** | Funciona en la PC (16 de 27 cajas, ~3,7 s) con la versión del 26/10; la versión del 27/10 empeora (ver §6). |

**Lo más importante para decidir esta semana**
1. **Firmware de la ESP32-S3** (S-04, S-08, S-09, S-13): sin él no hay prueba de punta a punta con el mecanismo real.
2. **Versión del módulo de IA:** la última del equipo de IA lee menos cajas y tarda más de 10 s (§6.1).
3. **Rangos FEFO reales:** hoy son provisionales y el carrito clasifica con ellos.
4. **Telemetría:** el contrato está acordado; falta que la Orange Pi y la ESP32-S3 lo implementen y que la PC cree la pestaña.

---

## 2. Arquitectura y principio de diseño (recordatorio)

```
ESP32-S3  <--UART-->  Orange Pi Zero 2W  <--TCP/mDNS-->  PC  <--Panel web-->  Regente
```

Flujo de control **estrictamente secuencial**: ningún nodo avanza al siguiente objeto sin la confirmación explícita del
actual. Un objeto: dispensador → 5 giros con foto → lote a la PC → clasificación (o decisión del regente) → `clasificar`.
El contrato vive solo en `shared/` y los demás lados lo importan (regla de oro).

---

## 3. Lado PC (`pc/`) — hecho

| Área | Qué hay | Detalle |
|---|---|---|
| Transporte | Servidor TCP de lotes (5000) y de consultas (5001), anuncio mDNS | PC-01 a PC-03, PC-20. Validación de framing, IP anunciada robusta. |
| Panel web | Inicio, Inventario, Alertas, Registro, Usuarios, Configuración, asistente | PC-04, PC-16. Solo biblioteca estándar; escucha solo en `127.0.0.1` (D-09). |
| Control del ciclo | Botón "Iniciar recorrido", "Detener", cantidad de cajas, indicador de conexión de la Orange Pi | PC-10, PC-22 (informe 4.7 y 4.8). Probado de punta a punta con la Orange Pi real. |
| Decisión del regente | Alerta → destino `TIPO_X` o `DESCARTE` → la Orange Pi lo consulta | PC-03 (informe 4.5, 4.6). |
| Registro de eventos | SQLite de **solo escritura** con cadena de hashes, verificación de integridad | PC-23 A. Todo evento lleva fecha, hora y responsable. |
| Usuarios y turnos | Auxiliar con código de 6 dígitos, administrador con clave; un turno a la vez; bloqueo por intentos | PC-23 B (D-14). Operar exige turno abierto. |
| Inventario | Una fila por caja; filtros, orden, vista agrupada por medicamento y lote, edición, eliminación lógica | PC-23 C. |
| IA en la PC | `PC_RECONOCIMIENTO=ia` ejecuta `IA/` dentro de la PC y llena medicamento, gramaje, lote, vencimiento y confianza **sin confirmación** | PC-23 D. Inicio muestra la lectura y el estado FEFO. |
| Exportación | CSV del inventario y del registro (Excel, con protección contra fórmulas) | PC-23 E. Cada descarga queda en el registro. |
| Simulación | `pc/scripts/simular_orangepi.sh`: Orange Pi simulada con fotos ya tomadas | Permite probar IA, panel e inventario sin hardware. |
| Documentación | `docs/GUIA_REGENTE.md` (guía de uso) | Redactada para el regente, sin términos técnicos. |

**Pruebas (2026-11-03):** `pc/` 496 pasan, 1 omitida (prueba opt-in con el modelo real). Además se verificó la interfaz con
pruebas de clics reales en un navegador sin interfaz (descarga de CSV incluida).

**Decisiones propias del lado PC:** D-05 revisada por D-13 (se persisten registro, usuarios e inventario; las fotos y las
alarmas siguen solo en RAM); D-14 (control de acceso); no hacer respaldo de la base ni política de retención (proyecto de clase).

### 3.1 Pendiente del lado PC
- **PC-24 · Telemetría:** atender `consulta: telemetria` en el puerto 5001, historial de ~10 min en RAM, pestaña TELEMETRÍA (tarjetas ESP32 y
  Orange Pi, gráficos pequeños, aviso de "datos viejos"). El contrato ya está acordado y publicado.
- **PC-11:** campos de OCR/FEFO en el panel dependen de que el equipo de IA entregue los rangos FEFO reales y confirme S-05.
- **PC-13:** calibrar timeouts con hardware real (no se puede hasta tener el hardware completo).
- **PC-19:** asistente conversacional con modelo de lenguaje (hoy en modo de prueba con reglas fijas; faltan elegir modelo y las acciones).

---

## 4. Lado Orange Pi (`orange-pi/`) — hecho

*Estado tomado de su sección del checklist y verificado con sus pruebas automáticas el día del corte.*

| Área | Qué hay | Detalle |
|---|---|---|
| Enlace UART | Checksum XOR, buffer limpio antes de cada orden, cierre ordenado | OP-01; `/dev/ttyS0` verificado con loopback. |
| Red | Descubrimiento mDNS con caché e invalidación; cliente TCP de lotes con reintentos hasta 30 s | OP-02, OP-03. |
| Ciclo por objeto | Dispensador (con hasta 5 reintentos), ráfaga de 5 caras, lote vacío si algo falla | OP-05, OP-13; captura robusta (informe 6.2). |
| Cámara | Captura 1080p con autoenfoque, JPEG en memoria; **se enciende solo durante cada lote** | OP-04, OP-27, D-12. |
| Decisión del regente | Consulta cada 2 s sin tope; `DESCARTE` soportado | OP-10 a OP-12, OP-19. |
| Recuperación | Si falla la PC: alarma local, sondeo con lote vacío cada 10 s, `desactivar_alarma_local`, espera al regente | OP-15 (informe 4.6). |
| Orden de inicio y detención | Espera `iniciar`; consulta `estado_ciclo` antes de cada objeto | OP-22, OP-26 (informe 4.7 y 4.8). |
| Operación | `start_Opi.sh` (verifica UART, cámara y red; opciones `--verificar`, `--sin-orden`, `--simular-esp`) | OP-24, OP-25. |
| Red de la demo | Router D-Link DWR-932W, `192.168.0.x`, ciclo completo probado | OP-23, S-10 (D-11). |

**Pruebas (2026-11-03):** `orange-pi/` 60 pruebas pasan, sin hardware. **Pruebas con hardware (2026-10-06 a 2026-10-23, según su
sección):** ciclo de punta a punta contra la PC real con cámara real y ESP32-S3 simulada, incluido el camino del regente;
12 ciclos seguidos con el botón; detener y reanudar; todo también en el router de la demo.

### 4.1 Pendiente del lado Orange Pi
- **S-12 · Telemetría** (abierta): despachar eventos UART por nombre, pedir la telemetría a la ESP32-S3 cada 2 s, enviar la suya a la PC cada 5 s en
  segundo plano y un monitor de terminal local; que `--simular-esp` también responda telemetría.
- **OP-16:** prueba de punta a punta con la ESP32-S3 **real** (depende del firmware).
- Medir con la **Arducam** final: el autoenfoque a la distancia real de la caja no está verificado.

---

## 5. ESP32-S3 (`esp32-firmware/`) — sin implementar

`esp32-firmware/src/main.ino` (116 líneas) solo implementa el lazo de lectura UART con checksum y las respuestas de confirmación simuladas;
**todo el control físico es `TODO`** (motores, encoders, dispensador, alarma, clasificación). Pendientes del contrato para quien lleve el firmware:

| ID | Qué se pide |
|---|---|
| S-04 | `clasificar` puede traer `DESCARTE`: mapearlo al contenedor de descarte. |
| S-08 | `activar_dispensador` reemplaza a `introducir_objeto`; control real del servo. |
| S-09 | Manejar `desactivar_alarma_local` (apagar el zumbador/LED). |
| S-13 | Responder `solicitar_telemetria` con los campos acordados en menos de 0,5 s, sin interrumpir un movimiento. |

**Datos del hardware dichos por el equipo (para el firmware y la telemetría):** giroscopio por I2C, ADC por I2C con 2 sensores de corriente,
5 encoders de cuadratura (A y B) y 11 salidas PWM. Los **5 encoders** se reparten así: **3 son de los motores del mecanismo SPM** (manipulador
esférico paralelo del clasificador) y **2 de las ruedas de la base** del carrito. `esp32-firmware/CLAUDE.md` solo habla de los 3 motores del SPM
y no menciona las ruedas: conviene completarlo cuando se defina el firmware de la base. *Punto por confirmar con quien lleve el firmware:* en el
campo `enc` de la telemetría, qué posiciones son del SPM y cuáles de las ruedas (el contrato solo dice `[e1..e5]`; se puede fijar, por ejemplo,
`e1..e3` = SPM y `e4..e5` = ruedas, pero no se ha decidido).

---

## 6. Módulo de IA (equipo de IA) — conectado, con hallazgos

El reconocimiento corre **dentro de la PC** (no viaja por el cable a la Orange Pi), así que S-05 se reduce a la interfaz interna `pc/src/ocr_interface.py` ↔ `IA/` y
**no cambia el contrato**.

### 6.1 Retroceso detectado el 2026-11-03 (para el equipo de IA)
Se midieron las 27 cajas del conjunto de entrenamiento (fotos de celular, CPU de la PC de desarrollo), en proceso y sin tocar el inventario:

| Versión de `IA/` | Cajas leídas completas | Tiempo por caja (media / máx.) | Cajas > 10 s |
|---|---|---|---|
| Del 26/10 (`f87f3a1`) | **16 de 27** | **3,7 s / 8,2 s** | **0** |
| Del 27/10 (`a6a65ce`, pipeline en 3 pasos) | 8 de 27 (con tope de 9 s) | 10,7 s / 19,1 s | 19 |

Con la versión del 26/10 las 6 cajas probadas aparte se leyeron todas (media 3,4 s) en la misma máquina, así que la causa es el código y no el equipo.
Sospechosos: el paso de reintentos con filtros de imagen (`prefiltro.py`) y `confianza_minima` subida de 0,6 a 0,75. **La Orange Pi espera 10 s y reenvía el
lote**, de modo que con la versión nueva habría reenvíos y cajas duplicadas. **Decisión provisional del lado PC (pedida por el usuario):** en esta copia de
trabajo se restauraron los archivos de ejecución de `IA/` a la versión del 26/10; **los cambios del equipo de IA no se borraron del historial**.

### 6.2 Otros pendientes del equipo de IA
- **Rangos FEFO reales:** hoy son provisionales (vencido → `DESCARTE`, ≤ 90 días → `TIPO_A`, ≤ 180 → `TIPO_B`, resto → `TIPO_C`) y **el carrito clasifica con ellos** en modo `ia`.
- Medir con fotos de la **Arducam** (todo lo medido es con fotos de celular, 1440×1920) y calibrar `tiempo_maximo_s` con cajas reales.
- Texto en relieve o al revés se lee mal; nombres y lotes ruidosos entran al inventario sin confirmación (decisión del usuario; las correcciones humanas quedan en el registro).
- Confirmar S-05.

---

## 7. Contrato (`shared/`) — historial resumido

| Fecha | Cambio | Quién lo implementa |
|---|---|---|
| 2026-10-05 | Consulta de la decisión del regente (puerto 5001); `DESCARTE` | PC, Orange Pi, ESP32-S3 (S-04) |
| 2026-10-06 | `activar_dispensador` (reemplaza a `introducir_objeto`); `desactivar_alarma_local`; recuperación de 4.6; orden de inicio `orden_ciclo` | Orange Pi (hecho); ESP32-S3 pendiente |
| 2026-10-23 | `estado_ciclo`: continuar o detener entre objetos | PC y Orange Pi (hecho) |
| 2026-10-26 | **Telemetría** ESP32-S3 → Orange Pi → PC (informe 4.9 y 5.6): petición/respuesta por UART y mensaje TCP cada 5 s, de mejor esfuerzo | Orange Pi (S-12), ESP32-S3 (S-13), PC (PC-24): pendientes |

Los cambios 2026-10-23 y 2026-10-26 siguen el flujo acordado: primero `shared/`, con entrada en el CHANGELOG, y aviso al equipo.

---

## 8. Riesgos y puntos abiertos

| # | Riesgo / punto abierto | Impacto | Acción propuesta |
|---|---|---|---|
| 1 | Firmware de la ESP32-S3 sin implementar | No hay prueba con el mecanismo real; bloquea OP-16, S-04, S-08, S-09, S-13 | Priorizar; la Orange Pi ya puede simular la ESP32-S3 (`--simular-esp`) |
| 2 | Versión nueva de IA más lenta y menos precisa | > 10 s ⇒ reenvíos y cajas duplicadas | Equipo de IA revisa §6.1; mientras tanto se usa la versión del 26/10 |
| 3 | Rangos FEFO provisionales | El carrito clasifica y descarta con reglas no confirmadas | Equipo de IA confirma los rangos |
| 4 | Precisión medida solo con fotos de celular | Puede cambiar con la Arducam y la iluminación del carrito | Repetir la medición con fotos del carrito real |
| 5 | Red: en Wi-Fi `192.168.20.x`, WSL no resolvió el ARP a la Orange Pi | En la red de la demo no ocurre; sin causa explicada | Si reaparece: parche de ARP o PC por cable (ver PC-21) |
| 6 | El panel solo escucha en `127.0.0.1` (D-09) | El regente debe trabajar frente a la PC | Reevaluar si cambia el uso |
| 7 | Caja duplicada si llega un lote vacío de sondeo tras perder la conexión (caso F2) | Una fila de más en el inventario | Se elimina a mano; documentado en la guía del regente |
| 8 | Los turnos viven en RAM | Reiniciar el servidor cierra el turno | Aceptado; documentado |
| 9 | La base tiene ruedas con encoder, pero el contrato **no define ninguna orden para moverlas** (solo viajan sus encoders en la telemetría) | Hoy el botón "Iniciar recorrido" inicia el ciclo de auditoría y no desplaza el carrito (aclaración del usuario, 2026-10-05) | Si el carrito debe avanzar entre cajas, definir el mensaje en `shared/` antes de implementarlo |

---

## 9. Próximos pasos sugeridos (en orden)

1. **Equipo de firmware:** implementar la ESP32-S3 (dispensador, giro con encoders, alarma, clasificación, telemetría) contra el contrato actual.
2. **Equipo de IA:** revisar el retroceso de §6.1 y confirmar los rangos FEFO; medir con la Arducam.
3. **Lado Orange Pi:** S-12 (telemetría y monitor de terminal), y repetir las pruebas de punta a punta cuando la ESP32-S3 real esté lista.
4. **Lado PC:** PC-24 (recepción y pestaña de telemetría) en cuanto la Orange Pi confirme el formato; después, calibrar tiempos con hardware completo (PC-13).
5. **Demo:** prueba general con todo conectado en el router de la demo, con la PC **conectada a la corriente** y el plan de energía en alto rendimiento (con batería, el reconocimiento se hace más lento).

---

## 10. Cómo verificar lo dicho aquí

| Qué | Cómo |
|---|---|
| Pruebas de la PC | `cd pc && env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q` |
| Pruebas de la Orange Pi | `cd orange-pi && python -m unittest discover -s tests` |
| Pruebas de IA | `cd IA && python -m unittest discover -s tests -t .` |
| Lectura de las 27 cajas con la IA | `bash pc/scripts/simular_orangepi.sh --sin-orden` (con el servidor en `PC_RECONOCIMIENTO=ia`; **escribe en la base que use el servidor**, usar `PC_DB=:memory:` para no ensuciar la real) |
| Estado detallado e IDs | `shared/checklist.md` |
| Uso del panel | `docs/GUIA_REGENTE.md` |
