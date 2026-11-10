"""Identificación de los motores del SPM: mide las cuentas por vuelta, captura las respuestas en la placa y ajusta el modelo de cada motor.

    python herramientas/identificar_motor.py ppr      --puerto COM5 --motor 1 [--aplicar]
    python herramientas/identificar_motor.py capturar --puerto COM5 --motor 1 --salida identificacion
    python herramientas/identificar_motor.py ajustar  --carpeta identificacion --motor 1

Primero con el MOTOR LIBRE (sin el mecanismo), fuente de 12 V encendida y la mano en el interruptor. Pasos y criterios en firmware/README_firmware.md.
Requiere pyserial (pip install pyserial).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spm.identificacion import ajustar_motor, cargar_csv, informe, reescalar                          # noqa: E402
from spm.identificacion_serial import Capturador, ErrorCaptura, ejecutar_plan, medir_ppr  # noqa: E402


def abrir(puerto, baudios=115200):
    import serial                                                                          # pyserial
    s = serial.Serial(puerto, baudios, timeout=0.2)
    s.dtr = s.rts = False                                                                  # no reiniciar la placa al abrir (en algunas igual lo hace)
    import time
    time.sleep(2.0)
    s.reset_input_buffer()
    return s


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):                                                 # la consola de Windows (cp1252) no puede imprimir τ, ₀...
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("ppr", help="mide las cuentas por vuelta del eje de salida girándolo a mano")
    p.add_argument("--puerto", required=True); p.add_argument("--motor", type=int, required=True); p.add_argument("--aplicar", action="store_true", help="enviar {\"cfg\":\"ppr\"} a la placa")
    p = sub.add_parser("capturar", help="rampas y escalones en la placa + ajuste del modelo")
    p.add_argument("--puerto", required=True); p.add_argument("--motor", type=int, required=True); p.add_argument("--salida", default="identificacion")
    p.add_argument("--tope", type=float, help="ciclo de trabajo máximo (def.: el pwm_max del motor)")
    p.add_argument("--ms", type=int, default=500, help="duración de cada escalón")
    p.add_argument("--max-deg", type=float, default=720, help="giro máximo por escalón antes de cortar (con el mecanismo acoplado: unos pocos grados)")
    p.add_argument("--hold", action="store_true", help="los otros dos motores sostienen su posición con el lazo normal (mecanismo acoplado)")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    p = sub.add_parser("ajustar", help="ajusta el modelo desde los CSV ya capturados")
    p.add_argument("--carpeta", required=True); p.add_argument("--motor", type=int, required=True)
    p.add_argument("--ppr", type=float, help="recalcula con estas cuentas por vuelta (si la placa tenía otro ppr al capturar, p. ej. el 1000 de fábrica)")
    a = ap.parse_args(argv)

    if a.orden == "ajustar":
        caps = [c for c in (cargar_csv(f) for f in sorted(Path(a.carpeta).glob("motor%d_*.csv" % a.motor)))]
        if not caps:
            print("No hay motor%d_*.csv en %s" % (a.motor, a.carpeta), file=sys.stderr)
            return 1
        if a.ppr:
            caps = [reescalar(c, a.ppr) for c in caps]
            print("(recalculado con ppr = %.0f)" % a.ppr)
        m = ajustar_motor(caps)
        print(informe(m))
        m.a_json(Path(a.carpeta) / ("modelo_motor%d.json" % a.motor))
        return 0

    cap = Capturador(abrir(a.puerto))
    try:
        if a.orden == "ppr":
            ppr = medir_ppr(cap, a.motor, lambda: input("Gira el eje de SALIDA del motor %d exactamente UNA vuelta a mano y pulsa Enter... " % a.motor))
            print("ppr medido: %.0f (cuentas X4 por vuelta del eje de salida)" % ppr)
            if a.aplicar:
                cap.enviar({"cfg": "ppr", "motor": a.motor, "val": ppr})
                print("Enviado. Para que sobreviva a un reinicio: {\"cmd\":\"save\"}; y ponlo también en «pulsos/vuelta» de la interfaz.")
            return 0
        print("Antes de empezar: motor %d LIBRE (sin el mecanismo), fuente de 12 V encendida y la mano en el interruptor. Cada captura gira el eje hasta %.0f°." % (a.motor, a.max_deg))
        if not a.si and input("Escribe SI para continuar: ").strip().upper() != "SI":
            return 1
        caps, modelo = ejecutar_plan(cap, a.motor, a.salida, a.tope, a.ms, a.max_deg, hold=a.hold)
        print()
        print(informe(modelo))
        modelo.a_json(Path(a.salida) / ("modelo_motor%d.json" % a.motor))
        print("\nCSV y modelo_motor%d.json en %s" % (a.motor, a.salida))
        return 0
    except ErrorCaptura as e:
        print("ERROR:", e, file=sys.stderr)
        try:
            cap.enviar({"cmd": "stop"})
        except Exception:                                                                  # noqa: BLE001
            pass
        return 1
    except KeyboardInterrupt:
        cap.enviar({"cmd": "stop"})
        print("\nInterrumpido: motores libres.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
