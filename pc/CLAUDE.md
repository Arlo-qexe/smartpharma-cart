# Contexto: lado PC (servidor de reconocimiento)

Este código corre en la PC y es responsable de:

1. Anunciarse en la red por mDNS para que la Orange Pi lo descubra automáticamente.
2. Recibir el lote de imágenes por TCP (una conexión nueva por lote).
3. Ejecutar el reconocimiento (OCR/clasificación) — **caja negra para este repo**, ver más abajo.
4. Responder con la clasificación, y activar la alarma en el panel de control cuando corresponda.

## Entorno (venv)

Este lado corre en la PC (x86_64, GPU NVIDIA) — **nunca** se comparte el
entorno virtual con `orange-pi/` (ARM64), cada máquina crea el suyo:

```bash
bash scripts/setup_venv.sh            # crea .venv/, instala requirements.txt + assistant/requirements.txt (CPU)
CUDA=1 bash scripts/setup_venv.sh     # además compila llama-cpp-python con soporte CUDA, para pc/assistant/
source .venv/bin/activate             # en cada terminal nueva
```

- `.venv/` está en `.gitignore` — nunca se commitea.
- El hook `SessionStart` (ver `.claude/settings.json`) avisa automáticamente
  al agente si `.venv/` no existe todavía en una sesión nueva.
- Una vez el entorno funcione de punta a punta, congela las versiones
  exactas para reproducibilidad: `pip freeze > requirements-lock.txt`
  (ese archivo sí se commitea, a diferencia de `.venv/`).
- Descargar un modelo GGUF (varios GB) para `pc/assistant/models/` es una
  acción que pide confirmación explícita (regla `ask` en `.claude/settings.json`
  para `curl`/`wget`) — nunca se dispara automáticamente.

## Antes de escribir código

- Importa TODO desde `../shared/protocol_constants.py` — nunca repitas a mano
  un nombre de mensaje, timeout o puerto.
- Consulta @../docs/arquitectura_comunicacion.md (secciones 3, 4, 7 y 8) para
  el "por qué" de cada decisión.
- Si necesitas cambiar el formato de la respuesta o algún timeout, ese cambio
  va primero en `../shared/`, junto con una entrada en
  `../docs/CHANGELOG_protocolo.md`.

## Reconocimiento con la IA (Parte D, PC-23): `PC_RECONOCIMIENTO`

`src/ocr_interface.py` tiene dos modos: `placeholder` (por defecto, al azar) e `ia`
(`PC_RECONOCIMIENTO=ia bash scripts/start_pc.sh`), que ejecuta `IA/` **en este proceso** con
`IA/modelos/v1_celular/best.onnx` (o `IA_MODELO=/ruta`; `IA_MODELO=` vacío = sin detector). Requiere
`IA=1 bash scripts/setup_venv.sh` (`requirements-ia.txt`, torch CPU). Al arrancar carga y prueba el
modelo; si falla, el servidor no arranca. El OCR llena **sin confirmación** medicamento, gramaje, lote,
vencimiento y confianza del inventario; las correcciones humanas quedan en el registro. Un lote se
reconoce en ~3,5 s de media (máx. ~7 s medido; la Orange Pi espera 10 s). Ojo: los rangos FEFO de
`IA/config/reconocimiento.json` son PLACEHOLDER y en este modo el carrito clasifica con ellos.

## El motor de OCR/clasificación es una caja negra aquí

`src/ocr_interface.py` define el **contrato** de entrada/salida con el
módulo de reconocimiento (que desarrolla el equipo de IA), no su
implementación. Mientras ese equipo no entregue su módulo real, usa el
placeholder ya incluido para poder probar todo el transporte de datos de
punta a punta. **No implementes aquí ningún modelo de OCR** — cuando el
equipo de IA entregue su código, se conecta reemplazando el cuerpo de
`procesar_lote_ocr()`, sin tocar nada de `server.py`.

