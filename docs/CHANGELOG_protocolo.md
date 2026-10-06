# Changelog del protocolo de comunicación

Todo cambio a `shared/protocol_constants.py`, `shared/protocol_constants.h` o
`shared/schemas/messages.schema.json` debe registrarse aquí — con fecha,
autor y motivo — **antes de hacer push**, y avisarse al resto del equipo.

Formato de cada entrada:

```
## AAAA-MM-DD — Autor
- Qué cambió y por qué.
- Archivos afectados.
```

---

## 2026-10-05 (2) — Arlo.exe (con Claude Code, lado PC)
- **Nuevo mensaje Orange Pi → PC: consulta de la decisión del regente**
  (opción A de `docs/propuesta_canal_regente.md`, aprobada por el lado
  Orange Pi). Hasta ahora no había forma de que la Orange Pi se enterara de lo
  que decide el regente tras un `ERROR_REVISION_MANUAL` (informe 8.2, paso 4).
- Nuevo flujo: tras `ERROR_REVISION_MANUAL`, la Orange Pi **no ordena
  `clasificar`**; abre una conexión TCP corta al puerto
  `TCP_PUERTO_DECISION_DEFECTO` (5001) cada `INTERVALO_CONSULTA_DECISION_S`
  (2 s), con framing de 4.2: consulta `{"consulta": "decision_regente"}`;
  respuestas `{"estado": "pendiente"}`, `{"estado": "resuelta", "destino": X}`
  (X = `TIPO_X` o `DESCARTE`) o `{"estado": "ninguna"}`. Detalle en
  `arquitectura_comunicacion.md`, sección 4.5.
- Constantes nuevas en `shared/protocol_constants.py`: `TCP_PUERTO_DECISION_DEFECTO`,
  `CLAVE_CONSULTA_JSON`, `CONSULTA_DECISION_REGENTE`, `CLAVE_ESTADO_DECISION_JSON`,
  `ESTADO_DECISION_PENDIENTE/RESUELTA/NINGUNA`, `CLAVE_DESTINO_JSON`,
  `CLAVE_ERROR_JSON`, `ERROR_CONSULTA_INVALIDA`, `DESTINO_DESCARTE`,
  `INTERVALO_CONSULTA_DECISION_S`, `TAMANO_MAX_MENSAJE_JSON_BYTES`.
  - Intervalo de 2 s: el regente tarda minutos; más rapidez solo gasta
    conexiones TCP. Valor inicial, calibrar.
  - Sin IDs de correlación: la PC guarda una sola decisión en espera, no la
    borra al leerla (una respuesta perdida se repite) y la reemplaza con el
    siguiente lote.
- **`DESTINO_DESCARTE` = `"DESCARTE"`** también es un valor válido de `destino`
  en `ACCION_CLASIFICAR`. **Impacto para esp32-firmware:** debe mapear
  `DESCARTE` a su contenedor/posición de descarte. Se agregó
  `DESTINO_DESCARTE` a `protocol_constants.h` (el resto de las constantes
  nuevas solo las usan Orange Pi y PC y no van en el `.h`).
- **Impacto para orange-pi:** reemplazar el `input()` provisional de
  `orange-pi/src/main.py` (revisión manual) por el bucle de consulta. Un
  cliente de referencia de UNA consulta está en
  `pc/tests/mock_orangepi_client.py::consultar_decision`. Si responde
  `ninguna` mientras espera, la PC perdió el estado: se recomienda mantener
  la caja y activar la alarma local. El límite de 30 s no aplica a esta espera.
- Esquema: `shared/schemas/messages.schema.json` (`orangepi_a_pc__consulta_decision`,
  `pc_a_orangepi__respuesta_decision`).
- Lado PC: `pc/src/servidor_decision.py` (servidor de consultas),
  `pc/src/estado_panel.py` (decisión en espera) y panel con selector de
  destino (`POST /api/alarmas/<id>/resolver`, reemplaza a `/desactivar`).
- Archivos: `shared/protocol_constants.py`, `shared/protocol_constants.h`,
  `shared/schemas/messages.schema.json`, `docs/arquitectura_comunicacion.md`,
  `docs/propuesta_canal_regente.md`.
- **Sigue pendiente:** el botón "Iniciar recorrido" del panel (necesita su
  propio mensaje PC → Orange Pi) y los campos OCR/FEFO del reconocimiento.

## 2026-10-05 — Arlo.exe (con Claude Code, lado PC)
- **Agregado (solo lado PC, sin cambio de mensajes ni de formato en el cable):**
  validación del framing en `pc/src/server.py`. Constantes nuevas en
  `shared/protocol_constants.py`:
  - `TIMEOUT_INACTIVIDAD_RECEPCION_S = 10.0` — máximo sin recibir bytes del
    cliente (por `recv`). Igual a `TIMEOUT_RESPUESTA_RECONOCIMIENTO_S` y muy
    por debajo de los 30 s de `TIMEOUT_TOTAL_TRANSACCION_S`, para liberar hilos
    de clientes colgados antes de que la Orange Pi reintente. Calibrar.
  - `TAMANO_MAX_IMAGEN_BYTES = 10 MiB` — un JPEG de una cara pesa ~0.2-2 MB;
    margen >5x sin permitir reservas de hasta 4 GB por un campo corrupto.
  - `MAX_IMAGENES_POR_LOTE = CARAS_POR_OBJETO` (5) — un lote válido trae 5
    imágenes, o 0 si falló la captura.
- Un lote que viole los límites se responde `ERROR_REVISION_MANUAL` y dispara
  la alarma del panel (motivo `framing_invalido`).
- **Impacto para orange-pi:** ningún cambio obligatorio; solo no enviar más de
  5 imágenes ni imágenes de más de 10 MiB por lote.
- `protocol_constants.h` no cambia: estas constantes no las usa la ESP32-S3
  (el `.h` tampoco trae los timeouts TCP existentes).
- Archivos: `shared/protocol_constants.py`, `pc/src/server.py`.

## Base inicial (sin fecha de commit todavía)

- **Corrección:** el nombre de servicio mDNS se cambió de
  `_ocr_service._tcp.local.` a `_ocr-service._tcp.local.` (guion, no guion
  bajo). RFC 6335 exige que el nombre de servicio contenga solo letras,
  dígitos y guiones — `python-zeroconf` rechaza el guion bajo con
  `BadTypeInNameException`. Detectado al probar `pc/src/server.py` de punta a
  punta con `pc/tests/mock_orangepi_client.py`. Afecta: `shared/protocol_constants.py`
  (`MDNS_SERVICE_TYPE`). **Pendiente:** el informe `Informe_Arquitectura_Comunicacion.docx`
  y los diagramas de flujo generados antes de este esqueleto todavía usan el
  nombre antiguo con guion bajo — actualizarlos si se quiere consistencia total.
- Definición inicial del protocolo: descubrimiento mDNS, transporte TCP por
  lote con framing de longitud, enlace UART con suma de verificación XOR,
  alarma física local, límite de 5 reintentos para `introducir_objeto`.
- Contrato de reconocimiento definido (`clasificacion` / `ERROR_REVISION_MANUAL`).
- **Pendiente sin resolver:** mensaje del dispensador (servo) — ver sección
  5.5 de `docs/arquitectura_comunicacion.md`. No se ha agregado a
  `protocol_constants.py`/`.h` todavía porque falta decidir si el servo lo
  controla la ESP32-S3 o la Orange Pi.
