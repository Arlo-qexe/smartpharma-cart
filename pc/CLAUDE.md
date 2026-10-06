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

## El motor de OCR/clasificación es una caja negra aquí

`src/ocr_interface.py` define el **contrato** de entrada/salida con el
módulo de reconocimiento (que desarrolla el equipo de IA), no su
implementación. Mientras ese equipo no entregue su módulo real, usa el
placeholder ya incluido para poder probar todo el transporte de datos de
punta a punta. **No implementes aquí ningún modelo de OCR** — cuando el
equipo de IA entregue su código, se conecta reemplazando el cuerpo de
`procesar_lote_ocr()`, sin tocar nada de `server.py`.

## Cómo probar sin la Orange Pi real

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
- `assistant/comandos_panel.py` — comandos de solo lectura del asistente sobre el estado del panel (ver `assistant/CLAUDE.md`).
- `src/registro.py` — configuración de logging (`PC_LOG_LEVEL`).
- `src/estado_panel.py` — estado en memoria (lotes, fotos, alarmas) compartido entre el servidor y el panel.
- `src/panel_web.py` + `src/panel_static/` — panel de control web (HTML/CSS/JS propios, sin dependencias).
- `tests/test_server.py`, `tests/test_panel.py`, `tests/test_decision.py`, `tests/test_assistant.py` — pruebas con pytest (ver el comando en `tests/test_server.py`; en máquinas con ROS usar `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).
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
- Severidad de las alarmas en el panel: **roja** = la Orange Pi la espera,
  **amarilla** = sigue activa pero ya nadie la espera (queda por cerrar),
  **gris** = resuelta.
- El panel de control / alarma (sección 8 del informe) es una página web
  generada por la PC con solo la biblioteca estándar (`src/panel_web.py` +
  `src/panel_static/`); por defecto escucha en `127.0.0.1:8080` (`PANEL_HOST`,
  `PANEL_PUERTO`). Si crece, se puede migrar a Flask: la lógica vive en
  `estado_panel.py`, independiente del servidor web.
- **Decisión del regente (opción A, ya implementada):** tras un
  `ERROR_REVISION_MANUAL` la PC deja UNA decisión en espera
  (`estado_panel.py`); el regente la resuelve en la pestaña Alertas (destino
  `TIPO_X` o `DESCARTE`) y la Orange Pi la consulta por
  `src/servidor_decision.py` (puerto `TCP_PUERTO_DECISION_DEFECTO`). Ver
  sección 4.5 de @../docs/arquitectura_comunicacion.md. Cliente de referencia
  de una consulta: `consultar_decision()` en `tests/mock_orangepi_client.py`.
- **Pendiente del equipo:** el botón "Iniciar recorrido" del panel
  (deshabilitado: necesita su propio mensaje PC → Orange Pi, cambio en
  `shared/`) y los campos OCR/FEFO que debe entregar el reconocimiento.
- Las fotos del panel salen de RAM (últimos `MAX_LOTES_CON_FOTOS` lotes), con
  `Cache-Control: no-store`; nunca se escriben a disco.
