"""
Telemetría de la ESP32-S3 y de la Orange Pi (PC-24, informe 4.9 y 5.6). Solo observación, de
MEJOR ESFUERZO: nada de aquí toca el ciclo de auditoría ni el registro de eventos.

La Orange Pi envía cada INTERVALO_ENVIO_TELEMETRIA_PC_S, por el puerto de consultas, su propia
telemetría y la última que recibió de la ESP32-S3. Aquí se valida, se guarda en RAM durante unos
minutos (para los gráficos de la pestaña TELEMETRÍA) y se entrega al panel. **Nunca va al registro
de eventos**: es de solo escritura y encadenado, y un dato cada 5 s lo inflaría sin control.
"""
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CLAVE_TELEMETRIA_ESP_EDAD_S_JSON,
    CLAVE_TELEMETRIA_ESP_JSON,
    CLAVE_TELEMETRIA_OPI_JSON,
    INTERVALO_ENVIO_TELEMETRIA_PC_S,
    TELEM_ALARMA,
    TELEM_CORRIENTE_MA,
    TELEM_ENCODERS,
    TELEM_ESTADO,
    TELEM_GIRO_DPS,
    TELEM_HEAP_LIBRE,
    TELEM_NUM_ENCODERS,
    TELEM_NUM_PWM,
    TELEM_NUM_SENSORES_CORRIENTE,
    TELEM_OPI_CAMARA,
    TELEM_OPI_CICLOS,
    TELEM_OPI_CPU_PCT,
    TELEM_OPI_CPU_TEMP_C,
    TELEM_OPI_DISCO_PCT,
    TELEM_OPI_FASE,
    TELEM_OPI_LOTES_VACIOS,
    TELEM_OPI_RAM_PCT,
    TELEM_OPI_RTT_PC_MS,
    TELEM_OPI_T_S,
    TELEM_OPI_UART,
    TELEM_OPI_ULTIMO_CICLO_S,
    TELEM_OPI_WIFI_DBM,
    TELEM_PWM,
    TELEM_T_MS,
    TELEM_TEMP_C,
    TELEM_UART_ERR,
    TELEM_UART_OK,
    FASES_OPI,
)

# Cuánto historial se conserva en RAM (para los gráficos). ~10 min a un mensaje cada 5 s = 120 muestras.
VENTANA_HISTORIAL_S = 600.0
MAX_MUESTRAS = 400                      # tope duro por si la Orange Pi enviara mucho más a menudo
# Sin ningún mensaje durante tres envíos seguidos, el panel marca los datos como viejos.
MUESTRAS_PERDIDAS_PARA_VIEJO = 3
# La última lectura de la ESP32-S3 que trae la Orange Pi también puede estar vieja (la ESP32-S3 dejó de
# responder y la Orange Pi repite la última): más de esto sin renovarse se marca como vieja.
EDAD_MAX_ESP_S = 10.0

CAMPOS_OPI_NUMERICOS = (TELEM_OPI_T_S, TELEM_OPI_CPU_TEMP_C, TELEM_OPI_CPU_PCT, TELEM_OPI_RAM_PCT,
                        TELEM_OPI_DISCO_PCT, TELEM_OPI_WIFI_DBM, TELEM_OPI_RTT_PC_MS, TELEM_OPI_CICLOS,
                        TELEM_OPI_LOTES_VACIOS, TELEM_OPI_ULTIMO_CICLO_S)
CAMPOS_OPI_TEXTO = (TELEM_OPI_FASE, TELEM_OPI_CAMARA, TELEM_OPI_UART)
CAMPOS_ESP_NUMERICOS = (TELEM_T_MS, TELEM_UART_OK, TELEM_UART_ERR, TELEM_HEAP_LIBRE, TELEM_TEMP_C)

# Series que se grafican: nombre -> (de dónde sale, cómo se lee)
SERIES_OPI = (TELEM_OPI_CPU_PCT, TELEM_OPI_CPU_TEMP_C, TELEM_OPI_RAM_PCT, TELEM_OPI_RTT_PC_MS, TELEM_OPI_WIFI_DBM)


