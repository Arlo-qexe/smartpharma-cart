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
# Orden de inicio del ciclo de auditoría (Orange Pi -> PC) — consulta S-06,
# opción 1, de shared/checklist.md. Misma conexión corta, mismo puerto
# (TCP_PUERTO_DECISION_DEFECTO) y mismo framing que la decisión del regente.
#
# Al arrancar (con UART y cámara listas) la Orange Pi NO empieza a introducir
# objetos: consulta cada INTERVALO_CONSULTA_DECISION_S hasta recibir "iniciar".
#   Consulta:  {"consulta": "orden_ciclo"}
#   Respuesta: {"orden": "esperando"}   el regente aún no ordenó: seguir consultando
#              {"orden": "iniciar"}     empezar el ciclo y NO volver a consultar
#              {"error": "consulta_invalida"}
#
# La orden es de UN solo uso y la da el regente con el botón "Iniciar recorrido"
# del panel. La PC la entrega una vez; si la Orange Pi vuelve a preguntar después
# de recibirla, la PC entiende que reinició y responde "esperando" (hace falta una
# orden nueva: ningún reinicio arranca el ciclo sin una decisión humana). Una
# orden que nadie recoge vence a los VIGENCIA_ORDEN_CICLO_S.
# ---------------------------------------------------------------------------
CONSULTA_ORDEN_CICLO = "orden_ciclo"
CLAVE_ORDEN_CICLO_JSON = "orden"
ORDEN_CICLO_ESPERANDO = "esperando"
ORDEN_CICLO_INICIAR = "iniciar"
# 60 s = 30 consultas de 2 s: de sobra para que una Orange Pi que espera la recoja, y
# lo bastante corto para que una orden olvidada (la Orange Pi ya estaba en marcha o
# apagada) no arranque el ciclo en un arranque posterior sin que nadie lo decida.
VIGENCIA_ORDEN_CICLO_S = 60.0               # valor inicial; calibrar

# ---------------------------------------------------------------------------
# Continuar o detener el ciclo, ENTRE objetos (Orange Pi -> PC) — opción 2 de S-06
# (shared/checklist.md), informe 4.8. Misma conexión corta, puerto y framing que las
# consultas anteriores.
#
# ANTES DE CADA OBJETO (el primero incluido) la Orange Pi consulta una vez:
#   Consulta:  {"consulta": "estado_ciclo"}
#   Respuesta: {"ciclo": "continuar"}   introducir el objeto (la PC lo cuenta)
#              {"ciclo": "detener"}     NO introducir más: volver a esperar `iniciar`
#                                       (CONSULTA_ORDEN_CICLO) como al arrancar
#              {"error": "consulta_invalida"}
#
# La PC decide cuándo detener: el regente pulsó "Detener", o se alcanzó la cantidad
# de cajas que fijó al iniciar. Detener solo actúa ENTRE objetos (principio
# secuencial): un objeto en vuelo, o con una decisión del regente pendiente, se
# termina primero. Tras `detener` hace falta una orden `iniciar` nueva.
# Si la PC no contesta esta consulta, la Orange Pi CONTINÚA (respuesta del lado Orange
# Pi a S-06, pregunta 4): el envío del lote ya dispara la recuperación de 4.6.
# Una PC que no sabe de ningún ciclo en marcha (p. ej. se reinició) responde
# `detener`: ningún reinicio sigue adelante sin una decisión humana.
# ---------------------------------------------------------------------------
CONSULTA_ESTADO_CICLO = "estado_ciclo"
CLAVE_CICLO_JSON = "ciclo"
CICLO_CONTINUAR = "continuar"
CICLO_DETENER = "detener"

