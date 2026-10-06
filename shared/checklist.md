# Checklist de coordinación — PC ↔ Orange Pi

> Documento vivo para que **ambos lados** vean el estado del otro, se hagan
> solicitudes y avisen de cambios o pendientes **antes de continuar**. No
> reemplaza al contrato (`protocol_constants.py`) ni al
> `docs/CHANGELOG_protocolo.md`: complementa a ambos con el "quién hace qué y
> en qué punto está".

**Última actualización:** 2026-10-06 (reloj de la Orange Pi) — Arlo-qexe (lado Orange Pi: OP-22, S-06 hecha).
Anterior: 2026-10-06 (reloj de la PC) — Arlo.exe (con Claude Code, lado PC: S-06 opción 1 implementada, D-10, asistente en el lateral de Inicio).

## Cómo usarlo

1. **Antes de empezar una sesión de trabajo**, lee las dos secciones y la tabla
   de solicitudes. Después del `git pull`, busca lo que está dirigido a tu lado.
2. **Cada lado edita su propia sección** (estado y pendientes). Para pedirle algo
   al otro lado, agrega una fila en **Solicitudes entre lados** — no edites la
   sección del otro.
3. **Al terminar algo**, márcalo aquí en el mismo commit. Todo cambio en
   `shared/` se commitea y se pushea en el mismo turno (regla de oro).
4. Si **no estás de acuerdo** con una solicitud, no la ignores ni la cambies en
   silencio: ponla en estado `En discusión` y escribe el motivo en la columna
   de notas. La decisión queda en **Decisiones tomadas**.
5. Cada línea lleva un **ID** (`PC-xx`, `OP-xx`, `S-xx`, `D-xx`) para
   referenciarla en commits y mensajes.

**Estados:** `[x]` hecho · `[~]` en curso · `[ ]` pendiente · `[!]` divergencia
con el contrato (hay que corregir) · `[?]` sin definir, requiere decisión.

---

## Sección 1 — Lado PC (`pc/`)

*Estado verificado por el propio lado PC (pruebas con pytest, 24 pasan).*

### Hecho

- [x] **PC-01** Servidor TCP de lotes (puerto 5000) con framing, validación
  (máx. 5 imágenes, máx. 10 MiB c/u) y timeout de inactividad (10 s).
- [x] **PC-02** Anuncio mDNS `_ocr-service._tcp.local.`; renombra el servicio si
  el nombre ya existe; si no hay ruta por defecto usa la IP del hostname.
- [x] **PC-03** Servidor de consulta de decisión del regente (puerto **5001**,
  sección 4.5 de `docs/arquitectura_comunicacion.md`). Decisión única en espera,
  sin IDs, no se borra al leerla, se reemplaza con el siguiente lote.
- [x] **PC-04** Panel web (`http://127.0.0.1:8080/`, solo biblioteca estándar):
  Inicio, Inventario, Alertas (selector de destino `TIPO_X`/`DESCARTE`),
  Configuración (solo lectura) y visor de fotos ampliadas. Las fotos solo viven
  en RAM (últimos 5 lotes).
- [x] **PC-05** Pruebas pytest (transporte, panel, decisión, flujo completo
  lote → decisión → regente) y cliente mock con `consultar_decision()`.
- [x] **PC-06** `setup_venv.sh` instala `requirements-dev.txt`;
  `requirements-lock.txt` generado.

### Pendiente

- [x] **PC-10** Botón "Iniciar recorrido" (**lado PC hecho**, **S-06** opción 1, **D-10**):
  iniciar el ciclo de auditoría. El botón ordena (`POST /api/ciclo/iniciar`), la PC
  atiende la consulta `orden_ciclo` en el puerto 5001 y entrega `iniciar` **una sola
  vez**; si la Orange Pi vuelve a preguntar después, se entiende que reinició y espera
  una orden nueva; una orden que nadie recoge vence a los 60 s. El panel muestra tres
  estados (esperando orden / orden enviada / recorrido iniciado) y si la Orange Pi
  consultó hace poco. Contrato en `shared/` y sección 4.7 del informe. **Falta el lado
  Orange Pi** (esperar `iniciar` al arrancar). Pausar/detener (opción 2) no existe.
- [ ] **PC-11** Campos "Lectura OCR", "Fecha de vencimiento" y "Estado FEFO" del
  panel: muestran "—" hasta que el módulo de reconocimiento los entregue
  (depende del equipo de IA, ver **S-05**). *Sigue pendiente del equipo de IA
  (confirmado por el usuario, 2026-10-05).*
