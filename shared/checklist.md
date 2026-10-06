# Checklist de coordinación — PC ↔ Orange Pi

> Documento vivo para que **ambos lados** vean el estado del otro, se hagan
> solicitudes y avisen de cambios o pendientes **antes de continuar**. No
> reemplaza al contrato (`protocol_constants.py`) ni al
> `docs/CHANGELOG_protocolo.md`: complementa a ambos con el "quién hace qué y
> en qué punto está".

**Última actualización:** 2026-10-05 (reloj de la PC) — Arlo.exe (con Claude Code, lado PC: respuesta a S-02, D-07/D-08).
Anterior: 2026-10-06 (reloj de la Orange Pi) — Arlo-qexe (lado Orange Pi: respuesta a S-02, S-08).

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

- [ ] **PC-10** Botón "Iniciar recorrido" del panel: hoy deshabilitado. Necesita
  un mensaje PC → Orange Pi que **no existe en el contrato** (ver **S-06**).
- [ ] **PC-11** Campos "Lectura OCR", "Fecha de vencimiento" y "Estado FEFO" del
  panel: muestran "—" hasta que el módulo de reconocimiento los entregue
  (depende del equipo de IA, ver **S-05**).
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
- [?] **PC-14** El estado del panel (historial y decisión en espera) vive solo
  en RAM: si la PC se reinicia, la Orange Pi recibe `ninguna`. ¿Se persiste?
  (Las fotos no, por diseño.) Ver **D-05**.
- [?] **PC-15** El panel escucha en `127.0.0.1` por defecto (sin autenticación).
  Si el regente lo abre desde otro equipo hay que usar `PANEL_HOST=0.0.0.0`.
  ¿Quién usa el panel y desde dónde en la demo?
- [ ] **PC-16** Mejoras menores del panel: revisión en celular/tablet, sonido o
  parpadeo ante alarma, severidad "amarilla" de alertas, `logging` en vez de
  `print()`.
- [x] **PC-18** Registro del servidor de decisión: ahora la PC anota cuando una
  consulta entrega un estado nuevo a la Orange Pi (`[decision] <ip> recibió:
  pendiente | resuelta (destino X) | ninguna`), sin una línea por cada consulta de
  2 s; así su log confirma que la Orange Pi recibió el destino. Las conexiones que
  se abren y cierran sin datos (pruebas de conectividad) se muestran como
  "sonda de conectividad", ya no como error. 26 pruebas pasan.
- [ ] **PC-17** Verificar el panel en un navegador real tras cada cambio de CSS/JS
  (hasta hoy solo se probó el servidor y la sintaxis; los fallos visuales los ha
  encontrado el usuario).

### Fuera de alcance de este repo (a propósito)

- El motor de OCR/clasificación y `pc/assistant/` (equipo de IA / pendiente).

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

- [~] **OP-19** Si la PC responde `ninguna` mientras se espera: se activa la
  alarma local, la caja se queda en posición y el ciclo **se detiene** (no hay
  forma definida de reanudarlo; mismo hueco que **S-02**).
- [x] **OP-14** `tests/mock_pc_server.py` atiende también el puerto de decisión (5001):
  `--error`, `--segundos`, `--destino`, `--sin-decision`. Probado con el cliente
  real (commit `5899489`).
- [?] **OP-15** ¿Cómo se reanuda el ciclo tras un **fallo de comunicación** con la
  PC? Hoy no está definido (ver **S-02**).
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
- [x] **OP-17** `orange-pi/requirements-lock.txt` generado con `pip freeze` (commit `6a3fb71`).
- [x] **OP-18** Quitados los TODO obsoletos de `camera.py` y `main.py` (commit `f1461ef`).

---

## Solicitudes entre lados

Estados: `Abierta` · `Aceptada` · `En discusión` · `Hecha` · `Rechazada`.