## Cómo arrancar el lado PC

```bash
bash pc/scripts/start_pc.sh        # desde cualquier carpeta; Ctrl+C para detener
```

Activa el venv, deja fuera el `PYTHONPATH` de ROS, avisa con un mensaje claro si falta
el venv o si otro servidor ya ocupa los puertos, y lanza `src/server.py` con la
**configuración de pruebas actual**: asistente en modo de prueba (sin modelo, reglas
fijas). Los puertos se leen de `shared/` y `panel_web.py`, no están escritos en el script.
Se puede sobrescribir al lanzar: `ASISTENTE_MODO= bash pc/scripts/start_pc.sh` (vacío =
asistente desactivado), `ASISTENTE_MODELO=/ruta/modelo.gguf`, `PC_LOG_LEVEL=DEBUG`,
`PANEL_PUERTO=...`, `PC_DB=...` (archivo del registro de eventos; `:memory:` = sin persistencia),
`PC_TURNO_MIN=...` (minutos de inactividad que cierran un turno; 30 por defecto), `PC_VENV=...`.
No hace `git pull`: hazlo antes si hace falta. **La primera vez hay que crear un administrador**
(`bash scripts/usuarios.sh crear-admin`, en una terminal; sin terminal: `--clave-env VARIABLE`); sin él nadie
puede iniciar turno ni operar el panel, y el banner de arranque lo avisa.

### Red y qué IP anuncia la PC

La PC se anuncia por mDNS con **una sola IP, elegida al arrancar** (`mdns_service.py::elegir_ip_anunciada`):
1) `PC_IP` si se define; 2) la de la ruta por defecto; 3) sin ruta por defecto, la primera IPv4
utilizable de las interfaces reales (se descartan loopback, enlace-local `169.254.x` y las
virtuales de Docker/puentes); 4) `127.0.0.1` solo si no hay nada mejor (el log lo advierte:
la Orange Pi no podría conectarse). **El hostname no se usa**: en Ubuntu resuelve a `127.0.1.1`.
`start_pc.sh` muestra la IP elegida y de dónde sale. Reglas prácticas:
- **Conecta la PC a la red de la demo antes de arrancar**; si cambias de red con el servidor
  corriendo, reinícialo (la IP no se actualiza sola).
- Con varias redes activas (p. ej. Ethernet con Internet + Wi-Fi de la demo), fuerza la
  correcta: `PC_IP=192.168.x.y bash pc/scripts/start_pc.sh`.
- La PC no necesita Internet. El firewall (`ufw`) es del equipo, no de la red: si se activa,
  abrir 5000/TCP, 5001/TCP y 5353/UDP.

## Cómo probar sin la Orange Pi real

- **Orange Pi simulada con fotos ya tomadas** (`bash pc/scripts/simular_orangepi.sh`, con el servidor corriendo):
  recorre el ciclo real (espera el botón «Iniciar recorrido», consulta `estado_ciclo`, envía el lote de 5 fotos,
  espera la decisión del regente si hay error) con las cajas de `IA/dataset_ia/roboflow/roboflow_v1.zip` o de otra
  carpeta/zip (`--fotos`, nombres `cajaNNN_caraK.jpg`). Opciones: `--sin-orden`, `--cajas 004 005`, `--repetir`,
  `--lote-vacío-cada N`, `--ip`. Combinado con `PC_RECONOCIMIENTO=ia` prueba el reconocimiento de punta a punta.
  Escribe en la base que use el servidor (`PC_DB=:memory:` para no ensuciar la real). No es parte del contrato.

- `tests/mock_orangepi_client.py` simula el cliente: se conecta, envía un
  lote de imágenes de prueba (bytes aleatorios) y muestra la respuesta.
  Úsalo para probar `server.py` sin depender del hardware real:

  ```bash
  # en una terminal:
  python3 src/server.py
  # en otra terminal:
  python3 tests/mock_orangepi_client.py
  ```

