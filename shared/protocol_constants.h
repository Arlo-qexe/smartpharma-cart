#ifndef PROTOCOL_CONSTANTS_H
#define PROTOCOL_CONSTANTS_H

/*
 * Contrato de comunicación — SmartPharma Cart (espejo C++ de protocol_constants.py)
 * ===================================================================================
 * IMPORTANTE: este archivo debe mantenerse sincronizado MANUALMENTE con
 * shared/protocol_constants.py. Es el ÚNICO punto del proyecto sin fuente
 * única automática, porque el firmware de la ESP32-S3 no puede importar un
 * módulo de Python. Cualquier cambio en uno de los dos archivos debe
 * reflejarse en el otro, y registrarse en docs/CHANGELOG_protocolo.md.
 *
 * No agregues ni cambies un mensaje aquí sin hacer el mismo cambio en
 * protocol_constants.py.
 */

#include <Arduino.h>

// ---------------------------------------------------------------------------
// Enlace serie (UART)
// ---------------------------------------------------------------------------
#define UART_BAUDRATE 115200
#define UART_TERMINADOR '\n'
#define UART_SEPARADOR_CHECKSUM '|'

// ---------------------------------------------------------------------------
// Acciones: Orange Pi -> ESP32-S3
// ---------------------------------------------------------------------------
#define ACCION_ACTIVAR_DISPENSADOR    "activar_dispensador"  // acciona el servo; confirma con EVENTO_OBJETO_EN_POSICION
#define ACCION_INTRODUCIR_OBJETO      ACCION_ACTIVAR_DISPENSADOR  // ALIAS en desuso (antes "introducir_objeto")
#define ACCION_GIRAR_POSICION         "girar_posicion"       // campo adicional: "cara"
#define ACCION_ACTIVAR_ALARMA_LOCAL   "activar_alarma_local"
#define ACCION_DESACTIVAR_ALARMA_LOCAL "desactivar_alarma_local"  // apaga el indicador (informe 4.6)
#define ACCION_CLASIFICAR             "clasificar"           // campo adicional: "destino"
#define ACCION_SOLICITAR_TELEMETRIA   "solicitar_telemetria" // responder con EVENTO_TELEMETRIA (informe 5.6)

// Valor especial de "destino" en ACCION_CLASIFICAR: descarte definitivo
// decidido por el regente (informe 8.2). Espejo de DESTINO_DESCARTE en el .py.
// La ESP32-S3 debe mapearlo a su propia posición/contenedor de descarte.
#define DESTINO_DESCARTE              "DESCARTE"

// ---------------------------------------------------------------------------
// Eventos: ESP32-S3 -> Orange Pi
// ---------------------------------------------------------------------------
#define EVENTO_OBJETO_EN_POSICION     "objeto_en_posicion"
#define EVENTO_EN_POSICION            "en_posicion"          // campo adicional: "cara"
#define EVENTO_TELEMETRIA             "telemetria"           // respuesta a ACCION_SOLICITAR_TELEMETRIA

// ---------------------------------------------------------------------------
// Telemetría (informe 5.6): la Orange Pi PIDE y la ESP32-S3 responde; nunca emite sola.
// Debe responder en menos de 500 ms (TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S) y NO debe retrasar
// ni interrumpir un movimiento. Un sensor que falle se manda como null.
// ---------------------------------------------------------------------------
#define TELEM_T_MS           "t_ms"          // ms desde el arranque
#define TELEM_ESTADO         "estado"        // ESTADO_ESP_REPOSO | ESTADO_ESP_MOVIENDO
#define TELEM_ALARMA         "alarma"        // true/false: indicador local encendido
#define TELEM_GIRO_DPS       "giro_dps"      // [x, y, z] giroscopio I2C, grados/s
#define TELEM_CORRIENTE_MA   "corriente_ma"  // [c1, c2] ADC I2C, mA
#define TELEM_ENCODERS       "enc"           // [e1..e5] cuenta acumulada con signo (cuadratura A, B)
#define TELEM_PWM            "pwm"           // [p1..p11] ciclo de trabajo, 0..TELEM_PWM_MAXIMO
#define TELEM_UART_OK        "uart_ok"       // mensajes UART válidos desde el arranque
#define TELEM_UART_ERR       "uart_err"      // mensajes descartados por checksum/formato
#define TELEM_HEAP_LIBRE     "heap"          // bytes libres
#define TELEM_TEMP_C         "temp_c"        // temperatura interna del chip, °C
#define ESTADO_ESP_REPOSO    "reposo"
#define ESTADO_ESP_MOVIENDO  "moviendo"
#define TELEM_NUM_SENSORES_CORRIENTE 2
#define TELEM_NUM_ENCODERS   5
#define TELEM_NUM_PWM        11
#define TELEM_PWM_MAXIMO     1000            // 1000 = 100,0 %

// Dispensador: DECIDIDO, lo controla la ESP32-S3 (ACCION_ACTIVAR_DISPENSADOR arriba).

// ---------------------------------------------------------------------------
// Parámetros de captura
// ---------------------------------------------------------------------------
#define CARAS_POR_OBJETO 5
#define PAUSA_ESTABILIZACION_MECANICA_MS 300
#define TIEMPO_CONVERGENCIA_AUTOENFOQUE_MS 500

// ---------------------------------------------------------------------------
// Suma de verificación XOR de 8 bits — debe dar el mismo resultado byte a
// byte que calcular_checksum_xor() en protocol_constants.py
// ---------------------------------------------------------------------------
inline uint8_t calcularChecksumXor(const String &payload) {
  uint8_t checksum = 0;
  for (size_t i = 0; i < payload.length(); i++) {
    checksum ^= (uint8_t)payload[i];
  }
  return checksum;
}

// Arma la línea completa "<json>|<checksum_hex>\n" lista para enviar por Serial
inline String empaquetarMensajeUart(const String &payloadJson) {
  uint8_t checksum = calcularChecksumXor(payloadJson);
  char hexBuf[3];
  snprintf(hexBuf, sizeof(hexBuf), "%02X", checksum);
  String linea = payloadJson + UART_SEPARADOR_CHECKSUM + String(hexBuf) + UART_TERMINADOR;
  return linea;
}

#endif // PROTOCOL_CONSTANTS_H
