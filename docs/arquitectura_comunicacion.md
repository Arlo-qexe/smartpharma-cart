# Informe de Arquitectura de Comunicación
### Sistema Comercial de Clasificación por Visión Artificial
**ESP32-S3 ↔ Orange Pi Zero 2W ↔ PC ↔ Regente de farmacia**

> Versión Markdown, sincronizada con el informe completo en Word
> (`Informe_Arquitectura_Comunicacion.docx`). Esta es la versión que Claude
> Code debe consultar — está pensada para leerse junto con
> `shared/protocol_constants.py`.

---

## 1. Resumen ejecutivo

El sistema clasifica productos (cajas) mediante reconocimiento óptico de
caracteres, distribuyendo las responsabilidades entre tres capas de hardware
más un rol humano de supervisión:

| Componente | Rol |
|---|---|
| ESP32-S3 | Control de bajo nivel en tiempo real: motores, servos y sensores de posición. No toma decisiones, solo ejecuta órdenes. |
| Orange Pi Zero 2W | Captura de imágenes, comunicación de red, y coordinación del ciclo entre los demás nodos. |
| PC | Ejecución del servicio de reconocimiento y consolidación del resultado. |
| Regente de farmacia | Supervisión humana: resuelve los casos que el sistema no puede clasificar automáticamente. |

El diseño prioriza, en este orden: que el mecanismo de clasificación nunca se
quede detenido indefinidamente ante una falla; que el sistema no requiera
configuración manual de red al instalarlo; y que el protocolo sea simple de
verificar y depurar.

Este documento cubre la capa de **comunicaciones e IoT**. El procesamiento de
imágenes (OCR/clasificación) es responsabilidad de otro equipo y se trata
aquí solo como un contrato de entrada/salida (ver sección 7).

## 2. Arquitectura general

```
ESP32-S3   <-- UART -->        Orange Pi Zero 2W
Orange Pi Zero 2W   <-- TCP/mDNS -->        PC
PC   <-- Panel de control -->        Regente de farmacia
```

**Principio de diseño del protocolo:** flujo de control estrictamente
secuencial entre los tres nodos — cada etapa se ejecuta solo después de que
la anterior fue confirmada, y ningún nodo avanza al siguiente objeto sin una
confirmación explícita del actual. Esto garantiza por construcción que nunca
haya dos objetos "en vuelo" al mismo tiempo, sin locks ni IDs de correlación.

## 3. Protocolo de descubrimiento de red (Orange Pi ↔ PC)

### 3.1 Decisión: mDNS / DNS-SD (Zeroconf)

- La PC registra el servicio `_ocr-service._tcp.local.` al iniciar (constante: `MDNS_SERVICE_TYPE`).
- La Orange Pi lo descubre automáticamente sin configurar direcciones IP manualmente.

**Justificación:** evita que cualquier ingreso manual de IP sea un punto de
fricción/fallo para el cliente final. mDNS es el estándar más usado para este
tipo de reconocimiento automático sin infraestructura de DNS dedicada.

### 3.2 Caché con invalidación bajo demanda

La IP y el puerto descubiertos se cachean en el nodo de captura y solo se
repite el descubrimiento mDNS cuando una conexión falla explícitamente — no
en cada ciclo.

**Justificación:** conexiones TCP de vida corta (ver 4.1) ya funcionan como
prueba de vida en cada intento; si falla, se invalida la caché y se fuerza un
nuevo descubrimiento, cubriendo tanto cambio de IP como reinicio del servicio.

## 4. Protocolo de transporte de imágenes (Orange Pi ↔ PC)

### 4.1 Decisión: conexión TCP nueva por cada lote

| Opción | Descripción |
|---|---|
| A. Conexión persistente | Una sola conexión abierta durante todo el turno de operación. |
| **B. Conexión por lote (elegida)** | Se abre y cierra una conexión nueva para cada objeto clasificado. |

**Justificación:** la Opción A requiere un mecanismo adicional de
verificación periódica para detectar caídas silenciosas. La Opción B
convierte cada intento de conexión en su propia prueba de vida. El costo de
una nueva conexión en LAN es despreciable frente al tiempo de captura y
reconocimiento.