def _numero(x):
    """Un número finito, o None. Un sensor que falló llega como null y no es un error del mensaje."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return x if math.isfinite(x) else None


def _lista(x, largo):
    """Una lista de `largo` números (o None en las posiciones que fallaron), o None si no tiene esa forma."""
    if not isinstance(x, list) or len(x) != largo:
        return None
    return [_numero(e) for e in x]


def _limpiar_opi(d: dict) -> dict:
    limpio = {k: _numero(d.get(k)) for k in CAMPOS_OPI_NUMERICOS}
    for k in CAMPOS_OPI_TEXTO:
        valor = d.get(k)
        limpio[k] = valor[:32] if isinstance(valor, str) else None
    if limpio[TELEM_OPI_FASE] not in FASES_OPI:
        limpio[TELEM_OPI_FASE] = None       # una fase que no es del contrato no se muestra como si lo fuera
    return limpio


def _limpiar_esp(d: dict) -> dict:
    limpio = {k: _numero(d.get(k)) for k in CAMPOS_ESP_NUMERICOS}
    estado = d.get(TELEM_ESTADO)
    limpio[TELEM_ESTADO] = estado[:16] if isinstance(estado, str) else None
    alarma = d.get(TELEM_ALARMA)
    limpio[TELEM_ALARMA] = alarma if isinstance(alarma, bool) else None
    limpio[TELEM_GIRO_DPS] = _lista(d.get(TELEM_GIRO_DPS), 3)
    limpio[TELEM_CORRIENTE_MA] = _lista(d.get(TELEM_CORRIENTE_MA), TELEM_NUM_SENSORES_CORRIENTE)
    limpio[TELEM_ENCODERS] = _lista(d.get(TELEM_ENCODERS), TELEM_NUM_ENCODERS)
    limpio[TELEM_PWM] = _lista(d.get(TELEM_PWM), TELEM_NUM_PWM)
    return limpio


class HistorialTelemetria:
    """Últimos minutos de telemetría, en RAM. Seguro entre hilos (lo escribe el servidor de consultas y
    lo lee el panel)."""

    def __init__(self, ventana_s: float = VENTANA_HISTORIAL_S, reloj=time.time):
        self._reloj = reloj
        self._ventana_s = ventana_s
        self._lock = threading.Lock()
        self._muestras = deque(maxlen=MAX_MUESTRAS)
        self._ip = None
        self._recibidos = 0

    def registrar(self, ip: str, mensaje: dict) -> bool:
        """Guarda un mensaje `telemetria` de la Orange Pi. False si no cumple el formato del contrato
        (la PC responde `consulta_invalida`): falta `orange_pi`, o `esp32`/`esp32_edad_s` tienen otro tipo."""
        opi = mensaje.get(CLAVE_TELEMETRIA_OPI_JSON)
        esp = mensaje.get(CLAVE_TELEMETRIA_ESP_JSON)
        edad = mensaje.get(CLAVE_TELEMETRIA_ESP_EDAD_S_JSON)
        if not isinstance(opi, dict) or not (esp is None or isinstance(esp, dict)):
            return False
        if edad is not None and _numero(edad) is None:
            return False
        ahora = self._reloj()
        muestra = {"ts": ahora, "opi": _limpiar_opi(opi),
                   "esp": _limpiar_esp(esp) if esp is not None else None,
                   "esp_edad_s": _numero(edad) if esp is not None else None}
        with self._lock:
            self._muestras.append(muestra)
            self._ip = ip
            self._recibidos += 1
            corte = ahora - self._ventana_s
            while self._muestras and self._muestras[0]["ts"] < corte:
                self._muestras.popleft()
        return True

    def snapshot(self) -> dict:
        """Lo que muestra la pestaña: la última lectura de cada fuente, su frescura y las series."""
        with self._lock:
            muestras = list(self._muestras)
            ip, recibidos = self._ip, self._recibidos
        ahora = self._reloj()
        if not muestras:
            return {"ip": ip, "recibidos": recibidos, "hay_datos": False, "edad_s": None, "viejos": False,
                    "intervalo_s": INTERVALO_ENVIO_TELEMETRIA_PC_S, "orange_pi": None, "esp32": None,
                    "esp32_edad_s": None, "esp32_vieja": False, "ventana_s": self._ventana_s, "series": {}}
        ultima = muestras[-1]
        edad = max(0.0, ahora - ultima["ts"])
        esp_edad = None
        if ultima["esp"] is not None and ultima["esp_edad_s"] is not None:
            esp_edad = ultima["esp_edad_s"] + edad      # la edad que traía + el tiempo que lleva aquí
        # la ESP32-S3 sin lectura reciente: en la última muestra no hay datos de ella, o es antigua
        ultima_esp = next((m for m in reversed(muestras) if m["esp"] is not None), None)
        return {
            "ip": ip,
            "recibidos": recibidos,
            "hay_datos": True,
            "edad_s": round(edad, 1),
            "viejos": edad > MUESTRAS_PERDIDAS_PARA_VIEJO * INTERVALO_ENVIO_TELEMETRIA_PC_S,
            "intervalo_s": INTERVALO_ENVIO_TELEMETRIA_PC_S,
            "ventana_s": self._ventana_s,
            "orange_pi": ultima["opi"],
            "esp32": ultima_esp["esp"] if ultima_esp else None,
            "esp32_edad_s": (round(esp_edad, 1) if esp_edad is not None else None),
            "esp32_vieja": ultima_esp is None or ultima["esp"] is None
                           or (esp_edad is not None and esp_edad > EDAD_MAX_ESP_S),
            "series": self._series(muestras),
        }

    @staticmethod
    def _series(muestras: list) -> dict:
        """{nombre: [[ts, valor], ...]}: solo los puntos con valor (un sensor caído deja un hueco)."""
        series = {}

        def agregar(nombre, ts, valor):
            if valor is not None:
                series.setdefault(nombre, []).append([round(ts, 1), valor])

        for m in muestras:
            for k in SERIES_OPI:
                agregar("opi_" + k, m["ts"], m["opi"].get(k))
            e = m["esp"]
            if e is None:
                continue
            agregar("esp_" + TELEM_TEMP_C, m["ts"], e.get(TELEM_TEMP_C))
            agregar("esp_" + TELEM_HEAP_LIBRE, m["ts"], e.get(TELEM_HEAP_LIBRE))
            corriente = e.get(TELEM_CORRIENTE_MA) or []
            for i, c in enumerate(corriente):
                agregar(f"esp_corriente_{i + 1}", m["ts"], c)
        return series