- [x] **PC-12** Prueba de punta a punta con la Orange Pi real (ESP32-S3 simulada),
  cerrada tras **OP-21**. Corroborado en el registro y el panel de la PC: 3 lotes
  de 5 imágenes respondidos `TIPO_A`; un lote vacío → alarma `fallo_captura` →
  el regente resolvió `DESCARTE` en el panel. Las fotos llegaron íntegras al
  panel: JPEG válidos de 1920×1080 (433-463 KB c/u). `ufw` inactivo, sin puertos
  que abrir. *Limitaciones:* la ESP32-S3 era simulada, y la PC no puede
  confirmar por sí sola que la Orange Pi recibió el destino (ver **PC-18**). Si
  la PC cambia de red (p. ej. hotspot de la demo) hay que volver a revisar el
  firewall y la IP.
- [ ] **PC-13** Calibrar con hardware real: `TIMEOUT_INACTIVIDAD_RECEPCION_S`,
  `TIMEOUT_RESPUESTA_RECONOCIMIENTO_S`, `INTERVALO_CONSULTA_DECISION_S` y el tope
  de 10 MiB por imagen (este último es una estimación, no una medición).
  *Aún no se puede hacer (usuario, 2026-10-05): falta el hardware real completo.*
- [x] **PC-14** Persistencia del estado del panel: **decidido no persistir por ahora**
  (**D-05**). El historial y la decisión en espera viven solo en RAM; si la PC se
  reinicia, la Orange Pi recibe `ninguna` y se recupera sola con el sondeo de la
  sección 4.6, así que solo se pierde el historial del panel. Se reevalúa si
  hace falta auditoría o trazabilidad. (Las fotos no se guardan, por diseño.)
- [x] **PC-15** Acceso al panel: **el regente trabaja frente a la PC**, así que el
  panel sigue escuchando solo en `127.0.0.1` (sin autenticación; **D-09**). Si
  más adelante el regente usa otro dispositivo, hay que arrancar con
  `PANEL_HOST=0.0.0.0` (cualquiera en esa red podría abrir el panel y resolver
  alarmas) y conviene añadir una clave compartida antes; reevaluar entonces.
- [x] **PC-16** Mejoras del panel pedidas por el usuario (2026-10-05):
  **severidad "amarilla"** de alertas — roja = la Orange Pi la espera (bloquea el
  ciclo), amarilla = sigue activa pero ya nadie la espera (queda por cerrar), gris
  = resuelta; el banner cambia de color igual — y **`logging`** en lugar de
  `print()` (loggers `server`, `decision`, `panel`, `mdns`, `alarma`; nivel con
  `PC_LOG_LEVEL`). *Excepción:* el placeholder `ocr_interface.py`, del equipo de IA.
  *No se harán por ahora (decisión del usuario):* sonido o parpadeo ante alarma y
  revisión en celular/tablet. La severidad amarilla de la maqueta basada en FEFO
  ("próximo a vencer") depende de **PC-11**.
- [x] **PC-18** Registro del servidor de decisión: ahora la PC anota cuando una
  consulta entrega un estado nuevo a la Orange Pi (log `decision`: `<ip> recibió:
  pendiente | resuelta (destino X) | ninguna`), sin una línea por cada consulta de
  2 s; así su log confirma que la Orange Pi recibió el destino. Las conexiones que
  se abren y cierran sin datos (pruebas de conectividad) se muestran como
  "sonda de conectividad", ya no como error.
- [x] **PC-17** Verificar el panel en un navegador real tras cada cambio de CSS/JS:
  el usuario lo ha ido comprobando en las pruebas y **no ha visto más fallos
  visuales** (2026-10-05). Sigue siendo buena práctica revisar el navegador tras
  cada cambio de CSS/JS. Pruebas automáticas de la PC: 28 pasan.

- [~] **PC-19** Asistente conversacional con LLM (`pc/assistant/`), **por fases**
  (ver `pc/assistant/ASSISTANT_CONTEXT.md`, sección 11):
  - [x] **Fase 0:** el motor acepta un LLM inyectado (`Assistant(llm=...)`) y se
    prueba sin modelo (bucle, validación de argumentos, confirmación, comandos
    desconocidos, JSON inválido, pasos agotados).
  - [x] **Fase 1:** tres comandos de **solo lectura** con datos reales del panel
    (`estado_sistema`, `ultima_clasificacion`, `listar_alarmas`) y `resumen_estado()`
    para el prompt. No inventan lo que el sistema aún no sabe (fecha de vencimiento,
    confianza OCR, estado mecánico del carro).
  - [ ] **Fase 2:** elegir modelo (necesario para usar el asistente de verdad) (descargar 2-3 GGUF, varios GB, pide confirmación;
    medir velocidad). Falta decidir la máquina de la demo: la de desarrollo actual no
    tiene GPU NVIDIA, así que correría en CPU.
  - [x] **Fase 3:** chat en el panel web (lateral derecho de la pestaña Inicio, endpoints `/api/asistente/*`,
    confirmación con botones, una solicitud a la vez, propuestas con id). Opcional y
    desactivado por defecto: `ASISTENTE_MODO=prueba` (reglas fijas, **no es un LLM**) o
    `ASISTENTE_MODELO=/ruta/modelo.gguf`. Sin streaming por ahora.
  - [ ] **Fase 4:** acciones; dependen de **S-06** (`iniciar_captura`) y **S-05**
    (`listar_proximas_a_vencer`).
  El asistente **no decide el destino de una alarma** (**D-07**): `limpiar_alarma`
  sigue como stub que no se conecta tal cual. Es independiente del protocolo
  Orange Pi ↔ PC: no cambia nada del contrato ni del lado Orange Pi.

