# Checklist de coordinación — PC ↔ Orange Pi

> Documento vivo para que **ambos lados** vean el estado del otro, se hagan
> solicitudes y avisen de cambios o pendientes **antes de continuar**. No
> reemplaza al contrato (`protocol_constants.py`) ni al
> `docs/CHANGELOG_protocolo.md`: complementa a ambos con el "quién hace qué y
> en qué punto está".

**Última actualización:** 2026-10-05 (reloj de la PC) — Arlo.exe (con Claude Code, lado PC: PC-12 y S-07).
Anterior: 2026-10-06 (reloj de la Orange Pi) — Arlo-qexe (lado Orange Pi: sección 2, S-01/S-03/S-07).

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
- [~] **PC-12** Prueba de punta a punta con la Orange Pi real. **Preparada del
  lado PC:** `ufw` está `inactive` (verificado con `sudo ufw status verbose`), así
  que no hay nada que abrir, y el servidor corre con estado limpio en
  `192.168.20.53` (5000/TCP lotes, 5001/TCP decisión; panel solo local en 8080).
  **Falta:** que la Orange Pi confirme la conexión desde su lado y hacer el ciclo
  completo. Nota: si la PC cambia de red (p. ej. hotspot de la demo) hay que
  volver a revisar el firewall y la IP.
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
- [ ] **OP-14** Actualizar `tests/mock_pc_server.py` para que también atienda el
  puerto de decisión (hay un cliente de referencia de una consulta en
  `pc/tests/mock_orangepi_client.py::consultar_decision`).
- [?] **OP-15** ¿Cómo se reanuda el ciclo tras un **fallo de comunicación** con la
  PC? Hoy no está definido (ver **S-02**).
- [ ] **OP-16** Prueba de punta a punta con la ESP32-S3 y con la PC reales.
- [x] **OP-20** `requirements.txt` usa `opencv-python-headless` (la imagen
  Armbian Minimal no trae `libGL`).
- [ ] **OP-17** `requirements-lock.txt` (lo pide su `CLAUDE.md` cuando el entorno
  funcione de punta a punta).
- [ ] **OP-18** Limpieza: el TODO de `camera.py:91` (`capturar_ráfaga_completa`)
  parece obsoleto porque `main.py` ya tiene `capturar_lote_completo`.

---

## Solicitudes entre lados

Estados: `Abierta` · `Aceptada` · `En discusión` · `Hecha` · `Rechazada`.

| ID | De → Para | Solicitud | Estado | Notas |
|---|---|---|---|---|
| S-01 | PC → Orange Pi | Implementar el bucle de consulta de decisión y corregir **OP-10 a OP-13**. Contrato: `docs/arquitectura_comunicacion.md` §4.5; cliente de referencia en `pc/tests/mock_orangepi_client.py`. | Hecha | Lado PC: commit `08a76ec`. Lado Orange Pi: commit `2ff59ce` (OP-10 a OP-13). |
| S-02 | PC → Orange Pi (+ equipo) | Definir cómo se **reanuda el ciclo tras un fallo de comunicación** con la PC. El informe solo dice "alarma física local"; no dice qué `destino` se usa después ni quién desbloquea. Opciones a discutir: botón físico vía ESP32-S3, reintento de consulta cuando la PC vuelva, etc. | Abierta | Sin propuesta formal todavía; el lado PC puede redactarla. |
| S-03 | PC → Orange Pi | Confirmar los supuestos del canal de decisión: sin tope de espera al regente, `ninguna` ⇒ mantener caja y activar alarma local, destino con formato `[A-Z0-9_]{1,32}`. | Hecha | Confirmados los dos primeros. Formato del destino: el cliente acepta cualquier cadena no vacía y deja a la PC la validación (`[A-Z0-9_]{1,32}`). |
| S-04 | PC → ESP32-S3 | `ACCION_CLASIFICAR` puede traer `"destino": "DESCARTE"`: mapearlo a su contenedor de descarte. `DESTINO_DESCARTE` ya está en `protocol_constants.h`. | Abierta | Lo gestiona quien lleve el firmware. |
| S-05 | PC → equipo de IA | Definir qué entrega el reconocimiento además de `clasificacion` (confianza OCR, fecha de vencimiento, lote) para llenar el panel y calcular FEFO. Implica ampliar el contrato. | Abierta | Bloquea **PC-11**. |
| S-06 | PC → Orange Pi | ¿El botón "Iniciar recorrido" debe existir? Si sí, hace falta un mensaje PC → Orange Pi (iniciar/pausar ciclo) y un canal; el diseño actual solo tiene Orange Pi → PC. | Abierta | Bloquea **PC-10**. |
| S-07 | Orange Pi → PC | Antes de la prueba de punta a punta (**PC-12**): abrir en el firewall de la PC los puertos 5000/TCP, 5001/TCP y 5353/UDP, y avisar cuando el servidor y el panel estén corriendo en la PC real. | Hecha | Firewall: `ufw` está `inactive` en la PC, no hay puertos que abrir. Servidor y panel corriendo con estado limpio en `192.168.20.53` (puertos 5000 y 5001). La Orange Pi descubre la PC por mDNS y usa la misma IP para el puerto 5001. **Si tu prueba de conexión falla, reabre esta solicitud.** Prueba sin instalar nada: `timeout 3 bash -c '</dev/tcp/192.168.20.53/5001' && echo OK`. |

---

## Decisiones tomadas

| ID | Fecha | Decisión | Quién |
|---|---|---|---|
| D-01 | 2026-10-05 | Canal de la decisión del regente: **opción A** (la Orange Pi consulta por TCP corto, puerto 5001). Ver `docs/propuesta_canal_regente.md`. | Lado Orange Pi (comunicado por el usuario) |
| D-02 | 2026-10-05 | El panel de control es una página web generada por la PC con la biblioteca estándar de Python; se migra a Flask solo si crece. | Usuario |
| D-03 | 2026-10-05 | El panel muestra las fotos del lote, solo desde RAM (últimos 5 lotes), nunca a disco. | Usuario |
| D-04 | 2026-10-05 | Timeout de inactividad 10 s, imagen máx. 10 MiB, máx. 5 imágenes por lote (valores iniciales, a calibrar). | Usuario aprobó los valores propuestos |
| D-05 | — | *(pendiente)* Persistir o no el historial/decisión del panel (**PC-14**). | — |

---

## Registro de cambios de este documento

| Fecha | Quién | Cambio |
|---|---|---|
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | S-07 `Hecha` (firewall inactivo verificado, servidor corriendo limpio); PC-12 pasa a `[~]`. |
| 2026-10-06 | Arlo-qexe (lado Orange Pi, con Claude Code) | Sección 2 actualizada (OP-10 a OP-13 corregidos, OP-01/OP-04 verificados, OP-19/OP-20 nuevos); S-01 y S-03 `Hecha`; S-07 agregada. |
| 2026-10-05 | Arlo.exe (lado PC, con Claude Code) | Creación. Estado del lado PC verificado; estado del lado Orange Pi redactado por lectura del código, con 4 divergencias señaladas (OP-10 a OP-13). |
