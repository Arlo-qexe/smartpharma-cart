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

# Lado PC: tiempo máximo SIN recibir ningún byte del cliente (se aplica a cada
# recv(), no al lote completo). Valor = TIMEOUT_RESPUESTA_RECONOCIMIENTO_S:
# mucho más holgado que una pausa normal en LAN, y bastante menor que
# TIMEOUT_TOTAL_TRANSACCION_S (30 s), así la PC libera el hilo de un cliente
# colgado antes de que la Orange Pi agote su propio límite y reintente.
TIMEOUT_INACTIVIDAD_RECEPCION_S = 10.0         # valor inicial; calibrar con hardware real

# ---------------------------------------------------------------------------
# Límites de validación del framing (lado PC) — protegen contra campos de
# longitud corruptos (el framing admite hasta 4 GB por campo)
# ---------------------------------------------------------------------------
# Un JPEG de una cara de caja (cámara de ~2-8 MP, comprimido) pesa del orden
# de 0.2-2 MB; 10 MB deja >5x de margen sin permitir reservas absurdas.
TAMANO_MAX_IMAGEN_BYTES = 10 * 1024 * 1024
# MAX_IMAGENES_POR_LOTE se define junto a CARAS_POR_OBJETO (más abajo): un lote
# válido tiene exactamente CARAS_POR_OBJETO imágenes, o 0 si falló la captura.

# ---------------------------------------------------------------------------
# Decisión del regente (Orange Pi -> PC) — opción A de
# docs/propuesta_canal_regente.md: la Orange Pi CONSULTA a la PC por una
# conexión TCP corta y nueva por consulta (mismo patrón que el envío de lotes).
#
# Cuándo: tras recibir RESULTADO_ERROR_REVISION_MANUAL, la Orange Pi NO ordena
# `clasificar`: consulta cada INTERVALO_CONSULTA_DECISION_S hasta obtener
# ESTADO_DECISION_RESUELTA, y entonces ordena `clasificar` con ese destino.
#
# Framing (igual que 4.2, en ambos sentidos):
#   Consulta:  [4 bytes: longitud del JSON] + {"consulta": "decision_regente"}
#   Respuesta: [4 bytes: longitud del JSON] + uno de:
#       {"estado": "pendiente"}                       el regente aún no decide
#       {"estado": "resuelta", "destino": "<X>"}      <X> = TIPO_X o DESTINO_DESCARTE
#       {"estado": "ninguna"}                         la PC no tiene decisión en espera
#       {"error": "consulta_invalida"}
#
# Sin IDs de correlación (principio de secuencialidad): la PC guarda UNA sola
# decisión en espera, la del último lote con error. No se borra al leerla (si
# la respuesta se pierde, la Orange Pi puede repetir la consulta); se reemplaza
# cuando llega el siguiente lote.
# "ninguna" mientras la Orange Pi esperaba significa que la PC perdió el estado
# (p. ej. se reinició): se recomienda mantener la caja en posición y activar la
# alarma local.
# ---------------------------------------------------------------------------
TCP_PUERTO_DECISION_DEFECTO = 5001          # misma IP/host que el servicio de lotes (mDNS)
CLAVE_CONSULTA_JSON = "consulta"
CONSULTA_DECISION_REGENTE = "decision_regente"
CLAVE_ESTADO_DECISION_JSON = "estado"
ESTADO_DECISION_PENDIENTE = "pendiente"
ESTADO_DECISION_RESUELTA = "resuelta"
ESTADO_DECISION_NINGUNA = "ninguna"
CLAVE_DESTINO_JSON = "destino"
CLAVE_ERROR_JSON = "error"
ERROR_CONSULTA_INVALIDA = "consulta_invalida"
DESTINO_DESCARTE = "DESCARTE"               # descarte definitivo (informe 8.2); también es un valor válido de ACCION_CLASIFICAR
# Una consulta cada 2 s: el regente tarda minutos, así que no hace falta más
# rapidez, y cada consulta es una conexión TCP completa en una LAN compartida.
INTERVALO_CONSULTA_DECISION_S = 2.0         # valor inicial; calibrar
# Tamaño máximo del JSON de una consulta (la PC rechaza longitudes mayores).
TAMANO_MAX_MENSAJE_JSON_BYTES = 4096

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
ACCION_ACTIVAR_DISPENSADOR = "activar_dispensador" # acciona el servo del dispensador (informe 5.5);
                                                   # se confirma con EVENTO_OBJETO_EN_POSICION
ACCION_INTRODUCIR_OBJETO = ACCION_ACTIVAR_DISPENSADOR  # ALIAS en desuso (antes "introducir_objeto"); usar el nuevo nombre
ACCION_GIRAR_POSICION = "girar_posicion"           # campo adicional: "cara": N
ACCION_ACTIVAR_ALARMA_LOCAL = "activar_alarma_local"
ACCION_CLASIFICAR = "clasificar"                   # campo adicional: "destino": "<TIPO_X>"

# ESP32-S3 -> Orange Pi
EVENTO_OBJETO_EN_POSICION = "objeto_en_posicion"   # llegada inicial (una vez por objeto)
EVENTO_EN_POSICION = "en_posicion"                 # confirma cada giro, campo: "cara": N

# Dispensador (servo): DECIDIDO — lo controla la ESP32-S3 (informe 5.5). La orden
# `activar_dispensador` reemplaza a `introducir_objeto` y se confirma igual, con
# `objeto_en_posicion`. Ver docs/CHANGELOG_protocolo.md (2026-10-06).

# ---------------------------------------------------------------------------
# Límites de reintento
# ---------------------------------------------------------------------------
LIMITE_REINTENTOS_INTRODUCIR_OBJETO = 5   # reintentos de ACCION_ACTIVAR_DISPENSADOR (nombre conservado)

# ---------------------------------------------------------------------------
# Captura de imágenes
# ---------------------------------------------------------------------------
CARAS_POR_OBJETO = 5
MAX_IMAGENES_POR_LOTE = CARAS_POR_OBJETO   # ver "Límites de validación del framing"
PAUSA_ESTABILIZACION_MECANICA_S = 0.3      # valor inicial; calibrar con hardware real
TIEMPO_CONVERGENCIA_AUTOENFOQUE_S = 0.5    # valor inicial; calibrar con hardware real

# ---------------------------------------------------------------------------
# Reconocimiento (contrato con el equipo de IA — ver pc/src/ocr_interface.py)
# ---------------------------------------------------------------------------
UMBRAL_VOTACION_MAYORIA = 0.5   # mayoría simple (>50%)