### Fuera de alcance de este repo (a propósito)

- El motor de OCR/clasificación (equipo de IA).

---

## Sección 2 — Lado Orange Pi (`orange-pi/`)

> **Actualizada el 2026-10-06 por el lado Orange Pi** (con Claude Code), tras
> el commit `2ff59ce`. La versión inicial la redactó el lado PC leyendo el
> código; aquí se confirman o corrigen sus líneas. Pruebas del lado Orange Pi:
> `cd orange-pi && python -m unittest discover -s tests` (13 pasan, sin
> hardware) y una prueba de integración contra `server.py` y
> `servidor_decision.py` reales de la PC (lote vacío → alarma → regente
> resuelve `TIPO_B` → `clasificar`), corrida a mano, no incluida en el repo.

### Implementado (según el código y su `CLAUDE.md`)

- [x] **OP-01** Enlace UART con checksum (`uart/uart_link.py`); `/dev/ttyS0`
  verificado con loopback en hardware real el 2026-09-16 y repetido el
  2026-10-06 (mensaje y checksum `1F` íntegros). *Falta el enlace con la
  ESP32-S3 real (OP-16).*
- [x] **OP-02** Descubrimiento mDNS con caché e invalidación (`network/mdns_discovery.py`).
- [x] **OP-03** Cliente TCP del lote con reintentos hasta 30 s (`network/tcp_client.py`).
- [x] **OP-04** Cámara por nombre, captura a 1080p con autoenfoque, JPEG en memoria
  (commit `2b3ffba`). Verificado el 2026-10-06 con la webcam HP de desarrollo:
  1920×1080, JPEG ~430 KB, 0,8 s. *No verificado:* que el autoenfoque enfoque a
  la distancia real de la caja, ni la Arducam USB final.
- [x] **OP-05** `introducir_objeto` con hasta 5 reintentos y ráfaga de 5 caras
  (`main.py`).
- [x] **OP-06** `tests/mock_pc_server.py` para desarrollar sin la PC.

### Divergencias con el contrato — corregidas (ver **S-01**)

- [x] **OP-10** `main.py` consulta la decisión con `network/decision_client.py`
  (puerto 5001, cada 2 s, **sin tope de espera**) en lugar del `input()`.
- [x] **OP-11** `clasificar` usa el destino devuelto (`TIPO_X` o `DESCARTE`).
- [x] **OP-12** `enviar_lote()` devuelve `(respuesta, fallo_de_red)`: la alarma
  local solo se activa ante fallo de red (30 s) o si la PC deja de responder
  durante la espera de la decisión (30 s seguidos de consultas fallidas). Un
  `ERROR_REVISION_MANUAL` respondido por la PC no la activa.
- [x] **OP-13** Si `introducir_objeto` agota sus reintentos se envía un lote
  vacío a la PC.

### Pendiente

- [x] **OP-19** Respuesta `ninguna` mientras se espera (F2): la caja se mantiene y se
  recupera como en la sección 4.6 (lote vacío → decisión nueva); el ciclo ya no se
  detiene.
- [x] **OP-14** `tests/mock_pc_server.py` atiende también el puerto de decisión (5001):
  `--error`, `--segundos`, `--destino`, `--sin-decision`. Probado con el cliente
  real (commit `5899489`).
- [x] **OP-15** Reanudación tras un fallo de comunicación con la PC (F1/F2/F3),
  sección 4.6: `network/recuperacion.py` sondea con **lote vacío** cada
  `INTERVALO_REINTENTO_PC_S` (10 s, sin tope); al primer éxito deja de sondear,
  envía `desactivar_alarma_local` y espera la decisión del regente. El ciclo no se
  reanuda solo (**D-07**). Probado con el mock y en una prueba de integración (PC
  ausente al inicio, vuelve a los 7 s). *Falta* la ESP32-S3 real (**S-09**).
- [~] **OP-16** Prueba de punta a punta con la ESP32-S3 y con la PC reales.
  **Hecho** el tramo Orange Pi ↔ PC real (ver **OP-21**); **falta** la ESP32-S3
  real (enlace UART con firmware, incluido el mapeo de `DESCARTE`, **S-04**).
- [x] **OP-20** `requirements.txt` usa `opencv-python-headless` (la imagen
  Armbian Minimal no trae `libGL`).