### 4.2 Estructura de los mensajes

Envío de imágenes (Orange Pi → PC):
```
[4 bytes: cantidad de imágenes] + N x ([4 bytes: tamaño de imagen] + [bytes JPEG])
```

Respuesta (PC → Orange Pi):
```
[4 bytes: longitud del JSON] + [bytes JSON UTF-8]
```

Ver `FRAMING_STRUCT_FORMAT`, `FRAMING_LENGTH_BYTES` en `shared/protocol_constants.py`.

**Caso especial — lote inválido:** si la captura falla en la Orange Pi antes
de completarse (ver sección 6.2), se envía la misma estructura con
**cantidad de imágenes = 0**. La PC responde `ERROR_REVISION_MANUAL` sin
intentar el reconocimiento — se reutiliza la infraestructura existente, sin
mensaje nuevo.

### 4.3 Manejo de fallos de red y reintentos

| Parámetro | Valor | Constante |
|---|---|---|
| Tiempo de espera de conexión | 3 s | `TIMEOUT_CONEXION_TCP_S` |
| Tiempo de espera de respuesta | 10 s (inicial, calibrar) | `TIMEOUT_RESPUESTA_RECONOCIMIENTO_S` |
| Límite total de transacción | 30 s | `TIMEOUT_TOTAL_TRANSACCION_S` |

El lote completo se mantiene en memoria durante todos los reintentos y se
reenvía íntegro en cada intento (una conexión interrumpida invalida el
contexto de la transacción anterior).

**Justificación:** el límite de 30 s es una decisión de producto (cuánto
puede esperar el mecanismo físico), documentada como parámetro ajustable.

### 4.4 Alarma física local ante fallo de red

Al agotar el límite de 30 s sin respuesta de la PC, la Orange Pi fija el
resultado en `ERROR_REVISION_MANUAL` **y** envía a la ESP32-S3 la orden
`activar_alarma_local`, que enciende un indicador físico (zumbador o LED).

**Justificación:** si la falla es de red o de la propia PC, el panel de
control en pantalla puede no estar disponible para avisar al regente. Se
activa solo al agotar el límite total, no en cada reintento, para minimizar
la fatiga de alarmas ante fallos transitorios que se resuelven solos.

### 4.5 Consulta de la decisión del regente (Orange Pi → PC)

Tras recibir `ERROR_REVISION_MANUAL`, la Orange Pi **no ordena `clasificar`**:
consulta a la PC cada `INTERVALO_CONSULTA_DECISION_S` (2 s) si el regente ya
decidió, hasta obtener una respuesta `resuelta`. Es la opción A de
`propuesta_canal_regente.md`, aprobada por el lado Orange Pi el 2026-10-05.

- **Transporte:** conexión TCP nueva por consulta, al mismo host descubierto por
  mDNS pero en el puerto `TCP_PUERTO_DECISION_DEFECTO` (5001); mismo framing
  que 4.2 (`[4 bytes longitud] + JSON`) en ambos sentidos. Si la conexión
  falla, se aplica la misma política de caché/redescubrimiento de 3.2.
- **Consulta:** `{"consulta": "decision_regente"}`.
- **Respuestas:**

| Respuesta | Significado |
|---|---|
| `{"estado": "pendiente"}` | El regente aún no decide: seguir consultando. |
| `{"estado": "resuelta", "destino": "<X>"}` | `<X>` es `TIPO_X` o `DESCARTE`: la Orange Pi ordena `clasificar` con ese destino. |
| `{"estado": "ninguna"}` | La PC no tiene decisión en espera (p. ej. se reinició y perdió el estado). La Orange Pi mantiene la caja y se recupera según la sección 4.6. |
| `{"error": "consulta_invalida"}` | La consulta no cumple el formato. |

- **Sin IDs de correlación:** la PC guarda una sola decisión en espera, la del
  último lote con error (flujo secuencial). **No se borra al leerla**, así una
  respuesta perdida se resuelve repitiendo la consulta; se reemplaza cuando
  llega el siguiente lote.
