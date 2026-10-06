# Contexto: asistente conversacional del operador (`pc/assistant/`)

Este submódulo agrega un chat embebido al panel de control de la PC. El operador
escribe lo que necesita y el asistente ejecuta un comando registrado o explica cómo
hacerlo. Lee primero @ASSISTANT_CONTEXT.md — ahí está el "por qué" de cada decisión.

## Qué sí y qué no toca este módulo

- **No** modifica `shared/protocol_constants.py` ni el protocolo de red entre la
  Orange Pi y la PC. Los comandos que mueve el carro (`iniciar_captura`,
  `reclasificar_caja`) terminan invocando funciones que ya existen en `../src/`, de la
  misma forma en que lo haría un botón de la GUI — este módulo nunca abre sockets ni
  arma mensajes del protocolo directamente.
- **No** implementa reconocimiento de imágenes ni la lógica FEFO. Esas decisiones son
  código determinista en `../src/ocr_interface.py` y `../src/server.py`.
- Por eso la "regla de oro" del CLAUDE.md raíz (cambios de protocolo van primero por
  `shared/` + changelog) no aplica a este submódulo en su mayoría — sí aplica si algún
  día un comando del asistente necesita agregar un mensaje nuevo al protocolo
  Orange Pi ↔ PC (por ejemplo, disparar una captura remota); en ese caso el mensaje se
  define primero en `shared/`.

## Cómo probar

**Sin modelo (lo normal):** `python3 -m pytest tests/test_assistant.py` desde `pc/`
(en máquinas con ROS: `env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m
pytest tests/test_assistant.py`). Usa un LLM falso inyectado con `Assistant(llm=...)`,
así que no necesita descargar nada.

## Probar con un modelo real en terminal

```bash
cd pc/assistant
pip install llama-cpp-python pydantic
# coloca un modelo GGUF pequeño (~3-4B, Q4) en pc/assistant/models/
python3 demo_cli.py
```

`demo_cli.py` trae comandos stub (`estado_sistema`, `ultima_clasificacion`,
`listar_proximas_a_vencer`, `iniciar_captura`, `reclasificar_caja`,
`limpiar_alarma`). Reemplaza sus cuerpos por llamadas reales cuando la GUI y la base
de datos existan — ver la tabla de la sección 8 de `ASSISTANT_CONTEXT.md`.

## Estructura

- `assistant.py` — motor del asistente: registro de comandos, schema JSON, wrapper de
  `llama-cpp-python`, loop de confirmación, recuperador de documentación.
- `comandos_panel.py` — comandos de SOLO LECTURA sobre el estado real del panel
  (`registrar_comandos_panel(estado)`, `resumen_estado(estado)`). Lee
  `../src/estado_panel.py`; no abre sockets ni toca el protocolo.
- `demo_cli.py` — arnés de prueba en terminal (comandos reales sobre un estado de
  ejemplo, más stubs de lo que depende de decisiones abiertas).
- `ASSISTANT_CONTEXT.md` — contexto y decisiones de este submódulo (análogo a este
  CLAUDE.md pero pensado para pegarse al inicio de un chat o para que el propio
  asistente lo recupere en tiempo de ejecución).

## Recordatorios de diseño clave

- Los parámetros de cada comando (`params`) deben ser modelos pydantic **planos**
  (str/int/float/bool/Literal), nunca anidados.
- Toda acción que cambie estado o mueva el carro necesita `needs_confirm=True`.
- El asistente **no decide el destino de una alarma** (eso es del regente, D-07):
  como mucho lo sugiere. Por eso `limpiar_alarma` sigue siendo un stub que no se
  conecta tal cual.
- Los comandos no inventan datos que el sistema aún no tiene (fecha de vencimiento,
  confianza OCR): lo dicen en el texto de la respuesta.
- Nunca pasar texto generado por el modelo a un shell o a `eval`.
- El LLM de este módulo es independiente del modelo de visión que extrae la fecha de
  vencimiento — ver sección 4-5 de `ASSISTANT_CONTEXT.md` para la estrategia de
  reparto de VRAM entre ambos.