- [x] **OP-21** Prueba de punta a punta contra la PC real (2026-10-06),
  `orange-pi/tests/prueba_extremo_a_extremo.py` con cámara real (5 fotos
  1920×1080, ~430-470 KB c/u), mDNS real y la ESP32-S3 simulada (cada movimiento
  tarda 2.5 s). Resultados:
  - Camino normal: la PC respondió `TIPO_A` y se ordenó `clasificar` con ese
    destino (ciclo de ~21 s).
  - Camino del regente: con la confirmación de `introducir_objeto` retrasada
    más allá del timeout, se agotaron los 5 reintentos, se envió el lote vacío,
    la PC abrió la alarma y el regente resolvió `DESCARTE` en el panel; la
    Orange Pi consultó el puerto 5001 y ordenó `clasificar` con `DESCARTE`
    (sin alarma local, como debe ser). **El lado PC puede cerrar PC-12.**
  - Observación: el primer intento de mDNS a veces tarda más de 3 s en el WiFi
    (se resuelve solo con el reintento del descubridor).
- [x] **OP-22** Espera de la orden de inicio del ciclo (**S-06**, sección 4.7):
  `main.py` abre UART y cámara, consulta `orden_ciclo` cada 2 s
  (`network/decision_client.py::esperar_orden_inicio`) y empieza el ciclo al
  recibir `iniciar`; no vuelve a consultar. Sin PC no arranca (reintenta sin tope y
  sin alarma local). `--sin-orden` la omite (solo pruebas, no es contrato).
  Probado con el mock (orden de un solo uso) y contra la PC real: `iniciar` a los
  0.2 s y segunda consulta `esperando`. *No probado aún:* el botón del panel de
  punta a punta con `main.py` corriendo.
- [x] **OP-17** `orange-pi/requirements-lock.txt` generado con `pip freeze` (commit `6a3fb71`).
- [x] **OP-18** Quitados los TODO obsoletos de `camera.py` y `main.py` (commit `f1461ef`).

---

## Solicitudes entre lados

Estados: `Abierta` · `Aceptada` · `En discusión` · `Hecha` · `Rechazada`.

| ID | De → Para | Solicitud | Estado | Notas |
|---|---|---|---|---|
| S-01 | PC → Orange Pi | Implementar el bucle de consulta de decisión y corregir **OP-10 a OP-13**. Contrato: `docs/arquitectura_comunicacion.md` §4.5; cliente de referencia en `pc/tests/mock_orangepi_client.py`. | Hecha | Lado PC: commit `08a76ec`. Lado Orange Pi: commit `2ff59ce` (OP-10 a OP-13). |
| S-02 | PC → Orange Pi (+ ESP32-S3) | Definir cómo se **reanuda el ciclo tras un fallo de comunicación** con la PC (situaciones F1 a F3: 30 s sin respuesta, `ninguna`, PC sin contestar en la espera). El informe solo dice "alarma física local"; no dice qué hace después ni quién desbloquea. Hoy la ESP32-S3 **no puede apagar** la alarma. | Hecha | **Opciones redactadas por el lado PC** en `docs/propuesta_reanudacion_fallo_comunicacion.md`; resumen y tabla de respuestas en la sección "Consulta abierta — S-02" más abajo. **Cada lado responde ahí antes de implementar.** **Cerrada el 2026-10-06** del lado Orange Pi y contrato: Opción 1 con confirmación del regente (**D-06/D-07**), sección 4.6, `ACCION_DESACTIVAR_ALARMA_LOCAL` e `INTERVALO_REINTENTO_PC_S` en `shared/`. Lo que falta de la ESP32-S3 es **S-09**. |
| S-03 | PC → Orange Pi | Confirmar los supuestos del canal de decisión: sin tope de espera al regente, `ninguna` ⇒ mantener caja y activar alarma local, destino con formato `[A-Z0-9_]{1,32}`. | Hecha | Confirmados los dos primeros. Formato del destino: el cliente acepta cualquier cadena no vacía y deja a la PC la validación (`[A-Z0-9_]{1,32}`). |
| S-04 | PC → ESP32-S3 | `ACCION_CLASIFICAR` puede traer `"destino": "DESCARTE"`: mapearlo a su contenedor de descarte. `DESTINO_DESCARTE` ya está en `protocol_constants.h`. | Abierta | Lo gestiona quien lleve el firmware. |
| S-05 | PC → equipo de IA | Definir qué entrega el reconocimiento además de `clasificacion` (confianza OCR, fecha de vencimiento, lote) para llenar el panel y calcular FEFO. Implica ampliar el contrato. | Abierta | Bloquea **PC-11**. |
| S-06 | PC → Orange Pi | ¿El botón "Iniciar recorrido" debe existir? Si sí, hace falta un mensaje PC → Orange Pi (iniciar/pausar ciclo) y un canal; el diseño actual solo tiene Orange Pi → PC. | Hecha | **Opción 1 decidida por ambos lados (D-10).** Lado PC implementado y contrato subido a `shared/` (`CONSULTA_ORDEN_CICLO`, `CLAVE_ORDEN_CICLO_JSON`, `ORDEN_CICLO_ESPERANDO`/`ORDEN_CICLO_INICIAR`, `VIGENCIA_ORDEN_CICLO_S`; sección 4.7 del informe). **Lado Orange Pi implementado (2026-10-06, commit `d7dfc06`, ver OP-22).** Antes: Bloquea **PC-10**. **Aclaración del usuario (2026-10-05):** el botón sería **iniciar el ciclo de auditoría** (no mover el carrito). Pendiente: que la Orange Pi diga si lo quiere y cómo (consulta periódica a la PC, igual que la decisión). **Consulta abierta con opciones y tabla de respuestas más abajo ("Consulta abierta — S-06"); se deja pendiente, sin implementar.** |
| S-08 | Orange Pi → ESP32-S3 (+ equipo) | **Dispensador decidido:** lo controla la ESP32-S3 y `introducir_objeto` se **reemplaza** por `activar_dispensador` (`ACCION_ACTIVAR_DISPENSADOR`); se confirma igual con `objeto_en_posicion` y conserva los 5 reintentos. En `main.ino`: usar el nombre nuevo (el viejo queda como alias en desuso, mismo texto en el cable) e implementar el control real del servo en lugar del TODO. | Abierta | Ver CHANGELOG 2026-10-06. La PC no se ve afectada. |
| S-09 | Orange Pi → ESP32-S3 | Manejar la acción nueva `desactivar_alarma_local` (`ACCION_DESACTIVAR_ALARMA_LOCAL`, sin campos y **sin evento de confirmación**, como `activar_alarma_local`): apagar el zumbador/LED. Sugerencia: alarma **continua** mientras esté encendida. Si la caja se retira a mano, el regente elige `DESCARTE` en el panel y llega un `clasificar` normal; la Orange Pi siempre ordena `clasificar` al final. | Abierta | Sin firmware aún: mientras tanto el mensaje se ignora y la alarma queda encendida. Ver CHANGELOG 2026-10-06 (2) y sección 4.6. |
| S-07 | Orange Pi → PC | Antes de la prueba de punta a punta (**PC-12**): abrir en el firewall de la PC los puertos 5000/TCP, 5001/TCP y 5353/UDP, y avisar cuando el servidor y el panel estén corriendo en la PC real. | Hecha | Firewall: `ufw` está `inactive` en la PC, no hay puertos que abrir. Servidor y panel corriendo con estado limpio en `192.168.20.53` (puertos 5000 y 5001). La Orange Pi descubre la PC por mDNS y usa la misma IP para el puerto 5001. **Si tu prueba de conexión falla, reabre esta solicitud.** Prueba sin instalar nada: `timeout 3 bash -c '</dev/tcp/192.168.20.53/5001' && echo OK`. |

