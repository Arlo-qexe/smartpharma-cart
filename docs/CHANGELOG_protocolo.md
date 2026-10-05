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