## Estructura

- `src/server.py` — servidor TCP (recepción de lotes, framing, orquestación de la respuesta).
- `src/mdns_service.py` — registro del servicio `_ocr-service._tcp.local.`.
- `src/ocr_interface.py` — contrato con el módulo de reconocimiento (placeholder).
- `src/servidor_decision.py` — servidor de consultas de decisión del regente (puerto 5001); `src/framing.py` — framing JSON compartido.
- `src/asistente_api.py` — contrato mínimo entre el panel y el servicio del asistente (sin pydantic ni llama-cpp).
- `assistant/comandos_panel.py` — comandos de solo lectura del asistente sobre el estado del panel (ver `assistant/CLAUDE.md`).
- `src/registro.py` — configuración de logging (`PC_LOG_LEVEL`).
- `src/estado_panel.py` — estado en memoria (lotes, fotos, alarmas) compartido entre el servidor y el panel.
- `src/auditoria.py` — **registro de eventos** persistente (SQLite, solo escritura, cadena de hashes). Ver abajo.
- `src/inventario.py` — inventario de cajas (Parte C): una fila por caja auditada. Ver abajo.
- `src/usuarios.py` — usuarios, turnos y permisos (Parte B); `src/usuarios_cli.py` + `scripts/usuarios.sh` — consola para crear el primer administrador. Ver abajo.
- `src/panel_web.py` + `src/panel_static/` — panel de control web (HTML/CSS/JS propios, sin dependencias).
- `tests/test_server.py`, `tests/test_panel.py`, `tests/test_decision.py`, `tests/test_assistant.py`, `tests/test_asistente_panel.py`, `tests/test_orden_ciclo.py`, `tests/test_detener_ciclo.py`, `tests/test_auditoria.py`, `tests/test_usuarios.py`, `tests/test_sesion_panel.py`, `tests/test_inventario.py`, `tests/test_inventario_panel.py` — pruebas con pytest (ver el comando en `tests/test_server.py`; en máquinas con ROS usar `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).
- `tests/mock_orangepi_client.py` — cliente falso para probar el servidor de forma aislada.
- `assistant/` — asistente conversacional embebido del panel de control (chat del
  operador → comandos validados). Ver @assistant/CLAUDE.md. Es un submódulo
  independiente: no toca `shared/` ni el protocolo de red, solo invoca funciones que
  ya existen en `src/`, igual que un botón de la GUI.

## Recordatorios de diseño clave

- Las imágenes se procesan **en memoria** (RAM), nunca se escriben a disco.
- Un lote con 0 imágenes significa que la captura falló del lado de la Orange
  Pi **o que es el sondeo de reconexión tras perder la conexión con la PC**
  (sección 4.6 del informe) — responde `ERROR_REVISION_MANUAL` directamente, sin
  intentar OCR. El panel lo muestra como "Lote vacío (captura fallida o
  reintento tras perder la conexión)".
- **Logging, no `print()`:** cada módulo usa su logger (`server`, `decision`,
  `panel`, `mdns`, `alarma`); `src/registro.py::configurar_logging()` lo
  configura una sola vez en `server.py`. Nivel con `PC_LOG_LEVEL`
  (`DEBUG|INFO|WARNING|ERROR`, por defecto `INFO`). La excepción es el
  placeholder de `src/ocr_interface.py`, que es del equipo de IA.
- **Asistente en el panel (opcional, desactivado por defecto):** lateral derecho de la
  pestaña Inicio.
  Se activa al arrancar `src/server.py` con `ASISTENTE_MODO=prueba` (sin modelo, reglas
  fijas) o `ASISTENTE_MODELO=/ruta/modelo.gguf`. El panel funciona igual sin él. No decide
  el destino de ninguna alarma (D-07). Ver `assistant/CLAUDE.md`.
- Severidad de las alarmas en el panel: **roja** = la Orange Pi la espera,
  **amarilla** = sigue activa pero ya nadie la espera (queda por cerrar),
  **gris** = resuelta.
- El panel de control / alarma (sección 8 del informe) es una página web
  generada por la PC con solo la biblioteca estándar (`src/panel_web.py` +
  `src/panel_static/`); por defecto escucha en `127.0.0.1:8080` (`PANEL_HOST`,
  `PANEL_PUERTO`). Si crece, se puede migrar a Flask: la lógica vive en
  `estado_panel.py`, independiente del servidor web.
- **Orden de inicio del ciclo (S-06, opción 1, ya implementada):** al arrancar, la
  Orange Pi consulta `orden_ciclo` (mismo puerto 5001 y framing que la decisión) hasta
  recibir `iniciar`; el regente la da con el botón "Iniciar recorrido" del panel
  (`POST /api/ciclo/iniciar`). La orden es de **un solo uso** (si la Orange Pi vuelve a
  preguntar tras recibirla, reinició: responde `esperando`) y **vence** a los
  `VIGENCIA_ORDEN_CICLO_S`. Estado en `estado_panel.py`; ver sección 4.7 de
  @../docs/arquitectura_comunicacion.md. Detener el ciclo y la cantidad de cajas (S-06 opción 2, sección 4.8): antes de cada
  objeto la Orange Pi consulta `estado_ciclo` y la PC responde `continuar`/`detener`;
  la PC cuenta las cajas (casilla "Cajas a procesar" + botón "Detener" del panel,
  `POST /api/ciclo/detener`). Si una PC reiniciada no sabe de ningún ciclo, responde
  `detener`. Pausar/reanudar **no existe**. El panel muestra además si la Orange Pi
  está conectada (último mensaje recibido, `VENTANA_CONEXION_ORANGEPI_S`).
- **Decisión del regente (opción A, ya implementada):** tras un
  `ERROR_REVISION_MANUAL` la PC deja UNA decisión en espera
  (`estado_panel.py`); el regente la resuelve en la pestaña Alertas (destino
  `TIPO_X` o `DESCARTE`) y la Orange Pi la consulta por
  `src/servidor_decision.py` (puerto `TCP_PUERTO_DECISION_DEFECTO`). Ver
  sección 4.5 de @../docs/arquitectura_comunicacion.md. Cliente de referencia
  de una consulta: `consultar_decision()` en `tests/mock_orangepi_client.py`.
- **Pendiente del equipo:** que el lado Orange Pi consulte `estado_ciclo` (S-11) y los campos
  OCR/FEFO que debe entregar el reconocimiento.
- `src/ocr_interface.py` es un placeholder que clasifica **al azar** entre `TIPOS_PLACEHOLDER`
  (`TIPO_A/B/C`) hasta que el equipo de IA entregue el módulo real.
- **Registro de eventos (Parte A del plan de inventario/usuarios; revierte D-05 solo para el log):**
  `src/auditoria.py` guarda en disco (`pc/data/smartpharma.db`, ignorado por git; `PC_DB` lo cambia)
  cada lote recibido, alarma, decisión del regente, orden de iniciar/detener y arranque/apagado,
  con fecha, hora y **responsable**. Es de solo escritura (triggers contra UPDATE/DELETE) y cada
  evento lleva el hash del anterior; `verificar()` detecta ediciones o borrados a mano, pero no el
  recorte del final del archivo (por eso el panel muestra el último sello). Lo escribe
  `EstadoPanel` (`_evento`, dentro del candado, para que el orden sea el de las transiciones); un
  fallo al registrar **nunca** rompe el flujo físico (se loguea y sigue). Las acciones humanas del
  panel pasan un `responsable`: la persona con turno abierto (`RESPONSABLE_SIN_SESION` solo existe para
  las pruebas, que construyen el panel sin gestor de usuarios). Los ids de lote y alarma reinician con cada arranque: cada evento lleva
  `arranque` (id del evento `sistema_iniciado`). El servidor **no arranca** si no puede abrir el
  registro. Panel: pestaña REGISTRO (`#registro`), `GET /api/log` y `GET /api/log/verificar`;
  **no hay ninguna ruta para editarlo**. El resto del estado (lotes con fotos, alarmas activas,
  decisión en espera) sigue en RAM.
- **Usuarios, turnos y permisos (Parte B):** `src/usuarios.py` (`GestorUsuarios`), en el MISMO archivo que
  el registro (`PC_DB`). Mirar el panel y el registro es libre; **iniciar/detener el recorrido, resolver
  alarmas y confirmar acciones del asistente exigen un turno abierto** (cookie `sesion`, HttpOnly y
  SameSite=Strict; sin sesión 401, sin permiso 403); administrar usuarios, solo un administrador.
  Auxiliar = **código de 6 dígitos** que genera el sistema (se muestra UNA vez); administrador = nombre y
  clave (≥10 caracteres); todo con PBKDF2 + sal, nunca en claro ni en el registro. **Un solo turno a la
  vez**; vence por inactividad; un administrador puede tomar el control. Un recorrido en marcha sigue a
  nombre de quien lo inició aunque su turno se cierre. Tras 5 intentos fallidos seguidos el inicio de
  sesión se bloquea (60 s, el doble en cada fallo más, máx. 15 min). Los nombres son únicos sin distinguir
  mayúsculas ni tildes y **no se reutilizan** (eliminar es lógico). Cada cambio (crear, regenerar código,
  desactivar, eliminar, turnos, intentos fallidos, acciones denegadas) se confirma en la **misma
  transacción** que su evento (`Auditoria.transaccion()`): no hay cambio sin su evento. Límites: los
  turnos viven en RAM (reiniciar el servidor los cierra sin evento de cierre), y la cadena de hashes
  cubre los EVENTOS, no el contenido de la tabla `usuarios`. `server.py` SIEMPRE conecta el gestor;
  construir el panel sin él (solo pruebas) desactiva la exigencia de sesión.
- **Inventario (Parte C):** `src/inventario.py` (`Inventario`), tabla `cajas` en el MISMO archivo que el
  registro. Cada lote que llega crea una caja (`EstadoPanel.registrar_lote`, en la misma transacción que
  su evento `lote_recibido`, que ahora lleva `caja_id`); cuando el regente resuelve una alarma, su destino
  pasa a ser el de la caja (`alarma_resuelta` también lleva `caja_id`). Dos números que NO se confunden:
  `captura_id` (el "Lote #N" de fotos, que reinicia en cada arranque; va con `arranque`) y `numero_lote`
  (el lote del medicamento). `resultado` (lo que respondió el reconocimiento) y `destino` (el final) NO se
  editan; medicamento, gramaje, lote del medicamento, vencimiento y notas sí, a mano, con turno
  (`caja_editada` guarda el antes y el después). Hasta que se conecte el OCR (Parte D) esas columnas están
  vacías; cuando las llene, `datos_origen` dirá 'ocr' (hoy 'manual' si una persona las tocó). Eliminar es
  lógico y se puede restaurar; la búsqueda ignora mayúsculas y tildes (columna `busqueda`, calculada en
  Python porque `lower()` de SQLite solo entiende ASCII); al ordenar, lo vacío va SIEMPRE al final. API:
  `GET /api/inventario[/agrupado]` (libre) y `POST /api/inventario/<id>/editar|eliminar|restaurar` (exigen
  turno). Si el inventario falla, el flujo físico sigue y el evento queda sin caja. Una caja por lote: el
  lote vacío del sondeo de reconexión (informe 4.6, caso F2) puede duplicar una caja ya registrada; se
  elimina a mano.
- Las fotos del panel salen de RAM (últimos `MAX_LOTES_CON_FOTOS` lotes), con
  `Cache-Control: no-store`; nunca se escriben a disco.