| ID | De → Para | Solicitud | Estado | Notas |
|---|---|---|---|---|
| S-01 | PC → Orange Pi | Implementar el bucle de consulta de decisión y corregir **OP-10 a OP-13**. Contrato: `docs/arquitectura_comunicacion.md` §4.5; cliente de referencia en `pc/tests/mock_orangepi_client.py`. | Hecha | Lado PC: commit `08a76ec`. Lado Orange Pi: commit `2ff59ce` (OP-10 a OP-13). |
| S-02 | PC → Orange Pi (+ ESP32-S3) | Definir cómo se **reanuda el ciclo tras un fallo de comunicación** con la PC (situaciones F1 a F3: 30 s sin respuesta, `ninguna`, PC sin contestar en la espera). El informe solo dice "alarma física local"; no dice qué hace después ni quién desbloquea. Hoy la ESP32-S3 **no puede apagar** la alarma. | En discusión | **Opciones redactadas por el lado PC** en `docs/propuesta_reanudacion_fallo_comunicacion.md`; resumen y tabla de respuestas en la sección "Consulta abierta — S-02" más abajo. **Cada lado responde ahí antes de implementar.** |
| S-03 | PC → Orange Pi | Confirmar los supuestos del canal de decisión: sin tope de espera al regente, `ninguna` ⇒ mantener caja y activar alarma local, destino con formato `[A-Z0-9_]{1,32}`. | Hecha | Confirmados los dos primeros. Formato del destino: el cliente acepta cualquier cadena no vacía y deja a la PC la validación (`[A-Z0-9_]{1,32}`). |
| S-04 | PC → ESP32-S3 | `ACCION_CLASIFICAR` puede traer `"destino": "DESCARTE"`: mapearlo a su contenedor de descarte. `DESTINO_DESCARTE` ya está en `protocol_constants.h`. | Abierta | Lo gestiona quien lleve el firmware. |
| S-05 | PC → equipo de IA | Definir qué entrega el reconocimiento además de `clasificacion` (confianza OCR, fecha de vencimiento, lote) para llenar el panel y calcular FEFO. Implica ampliar el contrato. | Abierta | Bloquea **PC-11**. |
| S-06 | PC → Orange Pi | ¿El botón "Iniciar recorrido" debe existir? Si sí, hace falta un mensaje PC → Orange Pi (iniciar/pausar ciclo) y un canal; el diseño actual solo tiene Orange Pi → PC. | Abierta | Bloquea **PC-10**. |
| S-08 | Orange Pi → ESP32-S3 (+ equipo) | **Dispensador decidido:** lo controla la ESP32-S3 y `introducir_objeto` se **reemplaza** por `activar_dispensador` (`ACCION_ACTIVAR_DISPENSADOR`); se confirma igual con `objeto_en_posicion` y conserva los 5 reintentos. En `main.ino`: usar el nombre nuevo (el viejo queda como alias en desuso, mismo texto en el cable) e implementar el control real del servo en lugar del TODO. | Abierta | Ver CHANGELOG 2026-10-06. La PC no se ve afectada. |
| S-07 | Orange Pi → PC | Antes de la prueba de punta a punta (**PC-12**): abrir en el firewall de la PC los puertos 5000/TCP, 5001/TCP y 5353/UDP, y avisar cuando el servidor y el panel estén corriendo en la PC real. | Hecha | Firewall: `ufw` está `inactive` en la PC, no hay puertos que abrir. Servidor y panel corriendo con estado limpio en `192.168.20.53` (puertos 5000 y 5001). La Orange Pi descubre la PC por mDNS y usa la misma IP para el puerto 5001. **Si tu prueba de conexión falla, reabre esta solicitud.** Prueba sin instalar nada: `timeout 3 bash -c '</dev/tcp/192.168.20.53/5001' && echo OK`. |

---

## Consulta abierta — S-02: reanudar el ciclo tras un fallo de comunicación

Detalle completo: `docs/propuesta_reanudacion_fallo_comunicacion.md`. **No hay nada
implementado ni decidido**; las opciones tocan a la Orange Pi, a la ESP32-S3 y al
contrato.

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

Cuando haya acuerdo, se registra en **Decisiones tomadas** (D-06) y S-02 pasa a `Hecha`.

---

## Decisiones tomadas

| ID | Fecha | Decisión | Quién |
|---|---|---|---|
| D-01 | 2026-10-05 | Canal de la decisión del regente: **opción A** (la Orange Pi consulta por TCP corto, puerto 5001). Ver `docs/propuesta_canal_regente.md`. | Lado Orange Pi (comunicado por el usuario) |
| D-02 | 2026-10-05 | El panel de control es una página web generada por la PC con la biblioteca estándar de Python; se migra a Flask solo si crece. | Usuario |
| D-03 | 2026-10-05 | El panel muestra las fotos del lote, solo desde RAM (últimos 5 lotes), nunca a disco. | Usuario |
| D-04 | 2026-10-05 | Timeout de inactividad 10 s, imagen máx. 10 MiB, máx. 5 imágenes por lote (valores iniciales, a calibrar). | Usuario aprobó los valores propuestos |
| D-05 | — | *(pendiente)* Persistir o no el historial/decisión del panel (**PC-14**). | — |
| D-06 | — | *(pendiente de cierre)* Cómo se reanuda el ciclo tras un fallo de comunicación (**S-02**). **Avanzado:** mecanismo de la Orange Pi aceptado por la PC y confirmación del regente decidida (**D-07**). **Falta:** la respuesta de la ESP32-S3 (¿`desactivar_alarma_local`? ¿qué hace el zumbador?) y agregar a `shared/` la constante y el mensaje nuevos. | — |
| D-07 | 2026-10-05 | Tras un fallo de comunicación con la PC el ciclo **no se reanuda solo**: requiere la confirmación del regente en el panel (`TIPO_X`, o `DESCARTE` si retiró la caja a mano). Descarta la reanudación automática. | Usuario |
| D-08 | 2026-10-06 | El servo del dispensador lo controla la **ESP32-S3**; `activar_dispensador` reemplaza a `introducir_objeto` (ver `docs/CHANGELOG_protocolo.md`, **S-08**). | Lado Orange Pi |

---

## Registro de cambios de este documento

| Fecha | Quién | Cambio |
|---|---|---|
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