---

## Consulta abierta — S-02: reanudar el ciclo tras un fallo de comunicación

Detalle completo: `docs/propuesta_reanudacion_fallo_comunicacion.md`.
**Cerrada el 2026-10-06 (D-06 / S-02 `Hecha`); se conserva como registro** de las
opciones y de las respuestas de cada lado. Lo que quedó decidido está en la sección
4.6 del informe; lo que falta es solo el firmware de la ESP32-S3 (**S-09**).

**Situaciones:** **F1** se agotan los 30 s enviando el lote · **F2** la PC responde
`ninguna` en la espera (perdió el estado) · **F3** la PC deja de contestar durante
la espera. **Dato clave:** la ESP32-S3 solo puede *encender* la alarma local, no
apagarla.

| Opción | Idea | Sin hardware nuevo | Sin intervención en fallo transitorio | Con la PC caída | Mensajes nuevos | Cambios en PC |
|---|---|---|---|---|---|---|
| **0** | Intervención técnica (reiniciar); estado actual formalizado | Sí | No | Manual | 0 | No |
| **1** | La Orange Pi conserva el lote y **reenvía** hasta que la PC responde; apaga la alarma y sigue el flujo normal | Sí | **Sí** | Espera | 1 (`desactivar_alarma_local`) | **No** |
| **2** | **Botón físico** en el carrito → evento ESP32-S3 → Orange Pi | No | No (confirma el regente) | **Sí** | 2 | No |
| **3** | Desbloqueo desde consola/mini-interfaz en la Orange Pi | Sí | No | Sí | 1 | No |
| 4 | Reanudar por tiempo | Sí | Sí | Sí | 1 | No — **descartada**: contradice "nunca avanzar sin confirmación explícita" |

**Recomendación del lado PC (no es una decisión):** **Opción 1 como base, con la
Opción 0 como límite explícito**; la Opción 2 como segunda fase si en la demo se
quiere tolerar una PC caída.

**Preguntas** (detalle en la sección 6 del documento): ¿se acepta la Opción 1? ·
tras recuperarse la PC, ¿reanuda solo o el regente confirma? (riesgo: caja retirada
a mano) · ¿intervalo de 10 s y sin tope? · ¿la ESP32-S3 puede implementar
`desactivar_alarma_local` y cómo suena el zumbador mientras tanto? · si el regente
retira la caja a mano, ¿el mecanismo necesita igual un `clasificar`?