- **La primera consulta ya encuentra `pendiente`:** la PC abre la decisión
  antes de responder `ERROR_REVISION_MANUAL`.
- **Tiempo de espera:** el límite de 30 s (4.3) aplica solo al intercambio de
  imágenes. La espera del regente no tiene tope en la PC; si el equipo quiere
  uno, debe definirse en el lado Orange Pi.
- **Fallos de comunicación con la PC** (8.1, punto 3) no usan este canal mientras
  dura la falla: no hay a quién consultar. Se resuelven con la alarma física local
  (4.4) y la recuperación de la sección 4.6.

### 4.6 Recuperación tras un fallo de comunicación con la PC

Decisión (S-02/D-06 del checklist): el ciclo **no se reanuda solo**; el regente
confirma, porque durante la alarma la caja pudo retirarse a mano. Cubre tres
situaciones: **F1** se agotan los 30 s enviando el lote (4.4), **F2** la PC
responde `ninguna` en la espera (4.5, perdió el estado), **F3** la PC deja de
responder durante la espera de la decisión.

1. La Orange Pi mantiene la caja y, en F1 y F3, deja encendida la alarma local
   (`activar_alarma_local`; en F3 tras `TIMEOUT_TOTAL_TRANSACCION_S` de consultas
   fallidas).
2. Sondea a la PC con un **lote vacío** cada `INTERVALO_REINTENTO_PC_S` (10 s),
   **sin tope**, con re-descubrimiento mDNS. La PC lo trata como cualquier lote
   vacío: responde `ERROR_REVISION_MANUAL`, alarma en su panel y abre la decisión.
3. Al **primer** sondeo con respuesta deja de sondear (si no, cada lote vacío
   crearía una alarma nueva y reemplazaría la decisión en espera), envía
   `desactivar_alarma_local` y pasa a consultar la decisión del regente (4.5).
4. El regente resuelve en el panel con `TIPO_X`, o `DESCARTE` si retiró la caja.
   Solo entonces la Orange Pi ordena `clasificar`.

En F2 la PC ya responde, así que el primer sondeo tiene éxito y no se enciende la
alarma local. Las fotos del objeto no llegan al panel (se envía un lote vacío).

## 5. Protocolo de control físico (Orange Pi ↔ ESP32-S3)

### 5.1 Decisión: enlace serie con estructura de mensaje y suma de verificación

- Fin de mensaje: salto de línea al final de cada JSON compacto.
- Validación de integridad: suma de verificación XOR de 8 bits: `<json>|<suma_hex>`.

Implementaciones de referencia en `shared/protocol_constants.py`
(`calcular_checksum_xor`, `empaquetar_mensaje_uart`,
`desempaquetar_mensaje_uart`) y `shared/protocol_constants.h`
(`calcularChecksumXor`, `empaquetarMensajeUart`).

**Justificación:** UART no ofrece verificación de integridad ni delimitación
por sí mismo. Una suma XOR simple detecta ruido eléctrico (frecuente cerca de
motores y servos) sin el costo de un método más complejo.

### 5.2 Reparto de responsabilidades en el protocolo

La ESP32-S3 ejecuta acciones físicas y reporta eventos de confirmación, sin
mantener temporizadores propios de reanudación; el nodo de captura interpreta
esos eventos y decide el siguiente paso. Esto evita que dos nodos intenten
tomar la misma decisión de forma independiente.

### 5.3 Mensajes definidos

`objeto_en_posicion` señala la llegada inicial del objeto a la zona de
captura (una sola vez por objeto), mientras que `en_posicion` confirma cada
uno de los cinco giros de rotación durante la ráfaga de fotos.

