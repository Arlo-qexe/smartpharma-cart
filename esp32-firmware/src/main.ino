/*
 * SmartPharma Cart — Firmware ESP32-S3
 * =====================================
 * Loop principal: lee mensajes UART de la Orange Pi, valida la suma de
 * verificación, despacha a los manejadores correspondientes, y confirma
 * con el evento correspondiente.
 *
 * Ver docs/arquitectura_comunicacion.md (sección 5) y CLAUDE.md de esta
 * carpeta antes de modificar este archivo.
 *
 * Requiere la librería ArduinoJson (Sketch > Include Library > Manage Libraries).
 */
#include <ArduinoJson.h>
#include "../../shared/protocol_constants.h"

// TODO: incluir aquí los módulos reales cuando existan:
// #include "motores.h"
// #include "alarma.h"
// #include "clasificador.h"
// #include "dispensador.h"

String bufferEntrada = "";

void setup() {
  Serial.begin(UART_BAUDRATE);
  // TODO: inicializar motores, encoders, alarma, etc.
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == UART_TERMINADOR) {
      procesarLinea(bufferEntrada);
      bufferEntrada = "";
    } else {
      bufferEntrada += c;
    }
  }
}

void procesarLinea(const String &linea) {
  int idxSeparador = linea.lastIndexOf(UART_SEPARADOR_CHECKSUM);
  if (idxSeparador < 0) {
    return;  // formato inválido, se descarta silenciosamente
  }

  String payload = linea.substring(0, idxSeparador);
  String checksumRecibido = linea.substring(idxSeparador + 1);
  checksumRecibido.trim();

  char checksumEsperado[3];
  snprintf(checksumEsperado, sizeof(checksumEsperado), "%02X", calcularChecksumXor(payload));

  if (!checksumRecibido.equalsIgnoreCase(checksumEsperado)) {
    // Mensaje corrupto (ruido eléctrico) -- se descarta, NUNCA se ejecuta
    // una acción sobre un mensaje con checksum inválido.
    return;
  }

  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, payload);
  if (error) {
    return;
  }

  if (doc.containsKey("accion")) {
    despacharAccion(doc);
  }
  // Los mensajes con "evento" son enviados POR esta ESP32, no recibidos --
  // si llega uno, probablemente sea eco del enlace serie; se ignora.
}

void despacharAccion(JsonDocument &doc) {
  const char *accion = doc["accion"];

  if (strcmp(accion, ACCION_INTRODUCIR_OBJETO) == 0) {
    // TODO: mover el servo/mecanismo de entrada del dispensador.
    // Al confirmar que el objeto llegó a posición:
    enviarEvento(EVENTO_OBJETO_EN_POSICION);

  } else if (strcmp(accion, ACCION_GIRAR_POSICION) == 0) {
    int cara = doc["cara"];
    // TODO: mover los 3 motores DC con encoder del manipulador a la cara `cara`.
    // Al confirmar el giro:
    enviarEventoConCara(EVENTO_EN_POSICION, cara);

  } else if (strcmp(accion, ACCION_ACTIVAR_ALARMA_LOCAL) == 0) {
    // TODO: encender el zumbador/LED físico.

  } else if (strcmp(accion, ACCION_CLASIFICAR) == 0) {
    const char *destino = doc["destino"];
    // TODO: accionar el mecanismo de clasificación hacia `destino`
    // (inclinación del manipulador + rampas internas).
  }

  // PENDIENTE DE DECISIÓN: si se confirma que el dispensador se controla
  // aquí, agregar un `else if` para ACCION_ACTIVAR_DISPENSADOR una vez que
  // esa constante exista en protocol_constants.h (ver nota ahí).
}

void enviarEvento(const char *evento) {
  StaticJsonDocument<100> doc;
  doc["evento"] = evento;
  String payload;
  serializeJson(doc, payload);
  Serial.print(empaquetarMensajeUart(payload));
}

void enviarEventoConCara(const char *evento, int cara) {
  StaticJsonDocument<100> doc;
  doc["evento"] = evento;
  doc["cara"] = cara;
  String payload;
  serializeJson(doc, payload);
  Serial.print(empaquetarMensajeUart(payload));
}