### Respuestas (cada lado completa SU fila; no edites la del otro)

| Lado | Respuesta (opción preferida y notas) | Quién / fecha |
|---|---|---|
| **PC** | **Acepta el mecanismo del lado Orange Pi** (Opción 1 con confirmación del regente: sondeo con lote vacío cada 10 s, alarma local hasta `desactivar_alarma_local`, decisión del regente en el panel). **No requiere cambios en el protocolo ni en el servidor de la PC.** Matices: (1) el panel mostraba esos lotes como "fallo de captura" aunque la causa sea la red: el lado PC **cambia el texto** de la alarma a *"Lote vacío (captura fallida o reintento tras perder la conexión)"*; (2) la Orange Pi debe **dejar de sondear en cuanto la PC responda por primera vez**, o cada lote vacío creará una alarma nueva y reemplazará la decisión en espera; (3) las fotos del objeto no quedan en el panel (aceptado); (4) la alternativa de reenviar el lote real y forzar la decisión necesitaría una marca nueva en el contrato: **no se recomienda por ahora**. | Arlo.exe (lado PC), 2026-10-05 |
| **Orange Pi** | **Opción 1 con confirmación del regente** (decisión del usuario: así también se cubre que la caja se retire a mano). **Mecanismo propuesto, sin cambios en la PC:** ante F1/F2/F3 la Orange Pi activa la alarma local, mantiene la caja y reintenta cada **10 s, sin tope** (con re-descubrimiento mDNS); el reintento es un **lote vacío**, que sirve de sondeo y fuerza el camino del regente: cuando la PC responde, apaga la alarma local (`desactivar_alarma_local`) y espera la decisión del regente en el panel (sección 4.5), que elige `TIPO_X` o `DESCARTE` (si retiró la caja, `DESCARTE`). Así F2 (`ninguna`) también queda cubierta. **Costo:** las fotos de ese objeto no quedan en el panel. **Alternativa** si se quieren conservar: reenviar el lote real y que la PC abra la decisión aunque el resultado sea automático (cambio en la PC). Alarma local: continua hasta `desactivar_alarma_local`. Esperar a que respondan la ESP32-S3 (preguntas 4 y 5) y a D-06 antes de implementar. | Arlo-qexe (lado Orange Pi), 2026-10-06 |
| **ESP32-S3** | *(pendiente — o quien lleve el firmware)* | |
| **Usuario / regente** | **El ciclo NO se reanuda solo tras un fallo de comunicación: requiere confirmación del regente**, porque durante la alarma la caja puede haberse retirado a mano (en ese caso el regente elige `DESCARTE`). Esta decisión la tomó el usuario (lado PC). | Arlo.exe (usuario), 2026-10-05 |

Acuerdo registrado en **Decisiones tomadas** (D-06 y D-07); S-02 está `Hecha`.

---

## Consulta abierta — S-06: botón "Iniciar recorrido" (iniciar el ciclo de auditoría)

**Estado: opción 1 DECIDIDA (D-10) e implementada en ambos lados (PC y Orange Pi, OP-22).**
Se conserva como registro de las opciones. El botón existe en el panel pero está deshabilitado. Aclaración
del usuario (2026-10-05): **iniciar el ciclo de auditoría**, no mover el carrito.

**Hoy:** `orange-pi/src/main.py` arranca el ciclo continuo apenas se ejecuta
(`while True: ciclo_de_un_objeto(...)`) y solo se detiene con `Ctrl+C` en la
terminal de la Orange Pi. La PC **no puede darle órdenes**: la Orange Pi nunca recibe
conexiones entrantes y el contrato solo tiene mensajes Orange Pi → PC (el lote y la
consulta de decisión).

| Opción | Idea | Mensajes nuevos | Cambios en la Orange Pi | Cambios en la PC | Si la PC no responde |
|---|---|---|---|---|---|
| **0** | **Sin botón**: la Orange Pi sigue arrancando sola; se quita el botón (o queda solo como indicador "en marcha") | 0 | Ninguno | Quitar/ocultar el botón | — |
| **1** | **Solo "iniciar"**: al arrancar, la Orange Pi consulta (`consulta: orden_ciclo`, mismo puerto 5001 y framing que la decisión) hasta recibir `iniciar`; luego corre sola como hoy | 1 consulta + 1 respuesta (`esperando` / `iniciar`) | Esperar la orden antes del primer objeto | Guardar el estado y habilitar el botón | No arranca (sin PC no se puede auditar de todos modos) |
| **2** | **Iniciar + pausar/reanudar + detener**: la Orange Pi consulta la orden deseada **entre objetos** (y cada 2 s si está pausada) | La misma consulta, con 3 estados (`en_marcha` / `pausado` / `detenido`) | Consultar antes de cada objeto (una conexión TCP corta más por objeto) | Estado de la orden + 3 botones | Pregunta 4 |

