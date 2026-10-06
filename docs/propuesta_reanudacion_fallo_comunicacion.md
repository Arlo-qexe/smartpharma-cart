# Consulta: cómo se reanuda el ciclo tras un fallo de comunicación con la PC

> **Estado: CONSULTA ABIERTA (S-02 en `shared/checklist.md`). No modifica
> `shared/` ni está implementada.** Redactada por el lado PC (Arlo.exe, con
> Claude Code) el 2026-10-05. **Se pide respuesta de cada lado en la tabla "Respuestas" del
> checklist** antes de implementar nada: las opciones tocan a la Orange Pi, a la
> ESP32-S3 y al contrato.

## 1. El problema

Cuando el fallo es del reconocimiento (lote vacío, sin consenso), el regente
resuelve desde el panel de la PC y la Orange Pi recibe el destino (sección 4.5).
Pero hay situaciones en que **la PC no está disponible**, y ahí el informe solo
dice "la Orange Pi activa la alarma física local" (4.4) — no dice **qué pasa
después, ni quién desbloquea el ciclo**.

Estado actual del código de la Orange Pi (leído por el lado PC en
`orange-pi/src/main.py` y `network/decision_client.py`; la Orange Pi debe
confirmarlo):

| # | Situación | Qué hace hoy |
|---|---|---|
| **F1** | Se agotan los 30 s enviando el lote (red o PC caída) | Alarma local y un `input()` provisional ("presiona Enter cuando el regente resuelva"). Con el TODO de que no hay canal para desbloquear sin la PC. |
| **F2** | La PC responde `ninguna` mientras se espera la decisión (se reinició y perdió el estado) | Mantiene la caja, activa la alarma local y **detiene el ciclo** (OP-19): solo se reanuda reiniciando el proceso. |
| **F3** | La PC deja de contestar durante la espera (30 s de consultas fallidas) | Activa la alarma local una vez y sigue consultando; al volver la PC, depende de si conservó la decisión (→ continúa, o `ninguna` = F2). |

Hay que decidir, para F1 y F2 (y en parte F3): **1)** quién decide el destino de
esa caja, **2)** por qué canal llega esa decisión sin PC, y **3)** cómo se apaga
la alarma física y se reanuda el ciclo.

**Dato que condiciona todas las opciones:** hoy la ESP32-S3 solo sabe
*encender* la alarma local (`ACCION_ACTIVAR_ALARMA_LOCAL`). **No existe ninguna
orden para apagarla**, así que cualquier opción que reanude el ciclo necesita al
menos un mensaje nuevo (o apagarla por un medio físico).

## 2. Restricciones del diseño

- El principio central: **ningún nodo avanza al siguiente objeto sin una
  confirmación explícita del actual** (sección 2). Una reanudación debe venir de
  algo explícito, no de "se acabó el tiempo".
- La caja permanece en posición mientras haya error (8.2, paso 1).
- La alarma local existe justamente porque el panel puede no estar disponible
  (4.4); la solución no debería depender del panel.
- Es un prototipo en red local controlada (sección 9): se admite una
  intervención técnica ocasional si el equipo lo decide.

## 3. Opciones

### Opción 0 — Aceptar que requiere intervención técnica (estado actual, formalizado)

El ciclo se detiene con la caja en posición y alarma encendida; un técnico
reinicia el proceso en la Orange Pi (y la PC si hace falta).

- **Cambios:** ninguno de código; solo documentar el comportamiento.
- **Pros:** cero trabajo, sin riesgo de reanudar mal.
- **Contras:** el regente no puede resolverlo solo; un corte de WiFi de 30 s deja
  el sistema detenido hasta que alguien con acceso a la Orange Pi intervenga.
- **Veredicto:** aceptable como **límite explícito** (p. ej. si la PC no vuelve
  nunca), no como único mecanismo.

### Opción 1 — Reenvío automático del lote cuando la PC vuelve

Ante F1 o F2 la Orange Pi **conserva el lote en memoria** (5 fotos ≈ 2-3 MB),
activa la alarma local, mantiene la caja y **reintenta enviar el lote** cada
cierto intervalo (con re-descubrimiento mDNS) sin límite. Cuando la PC responde,
se **apaga la alarma local** y sigue el flujo normal: clasificación automática, o
alarma en el panel y decisión del regente (sección 4.5) si hubo error.

- **Cubre:** F1, F2 y F3 sin intervención humana si la falla fue transitoria
  (WiFi caído, PC reiniciada).
- **Cambios en `shared/`:** `ACCION_DESACTIVAR_ALARMA_LOCAL` (Orange Pi →
  ESP32-S3, en `.py`, `.h` y esquema) y un intervalo de reintento (propuesta:
  `INTERVALO_REINTENTO_PC_S = 10`, sin tope).
- **Cambios por lado:** Orange Pi (bucle de reintento que sustituye al `input()` y
  al "detener ciclo"), ESP32-S3 (apagar la alarma). **PC: ninguno** — recibe un
  lote como cualquier otro; una alarma vieja del panel, si la hubiera, el
  regente la cierra (queda como "cerrada", sin decisión).
- **Pros:** sin hardware nuevo, reutiliza el canal ya probado, respeta que el
  regente y el panel solo intervienen cuando hay error de reconocimiento.
