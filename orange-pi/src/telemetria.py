"""
Telemetría de la Orange Pi (informe 4.9 y 5.6). Solo observación, de MEJOR ESFUERZO: nada de
aquí puede frenar, retrasar ni alterar el ciclo de auditoría ni la recuperación de 4.6.

Dos hilos de fondo (`ServicioTelemetria`):
  - ESP32-S3: cada INTERVALO_SOLICITUD_TELEMETRIA_ESP_S pide la telemetría por UART y espera
    TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S; sin respuesta anota `uart: "sin_respuesta"` (sin
    reintento ni alarma). Cada vez deja una instantánea en RAM (/dev/shm) para el monitor de
    terminal (`scripts/telemetria.sh`, local y fuera del contrato).
  - PC: cada INTERVALO_ENVIO_TELEMETRIA_PC_S envía la propia y la última de la ESP32-S3. Solo usa
    la PC ya descubierta (no lanza descubrimientos ni invalida la caché): si no contesta, se
    descarta en silencio.
"""
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CAMARA_AUSENTE,
    CAMARA_DISPONIBLE,
    CLAVE_TELEMETRIA_ESP_EDAD_S_JSON,
    CLAVE_TELEMETRIA_ESP_JSON,
    CLAVE_TELEMETRIA_OPI_JSON,
    INTERVALO_ENVIO_TELEMETRIA_PC_S,
    INTERVALO_SOLICITUD_TELEMETRIA_ESP_S,
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
    TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S,
    UART_OK,
    UART_SIN_RESPUESTA,
)

# Instantánea para el monitor de terminal: en RAM (tmpfs), nunca en la tarjeta SD.
RUTA_INSTANTANEA = Path(os.environ.get("OPI_TELEMETRIA_ARCHIVO", "/dev/shm/smartpharma_telemetria.json"))


class EstadoOrangePi:
    """Qué está haciendo el ciclo. Lo actualiza `main.py`; lo lee el hilo de telemetría
    (asignaciones simples: no necesitan candado)."""

    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self._t0 = reloj()
        self.fase = "esperando_orden"
        self.ciclos = 0
        self.lotes_vacios = 0
        self.ultimo_ciclo_s = None

    def segundos_desde_arranque(self) -> int:
        return int(self._reloj() - self._t0)


# --- lecturas del sistema (cada una devuelve None si no se puede leer) -----------------------

def leer_cpu_temp_c():
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


class LectorCPU:
    """Uso de CPU (0..100) entre dos llamadas, desde /proc/stat."""

    def __init__(self):
        self._previo = self._leer()

    @staticmethod
    def _leer():
        try:
            campos = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        ocioso = campos[3] + (campos[4] if len(campos) > 4 else 0)
        return sum(campos), ocioso

    def porcentaje(self):
        actual = self._leer()
        previo, self._previo = self._previo, actual
        if actual is None or previo is None or actual[0] == previo[0]:
            return None
        return round(100 * (1 - (actual[1] - previo[1]) / (actual[0] - previo[0])), 1)


def leer_ram_pct():
    try:
        mem = {}
        for linea in Path("/proc/meminfo").read_text().splitlines():
            clave, valor = linea.split(":", 1)
            mem[clave] = int(valor.split()[0])
        return round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)
    except (OSError, ValueError, KeyError):
        return None


def leer_disco_pct():
    try:
        uso = shutil.disk_usage("/")
        return round(100 * uso.used / uso.total, 1)
    except OSError:
        return None


def leer_wifi_dbm():
    """Señal de la primera interfaz inalámbrica, de /proc/net/wireless."""
    try:
        for linea in Path("/proc/net/wireless").read_text().splitlines()[2:]:
            partes = linea.split()
            if len(partes) >= 4:
                return int(float(partes[3]))
    except (OSError, ValueError):
        pass
    return None


def camara_disponible() -> bool:
    from capture.camera import buscar_camara_por_nombre
    try:
        buscar_camara_por_nombre()   # solo comprueba que exista: no la abre ni la enciende
        return True
    except Exception:  # noqa: BLE001
        return False


