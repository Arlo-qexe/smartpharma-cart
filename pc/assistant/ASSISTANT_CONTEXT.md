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

- PC de desarrollo prevista: laptop, 16 GB RAM, GPU NVIDIA RTX con 6 GB VRAM.
  **Ojo:** la máquina donde se trabaja hoy (Ubuntu 24.04) **no tiene GPU NVIDIA**, así
  que ahí el LLM correría en CPU (el venv trae `llama-cpp-python` sin CUDA). Falta
  decidir en qué máquina correrá la demo (afecta al tamaño de modelo posible).
- Debe funcionar **completamente offline** (requisito ya validado para el resto del
  proyecto: mDNS local, sin dependencias de nube).
- Lenguaje: Python, consistente con el resto de `pc/`.
- Panel de control: **decidido (D-02 en `shared/checklist.md`)**, es una página web
  generada por la PC con la biblioteca estándar (`../src/panel_web.py`). El chat será
  una caja en esa página que llame, por HTTP, a `handle()` / `confirm()` / `cancel()`
  desde un hilo de trabajo en el servidor. **Construido en la Fase 3** (ver sección 11).
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
- El asistente recibe un breve **resumen del estado de la app** en cada prompt
  (`resumen_estado()`: lotes en el historial, último resultado, alarmas activas y si hay
  una decisión del regente en espera). Cuando el reconocimiento entregue fecha de
  vencimiento y confianza OCR (S-05), se agregarán ahí.
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

Comandos reales hoy (`comandos_panel.py`, solo lectura): `estado_sistema`,
`ultima_clasificacion`, `listar_alarmas`. Siguen como stubs en `demo_cli.py`:
`listar_proximas_a_vencer`, `iniciar_captura`, `reclasificar_caja`, `limpiar_alarma`
(ver la tabla de la sección 8 para saber de qué decisión depende cada uno).

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

El estado del panel vive en `../src/estado_panel.py` (`EstadoPanel`: lotes, fotos,
alarmas y la decisión del regente en espera), solo en RAM (D-05: no se persiste).
`comandos_panel.py` registra los comandos de solo lectura sobre una instancia de
ese estado con `registrar_comandos_panel(estado)`, y `resumen_estado(estado)` arma
el resumen que se inyecta en cada prompt.

| Comando del asistente | Estado | Se conecta con |
|---|---|---|
| `estado_sistema` | **Real (Fase 1)** | `EstadoPanel.snapshot()` y `consultar_decision()`: lotes, alarmas, decisión en espera. **No** conoce el estado mecánico del carro. |
| `ultima_clasificacion` | **Real (Fase 1)** | Últimos lotes (hora, origen, nº de imágenes, clasificación). **No** hay fecha de vencimiento ni confianza OCR todavía. |
| `listar_alarmas` | **Real (Fase 1)** | `EstadoPanel.snapshot()["alarmas"]`: motivo, lote, y si está esperando la decisión, es antigua o resuelta. |
| `listar_proximas_a_vencer` | Stub (solo `demo_cli.py`) | Necesita la fecha de vencimiento: depende de **S-05 / PC-11**. No hay base de datos (D-05). |
| `iniciar_captura` / `reclasificar_caja` | Stub (solo `demo_cli.py`) | Necesitan un canal PC → Orange Pi: depende de la consulta abierta **S-06**. Por la regla de oro, el mensaje se definiría primero en `shared/`. |
| `limpiar_alarma` | Stub — **no conectar tal cual** | Resolver una alarma es elegir el destino de la caja: decisión del **regente** (D-07). Como mucho el asistente podría *sugerirla*; el regente confirma con el botón del panel. |

## 9. Archivos

- `assistant.py`: registro de comandos, construcción de schema, wrapper de
  llama-cpp-python (o un LLM inyectado), loop de confirmación, recuperador de
  documentación.
- `comandos_panel.py`: comandos de solo lectura sobre el estado real del panel y
  `resumen_estado()`.
- `servicio.py`: `ServicioAsistente` (turnos, propuestas con id) y
  `crear_servicio_desde_entorno()`; `llm_prueba.py`: LLM de prueba (reglas fijas).
- `demo_cli.py`: arnés de prueba en terminal (comandos reales sobre un estado de
  ejemplo, más stubs).
- `../tests/test_assistant.py`, `../tests/test_asistente_panel.py`: pruebas sin modelo (LLM falso).
- `ASSISTANT_CONTEXT.md`: este archivo.

## 10. Preguntas abiertas

- ~~¿Qué framework de GUI?~~ **Resuelto:** panel web (D-02).
- ¿Cómo se dispara `iniciar_captura`/`reclasificar_caja` desde la PC hacia la Orange
  Pi? Es la consulta abierta **S-06** (`shared/checklist.md`).
- ¿En qué máquina corre la demo y con cuánta VRAM? Determina si el LLM va en GPU o CPU
  (y qué modelo, ver Fase 2). La máquina de desarrollo actual no tiene GPU NVIDIA.
- ¿Qué entrega el reconocimiento (fecha de vencimiento, confianza)? **S-05**; sin eso
  no hay `listar_proximas_a_vencer` ni "próximas a vencer".
- ¿Puede el asistente *sugerir* el destino de una alarma al regente? (Nunca decidirlo.)
- ¿El operador debe poder corregir una fecha mal leída a través del asistente?

## 11. Próximos pasos

Por fases, de lo más barato a lo más caro:

0. **Hecho — motor probado sin modelo.** `Assistant(llm=...)` acepta un modelo
   inyectado; `tests/test_assistant.py` cubre el bucle, la validación y la
   confirmación con un LLM falso.
1. **Hecho — comandos de solo lectura con datos reales** (`comandos_panel.py`).
2. **Elegir modelo.** Descargar 2-3 modelos GGUF instruct pequeños (~3-4B, 4 bits;
   son varios GB y `curl`/`wget` piden confirmación), correr `demo_cli.py` con cada
   uno y anotar precisión y velocidad con frases reales de un operador. Decidir
   GPU vs CPU según la máquina de la demo.
3. **Hecho — chat en el panel** (pestaña "Asistente"). `servicio.py` envuelve a
   `Assistant` para el panel: una conversación y una solicitud a la vez, y las
   propuestas llevan un id (confirmar con un id viejo no ejecuta nada). Endpoints en
   `../src/panel_web.py`: `GET /api/asistente/estado`, `POST /api/asistente/mensaje |
   confirmar | cancelar | reiniciar`. Es **opcional**: sin configurar queda desactivado
   y el panel funciona igual. Se activa al arrancar el servidor con
   `ASISTENTE_MODO=prueba` (sin modelo: `llm_prueba.py`, reglas fijas, **no es un
   LLM**) o `ASISTENTE_MODELO=/ruta/modelo.gguf` (modelo real, `ASISTENTE_GPU_LAYERS`
   para las capas en GPU). *Límites actuales:* sin streaming (la respuesta llega
   completa), una sola conversación compartida, y como aún no hay comandos que pidan
   confirmación, los botones Ejecutar/Cancelar solo se ejercitan en las pruebas.
4. **Acciones** (`iniciar_captura`, etc.): solo cuando existan S-06 y S-05.
5. Ampliar este archivo con los flujos del panel; el asistente recupera de aquí.
