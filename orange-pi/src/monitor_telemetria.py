"""
Monitor de terminal de la telemetría (local de la Orange Pi, sin PC; fuera del contrato).
Lee la instantánea que deja `main.py` en RAM (telemetria.RUTA_INSTANTANEA) y la muestra
refrescándose. Se corre en OTRA terminal mientras `main.py` está en marcha:

    bash scripts/telemetria.sh            # refresca cada segundo; Ctrl+C para salir
    bash scripts/telemetria.sh --una-vez  # imprime una vez y sale
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from telemetria import RUTA_INSTANTANEA  # noqa: E402

ANTIGUEDAD_MAXIMA_S = 10.0


def _v(valor, unidad=""):
    return "—" if valor is None else f"{valor}{unidad}"


def formatear(datos: dict, ahora: float) -> str:
    edad = ahora - datos.get("t_unix", 0)
    if edad > ANTIGUEDAD_MAXIMA_S:
        return f"Sin datos recientes (la última instantánea tiene {edad:.0f} s): ¿está corriendo main.py?"
    o = datos.get("orange_pi") or {}
    e = datos.get("esp32")
    lineas = [
        "=== Orange Pi ===",
        f" arranque  : {_v(o.get('t_s'), ' s')}      fase: {_v(o.get('fase'))}",
        f" CPU       : {_v(o.get('cpu_pct'), ' %')}  {_v(o.get('cpu_temp_c'), ' °C')}"
        f"      RAM: {_v(o.get('ram_pct'), ' %')}      disco: {_v(o.get('disco_pct'), ' %')}",
        f" Wi-Fi     : {_v(o.get('wifi_dbm'), ' dBm')}      latencia a la PC: {_v(o.get('rtt_pc_ms'), ' ms')}",
        f" cámara    : {_v(o.get('camara'))}      UART: {_v(o.get('uart'))}",
        f" ciclos    : {_v(o.get('ciclos'))}      lotes vacíos: {_v(o.get('lotes_vacios'))}"
        f"      último ciclo: {_v(o.get('ultimo_ciclo_s'), ' s')}",
        "",
    ]
    if e is None:
        lineas.append("=== ESP32-S3 === sin datos (no ha respondido ninguna petición)")
    else:
        lineas += [
            f"=== ESP32-S3 === (dato de hace {_v(datos.get('esp32_edad_s'), ' s')})",
            f" estado    : {_v(e.get('estado'))}      alarma: {_v(e.get('alarma'))}"
            f"      arranque: {_v(e.get('t_ms'), ' ms')}",
            f" giroscopio: {_v(e.get('giro_dps'), ' °/s')}",
            f" corriente : {_v(e.get('corriente_ma'), ' mA')}",
            f" encoders  : {_v(e.get('enc'))}",
            f" PWM       : {_v(e.get('pwm'))}",
            f" UART      : {_v(e.get('uart_ok'))} válidos / {_v(e.get('uart_err'))} descartados"
            f"      heap: {_v(e.get('heap'), ' B')}      temp: {_v(e.get('temp_c'), ' °C')}",
        ]
    return "\n".join(lineas)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Monitor de telemetría (Orange Pi + ESP32-S3)")
    ap.add_argument("--una-vez", action="store_true", help="imprime una vez y sale")
    ap.add_argument("--archivo", default=str(RUTA_INSTANTANEA), help="instantánea a leer")
    args = ap.parse_args(argv)
    ruta = Path(args.archivo)
    try:
        while True:
            try:
                texto = formatear(json.loads(ruta.read_text()), time.time())
            except (OSError, ValueError):
                texto = "Sin datos: no se encontró la instantánea (¿está corriendo main.py?)"
            print(texto if args.una_vez else "\033[H\033[J" + texto + "\n\nCtrl+C para salir", flush=True)
            if args.una_vez:
                return 0
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
