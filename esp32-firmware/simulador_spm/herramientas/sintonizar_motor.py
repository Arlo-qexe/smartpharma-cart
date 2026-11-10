"""Sintoniza por simulación el lazo de posición de un motor con su modelo identificado y deja las ganancias listas para el firmware.

    python herramientas/sintonizar_motor.py --motor 1 --ppr 4532 --tope-vel 130 --pwm-max 0.7

Lee identificacion/modelo_motorN.json (herramientas/identificar_motor.py), arma la trayectoria del programa «4 caras + volcado» para ese motor (con el tope de velocidad pedido,
que alarga los tiempos si hace falta), busca las ganancias de PID de posición y de cascada, compara con los valores de fábrica del firmware y valida las ganancias elegidas
contra variaciones de la planta (K ±25 %, zona muerta ×2, τ ×1.5). Escribe identificacion/ganancias_motorN.json con las órdenes {"cfg":...} para la placa.
Es una simulación del motor LIBRE: con el mecanismo acoplado hay que repetir la identificación y la sintonía.
"""
import argparse
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spm import SPM, cargar_config, desde_inclinacion, planificar_con_paradas  # noqa: E402
from spm import trayectoria as tj  # noqa: E402
from spm.sintonia import Ganancias, Planta, a_json, buscar, conservadora, costo, metricas, simular  # noqa: E402


def trayectoria_programa(tope_vel_degs: float, espera: float, motor: int):
    """(t, q_motor_deg) del programa de las 4 caras + volcado para ese motor, con tope de velocidad por motor (°/s)."""
    spm = SPM()
    trans, lim = cargar_config()
    lim.vel_motor_max = math.radians(tope_vel_degs)
    filas, _ = tj.programa_caja(spm, lim, 0.0, espera)
    Rs = [desde_inclinacion(*np.radians(w[:3])) for w in filas]
    Rx, Tx = tj.expandir_esperas(Rs, [w[4] for w in filas], [w[3] for w in filas[1:]])
    tr = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.02)
    return tr.t, np.degrees(tr.motor[:, motor - 1]), float(np.degrees(np.abs(tr.motor_vel[:, motor - 1])).max())


def fila(nombre, m):
    return "  %-34s mov: máx %5.2f° rms %5.2f° | pausa: error %5.2f° vibración %4.2f° duty %4.1f%% cambios/s %5.1f%s" % (
        nombre, m["error_max_mov_deg"], m["error_rms_deg"], m["pausa_error_max_deg"], m["pausa_vibracion_pp_deg"], 100 * m["pausa_duty_medio"],
        m["pausa_cambios_signo_por_s"], "  INESTABLE" if m["inestable"] else "")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--motor", type=int, default=1)
    ap.add_argument("--modelo", type=Path, help="def.: identificacion/modelo_motorN.json")
    ap.add_argument("--ppr", type=float, required=True, help="cuentas por vuelta de salida (las del modelo)")
    ap.add_argument("--tope-vel", type=float, default=130.0, help="tope de velocidad del motor en el programa [°/s]")
    ap.add_argument("--pwm-max", type=float, default=0.7)
    ap.add_argument("--iter", type=int, default=80)
    ap.add_argument("--salida", type=Path)
    a = ap.parse_args(argv)
    raiz = Path(__file__).resolve().parents[1]
    modelo = a.modelo or raiz / "identificacion" / ("modelo_motor%d.json" % a.motor)
    planta = Planta.desde_modelo(modelo, a.ppr)
    print("Planta: K %.0f/%.0f °/s, u0 %.3f/%.3f, τ %.1f ms, %.0f cuentas/vuelta (%.4f °/cuenta)" % (
        planta.K_pos, planta.K_neg, planta.u0_pos, planta.u0_neg, planta.tau * 1e3, planta.ppr, planta.dpc))
    t_c, q_c, pico = trayectoria_programa(a.tope_vel, 1.5, a.motor)
    t_l, q_l, _ = trayectoria_programa(a.tope_vel, 5.0, a.motor)
    print("Programa con tope %.0f °/s: pico %.0f °/s (%.0f %% de la velocidad libre ≈ %.0f °/s), %.1f s con pausas de 5 s\n" % (
        a.tope_vel, pico, 100 * pico / (planta.K_pos * (1 - planta.u0_pos)), planta.K_pos * (1 - planta.u0_pos), t_l[-1]))
    elegido = {}
    for ctrl in ("pid", "casc"):
        print("Buscando ganancias (%s)…" % ctrl)
        g, m, c = buscar(planta, t_c, q_c, ctrl, a.pwm_max, a.iter, log=print)
        elegido[ctrl] = (g, c)
    print("\nValidación sobre el programa completo (pausas de 5 s):")
    ref = {"Firmware de fábrica (pid)": Ganancias(pwm_max=a.pwm_max)}
    for ctrl, (g, c) in elegido.items():
        ref["Sintonizado %s" % ctrl] = g
    for nombre, g in ref.items():
        print(fila(nombre, metricas(simular(planta, g, t_l, q_l), t_l, q_l)))
    mejor = min(elegido, key=lambda k: elegido[k][1])
    g_opt = elegido[mejor][0]
    g = conservadora(g_opt)
    m_opt, m_cons = (metricas(simular(planta, x, t_l, q_l), t_l, q_l) for x in (g_opt, g))
    print("\nPara la PRIMERA prueba real (ganancia de posición limitada: la búsqueda tiende al borde del rango y el simulador no tiene holguras ni ruido eléctrico):")
    print(fila("%s optimizado (kp %.3g, kpp %.3g)" % (mejor, g_opt.kp, g_opt.kpp), m_opt))
    print(fila("%s CONSERVADOR (kp %.3g, kpp %.3g)" % (mejor, g.kp, g.kpp), m_cons))
    print("\nRobustez del CONSERVADOR (%s):" % mejor)
    variantes = (("K ×1.25 (p. ej. fuente de 12 V)", (1.25, 1, 1)), ("K ×0.75 (fuente que cae)", (0.75, 1, 1)), ("zona muerta ×2 (más fricción)", (1, 2, 1)),
                 ("τ ×1.5 (más inercia)", (1, 1, 1.5)), ("K ×1.25 y τ ×1.5", (1.25, 1, 1.5)))
    for nombre, v in variantes:
        print(fila(nombre, metricas(simular(planta.variante(*v), g, t_l, q_l), t_l, q_l)))
    salida = a.salida or raiz / "identificacion" / ("ganancias_motor%d.json" % a.motor)
    a_json(g, planta, m_cons, a.motor, salida, optimizadas=g_opt, metr_opt=m_opt)
    print("\nGanancias CONSERVADORAS (%s) guardadas en %s (las optimizadas, también). Órdenes para la placa:" % (mejor, salida))
    for c in g.comandos(a.motor):
        print("  ", c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
