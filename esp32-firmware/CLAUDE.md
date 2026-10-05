# Contexto: firmware ESP32-S3 (control físico en tiempo real)

Este código corre en la ESP32-S3 y es responsable de TODA acción física en
tiempo real: los 3 motores DC con encoder del manipulador esférico paralelo,
el dispensador (servo), el mecanismo de clasificación, y la alarma física
local. No toma decisiones — solo ejecuta lo que ordena la Orange Pi y reporta
eventos de confirmación.

## Antes de escribir código

- Incluye `shared/protocol_constants.h` — nunca repitas a mano un nombre de
  mensaje o un parámetro de tiempo.
- Consulta @../docs/arquitectura_comunicacion.md (sección 5) para el "por
  qué" del formato de mensajes y la suma de verificación.
- **Este archivo `.h` debe mantenerse sincronizado a mano con
  `shared/protocol_constants.py`** — es el único punto del proyecto sin
  fuente única automática. Si cambias algo aquí, cámbialo también allá y
  regístralo en `../docs/CHANGELOG_protocolo.md`.

## Reglas de diseño que debe respetar el firmware

- **Nunca reanudar el ciclo por cuenta propia.** La ESP32-S3 no debe tener
  ningún timeout interno que la haga "seguir adelante" si no recibe una
  orden — eso es responsabilidad exclusiva de la Orange Pi (que tiene el
  límite de 5 reintentos y el timeout total de 30 s). El firmware solo
  espera, ejecuta, y confirma.
- Cada mensaje enviado debe llevar la suma de verificación XOR
  (`calcularChecksumXor` / `empaquetarMensajeUart` en `protocol_constants.h`).
  Cada mensaje recibido debe validarse antes de actuar sobre él — si el
  checksum no coincide, descartar el mensaje sin ejecutar ninguna acción.
- El dispensador (servo) probablemente se controle desde aquí — ver la nota
  "PENDIENTE DE DECISIÓN" en `protocol_constants.h` antes de implementarlo.
  No agregues ese mensaje hasta que esté confirmado y sincronizado con
  `protocol_constants.py`.

## Estructura sugerida

- `src/main.ino` — loop principal: lectura UART, validación, despacho a los manejadores.
- `src/motores.h/.cpp` — control de los 3 motores DC con encoder del manipulador.
- `src/alarma.h/.cpp` — indicador físico local (zumbador/LED).
- `src/clasificador.h/.cpp` — mecanismo de clasificación (inclinación + rampas).
- `src/dispensador.h/.cpp` — servo del dispensador (una vez decidido que vive aquí).

## Librerías recomendadas

- `ArduinoJson` — parseo/serialización de los mensajes JSON.
- Driver de encoder que ya tengas elegido para los motores DC (confirmar con el equipo de Electrónica).
