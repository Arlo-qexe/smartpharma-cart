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
- `src/estado_panel.py` — estado en memoria (lotes, fotos, alarmas) compartido entre el servidor y el panel.
- `src/panel_web.py` + `src/panel_static/` — panel de control web (HTML/CSS/JS propios, sin dependencias).
- `tests/test_server.py`, `tests/test_panel.py` — pruebas con pytest (ver el comando en `tests/test_server.py`; en máquinas con ROS usar `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).
- `tests/mock_orangepi_client.py` — cliente falso para probar el servidor de forma aislada.
- `assistant/` — asistente conversacional embebido del panel de control (chat del
  operador → comandos validados). Ver @assistant/CLAUDE.md. Es un submódulo
  independiente: no toca `shared/` ni el protocolo de red, solo invoca funciones que
  ya existen en `src/`, igual que un botón de la GUI.

## Recordatorios de diseño clave

- Las imágenes se procesan **en memoria** (RAM), nunca se escriben a disco.
- Un lote con 0 imágenes significa que la captura falló del lado de la Orange
  Pi — responde `ERROR_REVISION_MANUAL` directamente, sin intentar OCR.
- El panel de control / alarma (sección 8 del informe) es una página web
  generada por la PC con solo la biblioteca estándar (`src/panel_web.py` +
  `src/panel_static/`); por defecto escucha en `127.0.0.1:8080` (`PANEL_HOST`,
  `PANEL_PUERTO`). Si crece, se puede migrar a Flask: la lógica vive en
  `estado_panel.py`, independiente del servidor web.
- **Pendiente del equipo:** cómo se entera la Orange Pi de que el regente
  desactivó una alarma o definió un destino (hoy "Desactivar" solo marca la
  alarma como resuelta en la PC) y el botón "Iniciar recorrido" (deshabilitado:
  no hay canal PC → Orange Pi). Requiere cambio en `shared/`. Propuesta con
  opciones para el equipo: @../docs/propuesta_canal_regente.md.
- Las fotos del panel salen de RAM (últimos `MAX_LOTES_CON_FOTOS` lotes), con
  `Cache-Control: no-store`; nunca se escriben a disco.