class ServicioTelemetria:
    def __init__(self, enlace, descubridor, estado: EstadoOrangePi, enviar=None,
                 hay_camara=camara_disponible, ruta_instantanea=RUTA_INSTANTANEA,
                 intervalo_esp=INTERVALO_SOLICITUD_TELEMETRIA_ESP_S,
                 intervalo_pc=INTERVALO_ENVIO_TELEMETRIA_PC_S,
                 plazo_esp=TIMEOUT_RESPUESTA_TELEMETRIA_ESP_S):
        if enviar is None:
            from network.decision_client import enviar_telemetria as enviar
        self._enlace, self._descubridor, self._estado = enlace, descubridor, estado
        self._enviar, self._hay_camara, self._ruta = enviar, hay_camara, ruta_instantanea
        self._intervalo_esp, self._intervalo_pc, self._plazo_esp = intervalo_esp, intervalo_pc, plazo_esp
        self._cpu = LectorCPU()
        self._cpu_t, self._cpu_pct = 0.0, None   # dos hilos la leen: se recalcula como mucho cada 1 s
        self._parar = threading.Event()
        self._hilos = []
        self._uart_ok = None        # None: aún sin petición; True/False: la última tuvo/no tuvo respuesta
        self._rtt_pc_ms = None
        self._camara = None

    # --- ciclo de vida -------------------------------------------------------------------
    def iniciar(self):
        for nombre, objetivo in (("telemetria-esp", self._bucle_esp), ("telemetria-pc", self._bucle_pc)):
            hilo = threading.Thread(target=objetivo, name=nombre, daemon=True)
            hilo.start()
            self._hilos.append(hilo)

    def detener(self):
        self._parar.set()
        for hilo in self._hilos:
            hilo.join(timeout=2.0)
        try:
            self._ruta.unlink()
        except OSError:
            pass

    # --- datos ---------------------------------------------------------------------------
    def telemetria_orange_pi(self) -> dict:
        e = self._estado
        ahora = time.monotonic()
        if ahora - self._cpu_t >= 1.0:
            self._cpu_pct, self._cpu_t = self._cpu.porcentaje(), ahora
        return {
            TELEM_OPI_T_S: e.segundos_desde_arranque(),
            TELEM_OPI_FASE: e.fase,
            TELEM_OPI_CPU_TEMP_C: leer_cpu_temp_c(),
            TELEM_OPI_CPU_PCT: self._cpu_pct,
            TELEM_OPI_RAM_PCT: leer_ram_pct(),
            TELEM_OPI_DISCO_PCT: leer_disco_pct(),
            TELEM_OPI_WIFI_DBM: leer_wifi_dbm(),
            TELEM_OPI_RTT_PC_MS: self._rtt_pc_ms,
            TELEM_OPI_CAMARA: CAMARA_DISPONIBLE if self._camara else CAMARA_AUSENTE,
            TELEM_OPI_UART: UART_SIN_RESPUESTA if self._uart_ok is False else UART_OK,
            TELEM_OPI_CICLOS: e.ciclos,
            TELEM_OPI_LOTES_VACIOS: e.lotes_vacios,
            TELEM_OPI_ULTIMO_CICLO_S: e.ultimo_ciclo_s,
        }

    def mensaje_para_la_pc(self) -> dict:
        """Lo que viaja a la PC: la telemetría propia y la última de la ESP32-S3 (o null)."""
        esp32, edad = self._enlace.telemetria.ultima()
        return {CLAVE_TELEMETRIA_OPI_JSON: self.telemetria_orange_pi(),
                CLAVE_TELEMETRIA_ESP_JSON: esp32,
                CLAVE_TELEMETRIA_ESP_EDAD_S_JSON: edad}

    # --- hilos ---------------------------------------------------------------------------
    def _bucle_esp(self):
        while not self._parar.is_set():
            try:
                antes = self._enlace.telemetria.contador()
                self._enlace.solicitar_telemetria()
                self._uart_ok = self._enlace.telemetria.esperar_nueva(antes, self._plazo_esp)
            except Exception:  # noqa: BLE001 - la telemetría nunca debe tumbar nada
                self._uart_ok = False
            try:
                self._camara = self._hay_camara()
                self._escribir_instantanea()
            except Exception:  # noqa: BLE001
                pass
            self._parar.wait(self._intervalo_esp)

    def _bucle_pc(self):
        while not self._parar.is_set():
            self._parar.wait(self._intervalo_pc)
            if self._parar.is_set():
                return
            ip = self._descubridor.ip   # solo la ya descubierta; sin buscar ni invalidar
            if ip is None:
                self._rtt_pc_ms = None
                continue
            try:
                t0 = time.monotonic()
                mensaje = self.mensaje_para_la_pc()
                ok = self._enviar(ip, mensaje[CLAVE_TELEMETRIA_OPI_JSON], mensaje[CLAVE_TELEMETRIA_ESP_JSON],
                                  mensaje[CLAVE_TELEMETRIA_ESP_EDAD_S_JSON])
                self._rtt_pc_ms = round((time.monotonic() - t0) * 1000) if ok else None
            except Exception:  # noqa: BLE001 - sin reintento, sin alarma: se descarta en silencio
                self._rtt_pc_ms = None

    def _escribir_instantanea(self):
        datos = self.mensaje_para_la_pc()
        datos["t_unix"] = time.time()
        tmp = Path(str(self._ruta) + ".tmp")
        tmp.write_text(json.dumps(datos))
        os.replace(tmp, self._ruta)