# ---------------------------------------------------------------------------
# Telemetría (ESP32-S3 -> Orange Pi -> PC) — informe 4.9 y 5.6. NO es control: es solo
# observación, de MEJOR ESFUERZO, y nunca puede frenar ni alterar el ciclo de auditoría.
#
# Tramo 1, UART (la Orange Pi PIDE, la ESP32-S3 RESPONDE; la ESP32-S3 nunca emite sola):
#   Orange Pi -> ESP32-S3: {"accion": "solicitar_telemetria"}
#   ESP32-S3 -> Orange Pi: {"evento": "telemetria", <campos TELEM_*>}
# Un evento "telemetria" puede llegar en CUALQUIER momento (también mientras la Orange Pi
# espera `objeto_en_posicion` o `en_posicion`): el lector UART debe despacharlo por nombre
# de evento, sin confundirlo con una confirmación. Si la ESP32-S3 no responde en
# TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S, la Orange Pi lo anota como "sin datos": sin reintento
# y sin alarma. La ESP32-S3 ignora la petición solo si está ocupada con algo crítico.
#
# Tramo 2, TCP (Orange Pi -> PC; conexión corta al puerto TCP_PUERTO_DECISION_DEFECTO, framing
# de 4.2, UNA vez cada INTERVALO_ENVIO_TELEMETRIA_PC_S):
#   Mensaje:   {"consulta": "telemetria", "orange_pi": {<TELEM_OPI_*>},
#               "esp32": {<TELEM_*>} | null, "esp32_edad_s": <segundos> | null}
#   Respuesta: {"ok": true}   |   {"error": "consulta_invalida"}
# `esp32` es la ÚLTIMA telemetría recibida (sin el campo "evento") y `esp32_edad_s` cuántos
# segundos tiene; ambos null si todavía no hubo ninguna. Si la PC no contesta, la Orange Pi
# descarta el envío en silencio: sin reintento, sin alarma, sin tocar la recuperación de 4.6.
# ---------------------------------------------------------------------------
INTERVALO_SOLICITUD_TELEMETRIA_ESP_S = 2.0     # cada cuánto la Orange Pi pide la telemetría a la ESP32-S3; valor inicial
TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S = 0.5       # la ESP32-S3 debe responder en menos de esto; valor inicial
INTERVALO_ENVIO_TELEMETRIA_PC_S = 5.0          # cada cuánto la Orange Pi envía su telemetría a la PC; valor inicial

CONSULTA_TELEMETRIA = "telemetria"             # valor de "consulta" (puerto 5001)
CLAVE_TELEMETRIA_OPI_JSON = "orange_pi"
CLAVE_TELEMETRIA_ESP_JSON = "esp32"
CLAVE_TELEMETRIA_ESP_EDAD_S_JSON = "esp32_edad_s"
CLAVE_OK_JSON = "ok"

# Campos de la telemetría de la ESP32-S3 (los mismos en el UART y dentro de "esp32" del mensaje TCP).
# Un valor numérico de un sensor puede ser null si ese sensor falló (p. ej. error de I2C).
TELEM_T_MS = "t_ms"                  # int: milisegundos desde el arranque de la ESP32-S3
TELEM_ESTADO = "estado"              # "reposo" | "moviendo"
TELEM_ALARMA = "alarma"              # bool: indicador físico local encendido
TELEM_GIRO_DPS = "giro_dps"          # [x, y, z] velocidad angular del giroscopio (I2C), grados/s
TELEM_CORRIENTE_MA = "corriente_ma"  # [c1, c2] los 2 sensores de corriente (ADC I2C), miliamperios
TELEM_ENCODERS = "enc"               # [e1..e5] cuenta acumulada con signo de cada encoder de cuadratura (A, B)
TELEM_PWM = "pwm"                    # [p1..p11] ciclo de trabajo de cada salida PWM, 0..TELEM_PWM_MAXIMO
TELEM_UART_OK = "uart_ok"            # int: mensajes UART válidos recibidos desde el arranque
TELEM_UART_ERR = "uart_err"          # int: mensajes UART descartados por checksum/formato desde el arranque
TELEM_HEAP_LIBRE = "heap"            # int: bytes de memoria libre
TELEM_TEMP_C = "temp_c"              # temperatura interna del chip, °C (null si no se puede leer)
ESTADO_ESP_REPOSO = "reposo"
ESTADO_ESP_MOVIENDO = "moviendo"
TELEM_NUM_SENSORES_CORRIENTE = 2
TELEM_NUM_ENCODERS = 5
TELEM_NUM_PWM = 11
TELEM_PWM_MAXIMO = 1000              # 1000 = 100,0 % de ciclo de trabajo (milésimas)

