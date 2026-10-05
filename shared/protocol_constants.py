"""
Contrato de comunicación — SmartPharma Cart
=============================================

Fuente ÚNICA de verdad para nombres de mensajes, timeouts y parámetros del
protocolo. Tanto `orange-pi/` como `pc/` deben IMPORTAR este archivo, nunca
repetir estos valores a mano.

El equivalente en C++ para la ESP32-S3 vive en `shared/protocol_constants.h`
y debe mantenerse sincronizado MANUALMENTE con este archivo — es el único
punto del proyecto sin fuente única automática, porque el firmware no puede
importar un módulo de Python. Cualquier cambio aquí debe reflejarse allá.

Ver docs/arquitectura_comunicacion.md para la justificación de cada decisión,
y docs/CHANGELOG_protocolo.md para el historial de cambios. NO cambies un
valor aquí sin actualizar ambos.
"""

# ---------------------------------------------------------------------------
# Descubrimiento de red (Orange Pi <-> PC)
# ---------------------------------------------------------------------------
# NOTA DE CORRECCIÓN: el nombre de servicio debe usar guion, no guion bajo.
# RFC 6335 exige que el <Service Name> (la parte entre "_" y ".") contenga
# solo letras, dígitos y GUIONES — un guion bajo lo invalida y algunas
# implementaciones de mDNS (como python-zeroconf) lo rechazan directamente.
MDNS_SERVICE_TYPE = "_ocr-service._tcp.local."

# ---------------------------------------------------------------------------
# Transporte de imágenes (Orange Pi <-> PC) — TCP, conexión nueva por lote
# ---------------------------------------------------------------------------
TCP_PUERTO_DEFECTO = 5000

# Framing de datos (ver docs/arquitectura_comunicacion.md, sección 4.2):
#   Envío:    [4 bytes big-endian: cantidad de imágenes]
#             + N x ([4 bytes big-endian: tamaño de imagen] + [bytes JPEG])
#   Respuesta: [4 bytes big-endian: longitud del JSON] + [bytes JSON UTF-8]
FRAMING_STRUCT_FORMAT = "!I"   # struct de Python: unsigned int, big-endian, 4 bytes
FRAMING_LENGTH_BYTES = 4

CLAVE_RESULTADO_JSON = "clasificacion"
RESULTADO_ERROR_REVISION_MANUAL = "ERROR_REVISION_MANUAL"

# ---------------------------------------------------------------------------
# Timeouts y reintentos (Orange Pi <-> PC)
# ---------------------------------------------------------------------------
TIMEOUT_CONEXION_TCP_S = 3.0
TIMEOUT_RESPUESTA_RECONOCIMIENTO_S = 10.0      # valor inicial; calibrar con el servicio real
TIMEOUT_TOTAL_TRANSACCION_S = 30.0             # al agotarse: RESULTADO_ERROR_REVISION_MANUAL

# ---------------------------------------------------------------------------
# Enlace serie (Orange Pi <-> ESP32-S3) — UART con checksum
# ---------------------------------------------------------------------------
UART_BAUDRATE = 115200
UART_TERMINADOR = "\n"
UART_SEPARADOR_CHECKSUM = "|"                  # formato completo: <json>|<checksum_hex>\n


def calcular_checksum_xor(payload: str) -> str:
    """Suma de verificación XOR de 8 bits sobre los bytes UTF-8 del payload.

    Devuelve el resultado como 2 dígitos hexadecimales en mayúscula, para que
    coincida byte a byte con `calcularChecksumXor()` en protocol_constants.h.
    """
    checksum = 0
    for b in payload.encode("utf-8"):
        checksum ^= b
    return f"{checksum:02X}"


def empaquetar_mensaje_uart(payload_dict: dict) -> bytes:
    """Serializa un dict a JSON compacto, le agrega el checksum y el
    terminador, y devuelve los bytes listos para escribir al puerto serie."""
    import json
    payload = json.dumps(payload_dict, ensure_ascii=False)
    checksum = calcular_checksum_xor(payload)
    linea = f"{payload}{UART_SEPARADOR_CHECKSUM}{checksum}{UART_TERMINADOR}"
    return linea.encode("utf-8")


def desempaquetar_mensaje_uart(linea: str):
    """Valida el checksum de una línea recibida por UART y devuelve el dict
    decodificado, o None si el checksum no coincide o el formato es inválido.
    `linea` debe venir ya sin el terminador (p. ej. resultado de readline()
    .decode().strip())."""
    import json
    if UART_SEPARADOR_CHECKSUM not in linea:
        return None
    payload, checksum_recibido = linea.rsplit(UART_SEPARADOR_CHECKSUM, 1)
    if calcular_checksum_xor(payload) != checksum_recibido.strip().upper():
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Mensajes UART definidos (Orange Pi <-> ESP32-S3)
# ---------------------------------------------------------------------------
# Orange Pi -> ESP32-S3
ACCION_INTRODUCIR_OBJETO = "introducir_objeto"
ACCION_GIRAR_POSICION = "girar_posicion"           # campo adicional: "cara": N
ACCION_ACTIVAR_ALARMA_LOCAL = "activar_alarma_local"
ACCION_CLASIFICAR = "clasificar"                   # campo adicional: "destino": "<TIPO_X>"

# ESP32-S3 -> Orange Pi
EVENTO_OBJETO_EN_POSICION = "objeto_en_posicion"   # llegada inicial (una vez por objeto)
EVENTO_EN_POSICION = "en_posicion"                 # confirma cada giro, campo: "cara": N

# --- PENDIENTE DE DECISIÓN (ver docs/arquitectura_comunicacion.md, sección 5.5) ---
# Mecanismo del dispensador (servo): aún no se ha definido si se controla desde
# la ESP32-S3 (recomendado) o desde la Orange Pi. Si se confirma en la ESP32-S3,
# agregar aquí algo como:
#   ACCION_ACTIVAR_DISPENSADOR = "activar_dispensador"
# NO usar este nombre en código hasta que se confirme y se actualice este
# archivo + protocol_constants.h + docs/CHANGELOG_protocolo.md.

# ---------------------------------------------------------------------------
# Límites de reintento
# ---------------------------------------------------------------------------
LIMITE_REINTENTOS_INTRODUCIR_OBJETO = 5

# ---------------------------------------------------------------------------
# Captura de imágenes
# ---------------------------------------------------------------------------
CARAS_POR_OBJETO = 5
PAUSA_ESTABILIZACION_MECANICA_S = 0.3      # valor inicial; calibrar con hardware real
TIEMPO_CONVERGENCIA_AUTOENFOQUE_S = 0.5    # valor inicial; calibrar con hardware real

# ---------------------------------------------------------------------------
# Reconocimiento (contrato con el equipo de IA — ver pc/src/ocr_interface.py)
# ---------------------------------------------------------------------------
UMBRAL_VOTACION_MAYORIA = 0.5   # mayoría simple (>50%)

# prueba notificacion telegram