**Regla común:** pausar o detener solo tiene efecto **entre objetos** (principio
secuencial: nunca se interrumpe uno en vuelo, y si hay una alarma o decisión del
regente pendiente se termina primero).

**Recomendación del lado PC (no es una decisión):** **Opción 1** como primer paso,
que es lo que describe el usuario; está pensada para extenderse a la 2 sumando
estados a la misma consulta, sin otro canal.

**Preguntas:**
1. **Orange Pi:** ¿el ciclo debe **esperar una orden de la PC al arrancar**? Hoy
   arranca solo; sería un cambio de hábito (por ejemplo, para pruebas sueltas).
2. **Usuario:** ¿solo iniciar (opción 1) o también pausar/detener (opción 2)?
3. **Todos:** ¿qué significa "detener": parar y esperar una nueva orden
   (propuesta), o terminar el proceso?
4. **Orange Pi (opción 2):** si la PC no contesta la consulta entre objetos,
   ¿continuar con el último estado o detenerse? Propuesta: continuar, porque si la
   PC está caída el envío del lote ya dispara la recuperación de la sección 4.6.
5. **ESP32-S3:** no se ve afectada (la orden nunca viaja por UART). Opcional: ¿se
   quiere un indicador físico de "pausado"?

### Respuestas S-06 (cada lado completa SU fila; no edites la del otro)

| Lado | Respuesta (opción preferida y notas) | Quién / fecha |
|---|---|---|
| **PC** | **Opción 1, implementada.** Contrato en `shared/`, servidor de consultas, estado de la orden y botón del panel; 15 pruebas nuevas. La orden es de un solo uso y vence a los 60 s (ver CHANGELOG 2026-10-06 (3)). | Arlo.exe (lado PC), 2026-10-06 |
| **Orange Pi** | **Opción 1** (decisión del usuario). (1) **Sí**: el ciclo debe esperar la orden de la PC al arrancar; se acepta el cambio de hábito. Al iniciar `main.py`, tras abrir UART y cámara, consulta `orden_ciclo` (puerto 5001, mismo framing y cada 2 s, con re-descubrimiento mDNS) hasta recibir `iniciar`, y luego corre solo como hoy; **no vuelve a consultar** después de `iniciar`. (2) **Sin PC no arranca**: reintenta indefinidamente, **sin alarma local** (no hay objeto en vuelo; solo un mensaje en consola). (3) Para pruebas sueltas sin PC, `main.py` tendrá una opción local `--sin-orden` que se salta la espera; **no es parte del contrato**. (4) Pregunta 4 (si se extiende a la opción 2): **continuar con el último estado** si la PC no contesta entre objetos, porque el envío del lote ya dispara la recuperación de 4.6. (5) **Esperando constantes en `shared/`** (`CONSULTA_ORDEN_CICLO`, claves y estados `esperando`/`iniciar`): el lado PC se ofreció a agregarlas; implemento el lado Orange Pi en cuanto estén. | Arlo-qexe (lado Orange Pi), 2026-10-06 |
| **ESP32-S3** | *(no afectada; pregunta 5 opcional)* | |
| **Usuario** | Interpretación: **iniciar el ciclo de auditoría** (no mover el carrito). **Confirma la opción 1** (solo iniciar). | Arlo.exe (usuario), 2026-10-06 |

---

## Decisiones tomadas

| ID | Fecha | Decisión | Quién |
|---|---|---|---|
| D-01 | 2026-10-05 | Canal de la decisión del regente: **opción A** (la Orange Pi consulta por TCP corto, puerto 5001). Ver `docs/propuesta_canal_regente.md`. | Lado Orange Pi (comunicado por el usuario) |
| D-02 | 2026-10-05 | El panel de control es una página web generada por la PC con la biblioteca estándar de Python; se migra a Flask solo si crece. | Usuario |
| D-03 | 2026-10-05 | El panel muestra las fotos del lote, solo desde RAM (últimos 5 lotes), nunca a disco. | Usuario |
| D-04 | 2026-10-05 | Timeout de inactividad 10 s, imagen máx. 10 MiB, máx. 5 imágenes por lote (valores iniciales, a calibrar). | Usuario aprobó los valores propuestos |
| D-05 | 2026-10-05 | **No persistir** el historial ni la decisión en espera del panel por ahora (**PC-14**): la Orange Pi se recupera sola tras un reinicio de la PC (sección 4.6). Se reevalúa si hace falta auditoría. | Usuario |
| D-10 | 2026-10-06 | **Opción 1 de S-06:** el ciclo de auditoría espera una orden `iniciar` de la PC al arrancar; la da el regente con el botón "Iniciar recorrido". Solo iniciar (sin pausar/detener, que queda fuera). La orden es de un solo uso y vence a los 60 s. | Usuario (PC y Orange Pi) |
| D-09 | 2026-10-05 | El panel de la PC sigue escuchando solo en `127.0.0.1`: el regente trabaja frente a la PC (**PC-15**). | Usuario |
| D-06 | 2026-10-06 | **Cerrada** (del lado Orange Pi y contrato): tras un fallo de comunicación, sondeo con lote vacío cada 10 s sin tope, alarma local hasta `desactivar_alarma_local` y confirmación del regente en el panel (sección 4.6). La ESP32-S3 se adapta después (**S-09**), según el usuario. | Usuario + lados PC y Orange Pi |
| D-07 | 2026-10-05 | Tras un fallo de comunicación con la PC el ciclo **no se reanuda solo**: requiere la confirmación del regente en el panel (`TIPO_X`, o `DESCARTE` si retiró la caja a mano). Descarta la reanudación automática. | Usuario |
| D-08 | 2026-10-06 | El servo del dispensador lo controla la **ESP32-S3**; `activar_dispensador` reemplaza a `introducir_objeto` (ver `docs/CHANGELOG_protocolo.md`, **S-08**). | Lado Orange Pi |