# Campos de la telemetría de la Orange Pi (dentro de "orange_pi" del mensaje TCP).
TELEM_OPI_T_S = "t_s"                    # int: segundos desde el arranque de la Orange Pi
TELEM_OPI_FASE = "fase"                  # una de FASES_OPI
TELEM_OPI_CPU_TEMP_C = "cpu_temp_c"      # temperatura de la CPU, °C
TELEM_OPI_CPU_PCT = "cpu_pct"            # uso de CPU, 0..100
TELEM_OPI_RAM_PCT = "ram_pct"            # memoria usada, 0..100
TELEM_OPI_DISCO_PCT = "disco_pct"        # disco usado, 0..100
TELEM_OPI_WIFI_DBM = "wifi_dbm"          # señal Wi-Fi, dBm (null si no se puede leer)
TELEM_OPI_RTT_PC_MS = "rtt_pc_ms"        # latencia a la PC, ms (null si no hubo respuesta reciente)
TELEM_OPI_CAMARA = "camara"              # "disponible" | "ausente"
TELEM_OPI_UART = "uart"                  # "ok" | "sin_respuesta" (la ESP32-S3 no respondió la última petición)
TELEM_OPI_CICLOS = "ciclos"              # int: objetos procesados desde el arranque
TELEM_OPI_LOTES_VACIOS = "lotes_vacios"  # int: lotes vacíos enviados desde el arranque
TELEM_OPI_ULTIMO_CICLO_S = "ultimo_ciclo_s"  # segundos del último ciclo completo (null si aún no hubo)
FASES_OPI = ("esperando_orden", "introduciendo", "capturando", "enviando",
             "esperando_regente", "recuperando_pc", "detenido")
CAMARA_DISPONIBLE = "disponible"
CAMARA_AUSENTE = "ausente"
UART_OK = "ok"
UART_SIN_RESPUESTA = "sin_respuesta"

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
ACCION_DESACTIVAR_ALARMA_LOCAL = "desactivar_alarma_local"   # apaga el indicador; ver informe 4.6
ACCION_CLASIFICAR = "clasificar"                   # campo adicional: "destino": "<TIPO_X>"
ACCION_SOLICITAR_TELEMETRIA = "solicitar_telemetria"  # la ESP32-S3 responde EVENTO_TELEMETRIA (ver arriba)

# ESP32-S3 -> Orange Pi
EVENTO_OBJETO_EN_POSICION = "objeto_en_posicion"   # llegada inicial (una vez por objeto)
EVENTO_EN_POSICION = "en_posicion"                 # confirma cada giro, campo: "cara": N
EVENTO_TELEMETRIA = "telemetria"                   # respuesta a ACCION_SOLICITAR_TELEMETRIA; campos TELEM_*

# Dispensador (servo): DECIDIDO — lo controla la ESP32-S3 (informe 5.5). La orden
# `activar_dispensador` reemplaza a `introducir_objeto` y se confirma igual, con
# `objeto_en_posicion`. Ver docs/CHANGELOG_protocolo.md (2026-10-06).

# ---------------------------------------------------------------------------
# Recuperación tras un fallo de comunicación con la PC (informe 4.6)
# ---------------------------------------------------------------------------
# Mientras la alarma local esté encendida, la Orange Pi envía a la PC un LOTE
# VACÍO cada INTERVALO_REINTENTO_PC_S, sin tope, hasta que la PC responda (el
# lote vacío es el sondeo y fuerza el camino del regente: alarma en el panel y
# decisión). Al primer éxito deja de sondear, envía ACCION_DESACTIVAR_ALARMA_LOCAL
# y espera la decisión del regente (4.5). El ciclo NO se reanuda solo.
INTERVALO_REINTENTO_PC_S = 10.0             # valor inicial; calibrar

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