| Dirección | Mensaje | Propósito |
|---|---|---|
| Orange Pi → ESP32-S3 | `{"accion": "activar_dispensador"}` | Acciona el dispensador: autoriza la entrada de un nuevo objeto (antes `introducir_objeto`) |
| ESP32-S3 → Orange Pi | `{"evento": "objeto_en_posicion"}` | Confirma que el objeto llegó a la zona de captura |
| Orange Pi → ESP32-S3 | `{"accion": "girar_posicion", "cara": N}` | Ordena rotar el objeto a la cara N |
| ESP32-S3 → Orange Pi | `{"evento": "en_posicion", "cara": N}` | Confirma el giro de la cara N completado |
| Orange Pi → ESP32-S3 | `{"accion": "activar_alarma_local"}` | Enciende el indicador físico ante un fallo de red prolongado |
| Orange Pi → ESP32-S3 | `{"accion": "desactivar_alarma_local"}` | Apaga el indicador físico cuando la PC vuelve a responder (sección 4.6) |
| Orange Pi → ESP32-S3 | `{"accion": "clasificar", "destino": "TIPO_X"}` | Ordena accionar el mecanismo de clasificación |

### 5.4 Límite de reintentos en la introducción del objeto

Si la ESP32-S3 no confirma `objeto_en_posicion` a tiempo, la Orange Pi repite
`activar_dispensador` hasta `LIMITE_REINTENTOS_INTRODUCIR_OBJETO` (5) veces; si
se agotan, fija `lote_valido = falso` sin iniciar la ráfaga, y continúa con
un lote vacío hacia la PC.

**Justificación:** sin este límite, una falla mecánica persistente en el
mecanismo de entrada dejaría a la Orange Pi reintentando indefinidamente
antes de llegar siquiera a la etapa de captura o red.

### 5.5 Mecanismo físico de introducción: el dispensador

La orden `activar_dispensador` (antes `introducir_objeto`) se implementa mediante un
**dispensador accionado por servomotor**, que permite el paso individual de
un objeto hacia la plataforma de clasificación. Este mecanismo es la
garantía física de la regla de no concurrencia de la sección 2: al liberar
un objeto a la vez, impide que exista más de uno simultáneamente en la zona
de clasificación.

Ciclo por objeto: el dispensador libera un objeto → el mecanismo de
clasificación lo posiciona para la ráfaga de captura → se ejecuta la
clasificación → se repite para el siguiente objeto, sin solaparse.

**Decisión (2026-10-06):** el servomotor del dispensador lo controla la
ESP32-S3 — consistente con el resto de actuadores, y evita abrir un segundo
canal de control fuera del protocolo UART. La orden es `activar_dispensador`
(`ACCION_ACTIVAR_DISPENSADOR`) y **reemplaza** a `introducir_objeto`: se confirma
igual, con `objeto_en_posicion`, y conserva el límite de reintentos de 5.

## 6. Proceso de captura de imágenes

### 6.1 Ráfaga de 5 fotos por objeto

Por cada una de las `CARAS_POR_OBJETO` (5) caras:

1. Orange Pi ordena girar → ESP32-S3 mueve el motor y confirma posición.
2. Pausa de estabilización (`PAUSA_ESTABILIZACION_MECANICA_S`, 0.3 s inicial) — mitiga vibración mecánica residual.
3. Autoenfoque (`TIEMPO_CONVERGENCIA_AUTOENFOQUE_S`, 0.5 s inicial) y captura, vía controlador estándar de video.
4. Compresión a JPEG **en memoria**, sin escritura a disco.

### 6.2 Manejo de fallos durante la captura

Si en cualquier punto del ciclo la ESP32-S3 no confirma el giro a tiempo, se
**aborta el lote completo** — no se intenta continuar con menos de 5 fotos.
Se envía un lote vacío a la PC (ver 4.2), manteniendo un único camino de
manejo de errores en todo el sistema.

## 7. Interfaz con el servicio de reconocimiento (PC)

El procesamiento de imágenes es responsabilidad de otro equipo. El contrato
de datos es:

- **Entrada esperada por la PC:** lote de imágenes JPEG de las 5 caras, bajo el framing de la sección 4.2.
- **Salida esperada de la PC:** JSON con clave `clasificacion` (constante `CLAVE_RESULTADO_JSON`), cuyo valor es el tipo de producto o `ERROR_REVISION_MANUAL` (constante `RESULTADO_ERROR_REVISION_MANUAL`).

