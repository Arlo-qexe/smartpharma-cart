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
