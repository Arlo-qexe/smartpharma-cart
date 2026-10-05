# Contexto: Asistente conversacional del panel de control (PC)

> Propósito de este archivo: fuente única de verdad del módulo de asistente embebido
> del operador. Pégalo al inicio de un chat/hilo nuevo sobre este módulo específico, o
> deja que el asistente lo recupere en tiempo de ejecución (`make_docs_retriever`).
> Los ítems marcados **(TBD)** están sin decidir.

## 1. Rol dentro de SmartPharma Cart

El carro fotografía 5 caras de cada caja de medicamento y envía las imágenes a la PC
(ver `../../docs/arquitectura_comunicacion.md`). Un modelo de visión en la PC extrae la
fecha de vencimiento; código determinista decide la acción FEFO (First-Expired,
First-Out): clasificación normal, alarma de revisión manual, o registro en base de
datos. **Este módulo (`pc/assistant/`) es la capa conversacional que ayuda al operador
a manejar ese programa** desde el panel de control: el operador dice lo que quiere, y
el asistente ejecuta el comando correspondiente o explica cómo hacerlo.

El asistente es una conveniencia, no una dependencia: el panel de control debe
funcionar completamente sin él (botones normales siguen existiendo).

## 2. Por qué vive separado del resto de `pc/`

- No toca el contrato de red (`shared/protocol_constants.py`) ni el servidor TCP
  (`src/server.py`) — solo invoca funciones ya expuestas por ese código, igual que lo
  haría un botón de la GUI. Por eso este módulo **no está sujeto a la regla de oro**
  del CLAUDE.md raíz (cambios de protocolo van primero por `shared/`): aquí no hay
  protocolo de red involucrado, solo comandos locales.
- Se desarrolla y prueba de forma aislada (`demo_cli.py` en terminal) antes de que
  exista la GUI real del panel de control, igual que `orange-pi/` y `pc/` se probaron
  con mocks antes del hardware real.

## 3. Pipeline del asistente

1. Operador escribe una solicitud en el chat del panel de control.
2. El LLM local (offline) devuelve JSON: o una respuesta de texto, o un comando
   registrado con sus argumentos.
3. `assistant.py` valida el JSON contra un schema y ejecuta la función Python
   registrada — el LLM nunca ejecuta nada directamente.
4. Comandos de **solo lectura** (estado, última clasificación, consultas) corren de
   inmediato y el resultado vuelve al modelo para que responda.
5. **Acciones** (iniciar captura, reclasificar, limpiar alarma) exigen que el operador
   confirme con un botón antes de ejecutarse.

## 4. Hardware y entorno

- PC de desarrollo: laptop, 16 GB RAM, GPU NVIDIA RTX con 6 GB VRAM.
- Debe funcionar **completamente offline** (requisito ya validado para el resto del
  proyecto: mDNS local, sin dependencias de nube).
- Lenguaje: Python, consistente con el resto de `pc/`.
- Framework de GUI del panel de control: **(TBD)** — PySide6/PyQt o Tkinter son
  candidatos; el asistente solo necesita que la GUI llame a `handle()` / `confirm()` /
  `cancel()` desde un hilo de trabajo.
- Modelo de visión (OCR de fecha de vencimiento) y su uso de VRAM: **(TBD)**, a cargo
  del equipo de IA — ver `../src/ocr_interface.py` para el contrato de entrada/salida.

## 5. Decisiones clave ya tomadas

- El LLM **solo maneja comandos y ayuda al operador**. NO toca reconocimiento de
  imágenes, extracción de fecha ni la decisión de alarma/FEFO — eso es código
  determinista en `server.py` / `ocr_interface.py`.
- Patrón: **tool calling**. El LLM devuelve JSON estructurado; la app valida y ejecuta
  funciones Python registradas.
- Runtime: `llama-cpp-python` con un **modelo instruct pequeño (~3-4B parámetros,
  cuantizado a 4 bits, GGUF)**. Modelo exacto **(TBD)**: comparar 2-3 candidatos sobre
  el set de comandos real del proyecto.
- La salida está restringida con un JSON schema, siempre es una respuesta válida o un
  comando válido.
- Dos tipos de comando:
  - **Solo lectura** (`estado_sistema`, `ultima_clasificacion`,
    `listar_proximas_a_vencer`): se ejecutan de inmediato, el resultado vuelve al
    modelo.
  - **Acciones** (`iniciar_captura`, `reclasificar_caja`, `limpiar_alarma`): exigen
    confirmación del operador antes de ejecutarse.
- El asistente recibe un breve **resumen del estado de la app** en cada prompt (estado
  del carro, última caja, última fecha, confianza OCR, alarmas activas).
- Las preguntas "¿cómo hago...?" se responden recuperando las 2-3 secciones más
  relevantes de este archivo (keyword match simple ahora; embeddings después si hace
  falta).
