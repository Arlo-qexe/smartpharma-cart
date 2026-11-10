"""Reproduce el programa «4 caras (5 s) + volcado» con los TRES motores desde el PC por UART.

  python herramientas/reproducir_programa.py --puerto COM5 --ensayo          # ensayo lento y corto (primera vez)
  python herramientas/reproducir_programa.py --puerto COM5                   # programa completo (tope 130 °/s, esperas de 5 s)
  python herramientas/reproducir_programa.py --simulado                      # sin placa (placa simulada)

Antes: motores libres o plato en la pose inicial (nivelado): al empezar se hace `zero all`, es decir, la posición actual es el origen.
Carga las ganancias identificacion/ganancias_motor{1,2,3}.json (--sin-ganancias para no hacerlo). Ctrl+C = parada ({"cmd":"stop"}: los motores quedan libres).
Escribe identificacion/reproduccion_AAAAMMDD_HHMMSS.csv con consigna, posición, u y error de cada motor.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spm.reproduccion import Abortado, Reproductor, guardar_csv, trayectoria_programa  # noqa: E402


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", help="COM5, /dev/ttyUSB0…")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--simulado", action="store_true", help="usa la placa simulada (sin hardware)")
    ap.add_argument("--ensayo", action="store_true", help="atajo: --tope-vel 60 --espera 1 (lento y corto)")
    ap.add_argument("--tope-vel", type=float, default=130.0, help="tope de velocidad por motor [°/s]")
    ap.add_argument("--espera", type=float, default=5.0, help="pausa en cada cara [s]")
    ap.add_argument("--e-max", type=float, default=15.0, help="error de seguimiento que dispara la parada [°]")
    ap.add_argument("--ppr", type=float, default=4532.0, help="ppr que deben tener los tres motores en la placa (0 = no comprobar)")
    ap.add_argument("--motores", default="1,2,3", help="motores que se mueven, p. ej. 1 o 1,3 (los demás se quedan en 0 sujetos por su lazo)")
    ap.add_argument("--sin-ganancias", action="store_true")
    ap.add_argument("--carpeta", type=Path, default=Path(__file__).resolve().parents[1] / "identificacion")
    a = ap.parse_args(argv)
    if a.ensayo:
        a.tope_vel, a.espera = 60.0, 1.0
    t, q = trayectoria_programa(a.tope_vel, a.espera)
    elegidos = {int(x) for x in a.motores.split(",") if x.strip()}
    for j in range(3):
        if (j + 1) not in elegidos:
            q[:, j] = 0.0
    print("Programa: %.1f s, recorrido por motor %s °, pico %s °/s (tope %.0f)" % (
        t[-1], [round(float(x), 0) for x in (q.max(axis=0) - q.min(axis=0))], [round(float(abs((q[1:] - q[:-1]) / (t[1] - t[0])).max(axis=0)[j])) for j in range(3)], a.tope_vel))
    if a.simulado:
        from spm.placa_simulada import PlacaSimulada
        puerto = PlacaSimulada(factor_tiempo=10.0, K=270.0, tau=0.027, u0=0.04, ppr=4532.0, pwm_max=0.7)
        puerto.timeout = 0.004
        rep = Reproductor(puerto, reloj=lambda: time.monotonic() * 10.0, dormir=lambda s: time.sleep(s / 10.0))
    else:
        if not a.puerto:
            ap.error("indica --puerto COMx (o --simulado)")
        import serial
        puerto = serial.Serial(a.puerto, a.baud, timeout=0.01)
        time.sleep(0.5)
        puerto.reset_input_buffer()
        rep = Reproductor(puerto)
    try:
        rep.preparar(None if a.sin_ganancias else [a.carpeta / ("ganancias_motor%d.json" % n) for n in (1, 2, 3)], a.ppr or None)
        print("Empieza en 3 s… (Ctrl+C = parada)")
        time.sleep(3.0 if not a.simulado else 0.1)
        res = rep.reproducir(t, q, e_max=a.e_max, progreso=lambda ts, tf, e: print("  t=%5.1f/%.1f s  error máx. %.2f°" % (ts, tf, e)))
    except Abortado as ex:
        print("NO se pudo empezar:", ex)
        return 1
    ruta = a.carpeta / ("reproduccion_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
    guardar_csv(res, ruta)
    m = res.metricas()
    print("\nResultado: %s" % res.motivo)
    if m:
        print("  error máx. por motor [°]:", [round(x, 2) for x in m["e_max_deg"]], "  rms:", [round(x, 2) for x in m["e_rms_deg"]], "  |u| máx. [‰]:", [round(x) for x in m["u_max_permil"]])
    print("  CSV:", ruta)
    return 0 if res.ok else 2


if __name__ == "__main__":
    sys.exit(main())