Esto permite desarrollar, integrar y probar la capa de comunicaciones de
forma independiente del avance del módulo de reconocimiento. Ver
`pc/src/ocr_interface.py` para el stub de este contrato.

## 8. Manejo de excepciones y rol del regente de farmacia

### 8.1 Cuándo se activa

`ERROR_REVISION_MANUAL` puede originarse en tres puntos:

1. **Fallo de captura física** — la ESP32-S3 no confirmó un giro a tiempo, o se agotó el límite de reintentos al introducir el objeto.
2. **Fallo de reconocimiento** — la PC no logró consenso en la votación.
3. **Fallo de comunicación con la PC** — se agotó el límite de 30 s.

En los dos primeros, la PC activa una alarma en su panel de control. En el
tercero, la Orange Pi activa la alarma física local en la ESP32-S3 (ver 4.4).

### 8.2 Flujo de intervención

1. La caja permanece físicamente en posición — no se acciona el mecanismo de clasificación mientras el error está activo.
2. El ciclo se detiene: la Orange Pi no autoriza el siguiente objeto.
3. El regente percibe la alarma, revisa la caja manualmente, y determina la clasificación correcta o el descarte definitivo.
4. El regente resuelve la alarma en el panel de la PC escribiendo el destino (`TIPO_X` o `DESCARTE`).
5. La Orange Pi, que consulta periódicamente (sección 4.5), recibe el destino y solo entonces ordena el mecanismo de clasificación (`clasificar`) con ese destino.

## 9. Alcance y exclusiones deliberadas

| Tema | Estado |
|---|---|
| Autenticación y seguridad de la conexión TCP | Fuera de alcance (prototipo académico en red local controlada) |
| Selección e implementación del servicio de reconocimiento | Fuera del alcance de este equipo (frente de IA) |
| Calibración de tiempos (pausas, timeouts) | Pendiente de pruebas con hardware real |
| Mecanismo exacto de los paneles de alarma | Pendiente de diseño de interfaz |
| Ubicación del control del servomotor del dispensador | Decidido: ESP32-S3, comando `activar_dispensador` (ver sección 5.5) |

## 10. Parámetros de configuración

Ver `shared/protocol_constants.py` — es la fuente ejecutable de esta tabla,
no la copies a mano en otro lugar.

| Parámetro | Valor inicial | Constante |
|---|---|---|
| Límite de reintentos para `introducir_objeto` | 5 | `LIMITE_REINTENTOS_INTRODUCIR_OBJETO` |
| Tiempo de espera de conexión TCP | 3 s | `TIMEOUT_CONEXION_TCP_S` |
| Tiempo de espera de respuesta del reconocimiento | 10 s | `TIMEOUT_RESPUESTA_RECONOCIMIENTO_S` |
| Límite total de transacción con la PC | 30 s | `TIMEOUT_TOTAL_TRANSACCION_S` |
| Pausa de estabilización mecánica | 0.3 s | `PAUSA_ESTABILIZACION_MECANICA_S` |
| Tiempo de convergencia de autoenfoque | 0.5 s | `TIEMPO_CONVERGENCIA_AUTOENFOQUE_S` |
| Caras fotografiadas por objeto | 5 | `CARAS_POR_OBJETO` |
| Umbral de votación del reconocimiento | Mayoría simple (>50%) | `UMBRAL_VOTACION_MAYORIA` |
| Puerto de consulta de decisión del regente | 5001 | `TCP_PUERTO_DECISION_DEFECTO` |
| Intervalo de consulta de decisión | 2 s | `INTERVALO_CONSULTA_DECISION_S` |
| Destino de descarte definitivo | `DESCARTE` | `DESTINO_DESCARTE` |

---

*Documento vivo — actualízalo junto con `shared/protocol_constants.py` cuando
cambie algo del protocolo, y registra el cambio en `CHANGELOG_protocolo.md`.
El informe original en Word (con el diagrama de flujo a color) sigue siendo
la versión de referencia para presentar al curso.*