- La capa de asistente se construye como **módulo independiente** (`assistant.py`) y
  se prueba en terminal (`demo_cli.py`) antes de que exista la GUI. La GUI solo
  necesitará un chat box que llame a `handle()` / `confirm()` / `cancel()`.

## 6. Convención del registro de comandos

Cada función invocable por el asistente se registra con el decorador `@command` en
`assistant.py`:

- `name`: identificador corto en snake_case
- `description`: una oración en lenguaje llano (esto es lo que lee el modelo)
- `params`: un modelo pydantic plano (solo str/int/float/bool/Literal)
- `needs_confirm`: True para cualquier cosa que cambie estado o mueva el carro
- `examples`: 1-2 frases que diría un operador real (mejoran mucho la precisión de
  modelos pequeños)

Comandos iniciales (stubs en `demo_cli.py`): `estado_sistema`,
`ultima_clasificacion`, `listar_proximas_a_vencer`, `iniciar_captura`,
`reclasificar_caja`, `limpiar_alarma`.

Si el registro crece más allá de ~8 comandos, solo se ofrecen al modelo los más
relevantes por solicitud (`select_commands`).

## 7. Reglas de seguridad

- Tratar la salida del modelo como entrada no confiable: whitelist de comandos,
  validación de argumentos con pydantic, nunca pasar texto del modelo a un shell.
- Acciones destructivas o irreversibles siempre requieren confirmación del operador.
- Registrar (log) cada comando ejecutado (nombre, argumentos, hora, resultado).
- El asistente nunca debe inventar IDs de caja, IDs de alarma ni fechas; si falta algo
  pregunta.
- Mostrar al operador la confianza del OCR — una fecha mal leída es costosa, así que
  los resultados de baja confianza deben marcarse para revisión humana por código
  plano (`ERROR_REVISION_MANUAL`), no por el LLM.

## 8. Integración con el resto de `pc/`

Cuando exista la GUI del panel de control, los comandos stub de `demo_cli.py` se
reemplazan por llamadas reales:

| Comando del asistente | Se conecta con |
|---|---|
| `estado_sistema` | estado interno de `server.py` / conexión mDNS con la Orange Pi |
| `ultima_clasificacion` | última entrada en la base de datos / último resultado de `ocr_interface.py` |
| `listar_proximas_a_vencer` | consulta a la base de datos (FEFO) — **(TBD)** esquema de base de datos |
| `iniciar_captura` / `reclasificar_caja` | comando hacia la Orange Pi vía el mismo canal que ya usa el protocolo (ver `shared/protocol_constants.py`) — **(TBD)** cómo se dispara desde la PC, no solo desde la Orange Pi |
| `limpiar_alarma` | `disparar_alarma_dashboard()` (placeholder mencionado en `../CLAUDE.md`) |

## 9. Archivos

- `assistant.py`: registro de comandos, construcción de schema, wrapper de
  llama-cpp-python, loop de confirmación, recuperador de documentación.
- `demo_cli.py`: arnés de prueba en terminal con comandos stub.
- `ASSISTANT_CONTEXT.md`: este archivo.

## 10. Preguntas abiertas

- ¿Qué framework de GUI? (afecta solo cómo se integra el chat box, no `assistant.py`)
- ¿Cómo se dispara `iniciar_captura`/`reclasificar_caja` desde la PC hacia la Orange
  Pi? El protocolo actual (`docs/arquitectura_comunicacion.md`) describe el flujo
  Orange Pi → PC para el lote de imágenes; falta definir el canal de vuelta (PC →
  Orange Pi) para comandos iniciados por el operador.
- ¿Qué modelo de visión y cuánta VRAM usa? Determina si el LLM del asistente corre en
  GPU o CPU (ver sección 5 de `../../CONTEXTO_PROYECTO.md` para la estrategia
  GPU/RAM general del proyecto).
- Esquema de base de datos y umbrales de alarma (qué cuenta como "por vencer pronto").
- ¿El operador debe poder corregir una fecha mal leída a través del asistente?

## 11. Próximos pasos

1. Instalar `llama-cpp-python` (build CUDA) y descargar 2-3 modelos GGUF instruct
   pequeños candidatos.
2. Correr `demo_cli.py` con cada modelo y anotar precisión/velocidad sobre frases
   realistas de un operador de farmacia.
3. Reemplazar los comandos stub por llamadas al código real de captura/base de datos.
4. Medir VRAM/RAM combinado con el modelo de visión cargado; decidir GPU vs CPU para
   el LLM del asistente.
5. Construir el panel de chat de la GUI (hilo de trabajo, salida en streaming, botones
   de confirmación).
6. Ampliar este archivo a medida que se escriban los flujos y la documentación del
   panel de control; el asistente recuperará de aquí.