- **Contras / riesgos:**
  - Si la PC está caída mucho tiempo, el sistema espera indefinidamente
    (→ combinar con la Opción 0 como límite).
  - Mientras dura la alarma por fallo de red, el regente podría **retirar la caja a
    mano**; al volver la PC, la Orange Pi ordenaría `clasificar` sobre una
    plataforma vacía. Mitigación: documentar que con alarma de red **no se toca la
    caja** hasta que la alarma se apague (o ver pregunta 2).
  - Si el regente ya había decidido en el panel antes de caer la PC (F2/F3), esa
    decisión se pierde y debe repetirla.

### Opción 2 — Confirmación física en el carrito (botón o llave)

Un pulsador cableado a la ESP32-S3. El regente lo acciona tras revisar la caja; la
ESP32-S3 envía un evento a la Orange Pi, que apaga la alarma y reanuda.

- **Variantes:** un solo botón ("resuelto, seguir") o dos ("descartar" /
  "reintentar enviar").
- **Cambios:** hardware (pulsador + cableado), firmware (leer el botón),
  `shared/` (un `EVENTO_…` ESP32-S3 → Orange Pi con el campo de acción, y
  `ACCION_DESACTIVAR_ALARMA_LOCAL`), Orange Pi (esperar el evento). PC: ninguno.
- **Pros:** funciona **sin red ni PC**, coherente con una alarma física; el
  regente decide de verdad (no es automático).
- **Contras:** trabajo de hardware y firmware; destinos fijos (no puede elegir
  `TIPO_X`); **no deja registro en el historial del panel**.
- **Veredicto:** la más robusta, pero la más costosa; razonable como segunda fase
  si en la demo se quiere tolerar una PC caída.

### Opción 3 — Desbloqueo local en la Orange Pi (consola o mini-interfaz)

El regente (o un técnico) confirma desde una terminal local o una página servida
por la propia Orange Pi.

- **Cambios:** una interfaz nueva en la Orange Pi (la consola actual `input()`
  es el embrión), más `ACCION_DESACTIVAR_ALARMA_LOCAL`.
- **Pros:** sin hardware nuevo; independiente de la PC.
- **Contras:** otro punto de interfaz que mantener; el regente necesitaría acceso
  a la Orange Pi del carrito (SSH, pantalla o teclado), poco realista para un
  usuario de farmacia.

### Opción 4 — Reanudar por tiempo (descartada)

Tras N minutos, descartar o reintentar automáticamente. **Contradice el
principio central** (nunca avanzar sin confirmación explícita) y puede perder o
clasificar mal una caja sin que nadie lo sepa. Se lista solo para dejar
constancia de por qué no se propone.

## 4. Comparación

| | Sin hardware nuevo | Sin intervención humana en fallo transitorio | Funciona con la PC caída | Mensajes nuevos | Cambios en PC |
|---|---|---|---|---|---|
| **0. Intervención técnica** | Sí | No | No (manual) | 0 | No |
| **1. Reenvío automático** | Sí | **Sí** | No (espera) | 1 (`desactivar_alarma_local`) | **No** |
| **2. Botón físico** | **No** | No (el regente confirma) | **Sí** | 2 | No |
| **3. Consola local** | Sí | No | Sí | 1 | No |
| 4. Por tiempo | Sí | Sí | Sí | 1 | No |

## 5. Recomendación del lado PC

**Opción 1 como mecanismo base, con la Opción 0 como límite explícito** (si la PC
no vuelve, se requiere intervención técnica y así queda documentado). Es la que
resuelve los casos más probables (WiFi o reinicio de la PC) sin hardware nuevo, y
la que menos toca: un mensaje en el contrato y ningún cambio en la PC. Si el
equipo quiere que el sistema tolere una PC caída durante la demo, **añadir la
Opción 2 como segunda fase**.

Es una recomendación, no una decisión: afecta a la Orange Pi y a la ESP32-S3,
que son quienes deben aceptarla.

## 6. Preguntas para cada lado

1. **Todos:** ¿se acepta la Opción 1 como base, con la Opción 0 como límite? ¿O se
   prefiere otra?
2. **Orange Pi / regente:** tras recuperarse la PC, ¿se reanuda **automáticamente**
   o el regente debe confirmar antes de que se ordene `clasificar`? (La propuesta
   asume automático; el riesgo es la caja retirada a mano, ver Opción 1.)
3. **Orange Pi:** ¿intervalo de reintento de 10 s y sin tope? ¿Se mantiene la alarma
   local encendida todo el tiempo o se repite periódicamente?
4. **ESP32-S3:** ¿se puede implementar `desactivar_alarma_local`? ¿Qué debe hacer
   el zumbador/LED mientras tanto (continuo, intermitente)?
5. **ESP32-S3:** si el regente retira la caja a mano, ¿el mecanismo necesita igual
   una orden `clasificar` para liberar la plataforma, o basta con "seguir"?
   (Relevante para las opciones 2 y 3.)
6. **Todos:** ¿quién agrega las constantes nuevas a `shared/` una vez decidido? El
   lado PC se ofrece, siguiendo la regla de oro (contrato + `.h` + esquema +
   changelog en el mismo turno).

## 7. Qué haría cada lado una vez decidido (Opción 1)

- **PC:** nada; se mantiene el servidor. Se podría añadir un aviso en el panel
  cuando llegue un lote tras un periodo sin conexión (opcional).
- **Orange Pi:** sustituir el `input()` y el "detener ciclo" por el bucle de
  reintento con el lote en memoria; apagar la alarma al recuperar la PC.
- **ESP32-S3:** manejar `ACCION_DESACTIVAR_ALARMA_LOCAL`.
- **`shared/`:** constantes nuevas, `.h`, esquema y entrada en el changelog.