---

## Registro de cambios de este documento

| Fecha | Quién | Cambio |
|---|---|---|
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | OP-22 nuevo: `main.py` espera la orden `iniciar` al arrancar (S-06 opción 1); S-06 pasa a `Hecha`. |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | Respuesta del lado Orange Pi a la consulta S-06: Opción 1 (esperar la orden `iniciar` al arrancar). |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | PC-14 (no persistir, **D-05** decidida), PC-16 (severidad amarilla y `logging`), PC-17 (verificado por el usuario) `[x]`; PC-11 y PC-13 anotados como pendientes de otros / de hardware; sección "Consulta abierta — S-02" marcada como cerrada. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | PC-15 `[x]` (D-09: panel solo local, el regente está en la PC); PC-10 y S-06 anotan que "Iniciar recorrido" sería iniciar el ciclo de auditoría. |
| 2026-10-06 | Arlo.exe (lado PC, con Claude Code) | S-06 `Aceptada` y D-10 decidida (opción 1); PC-10 `[x]` del lado PC: constantes y esquema en `shared/`, sección 4.7 del informe, `servidor_decision` atiende `orden_ciclo`, botón del panel. El asistente pasa de pestaña propia al lateral derecho de Inicio. |
| 2026-10-06 | Arlo.exe (lado PC, con Claude Code) | PC-19 fase 3 `[x]`: el asistente en el panel web (opcional, modo de prueba sin modelo). Sin cambios de contrato. |
| 2026-10-06 | Arlo.exe (lado PC, con Claude Code) | PC-19 pasa a `[~]`: asistente LLM, fases 0 (motor probado con LLM falso) y 1 (comandos de solo lectura con datos reales) hechas; fases 2 a 4 pendientes. Sin cambios de contrato. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | Consulta abierta S-06 (botón "Iniciar recorrido": opciones 0 a 2, preguntas y tabla de respuestas), D-10 pendiente; PC-19 nuevo (asistente LLM por revisar). |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | S-02 `Hecha` y D-06 cerrada: se implementa la recuperación de la sección 4.6 (`desactivar_alarma_local`, `INTERVALO_REINTENTO_PC_S`, bucle en la Orange Pi). OP-15 y OP-19 `[x]`; S-09 nueva para la ESP32-S3. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | Fila PC y fila Usuario de la consulta S-02; D-06 actualizado; D-07 (confirmación del regente) y D-08 (dispensador en la ESP32-S3) registradas. El panel cambia el texto de la alarma de lote vacío. |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | Respuesta del lado Orange Pi a la consulta S-02: Opción 1 con confirmación del regente (mecanismo y alternativa en su fila). |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | OP-17 y OP-18 `[x]`; S-08 nueva: el dispensador lo controla la ESP32-S3 con `activar_dispensador`, que reemplaza a `introducir_objeto`. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | PC-18 `[x]` (registro de consultas de decisión); S-02 pasa a `En discusión` con la consulta "Consulta abierta — S-02" y su tabla de respuestas; D-06 pendiente. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | PC-12 `[x]` tras revisar OP-21 contra el registro y el panel de la PC; PC-18 nuevo. |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | OP-21 nuevo (prueba de punta a punta contra la PC real: camino normal y camino del regente); OP-16 pasa a `[~]` (falta la ESP32-S3 real). |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | OP-14 `[x]`: el mock atiende el puerto 5001. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | S-07 `Hecha` (firewall inactivo verificado, servidor corriendo limpio); PC-12 pasa a `[~]`. |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | Sección 2 actualizada (OP-10 a OP-13 corregidos, OP-01/OP-04 verificados, OP-19/OP-20 nuevos); S-01 y S-03 `Hecha`; S-07 agregada. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | Creación. Estado del lado PC verificado; estado del lado Orange Pi redactado por lectura del código, con 4 divergencias señaladas (OP-10 a OP-13). |
