"""Línea de comandos:  python -m spm <ik|fk|demo|mapa> ..."""
import argparse
import math
import numpy as np
from pathlib import Path

from . import (SPM, cargar_config, desde_inclinacion, a_inclinacion, FueraDeEspacioDeTrabajo, planificar, CaminoPrecesion,
               CaminoInclinacion, planificar_con_paradas, seguir_trayectoria)


def _f(v):
    return "[" + ", ".join("%+8.3f" % x for x in v) + "]"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spm", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ik", help="orientación -> ángulos")
    p.add_argument("--inclinacion", type=float, default=0.0, help="grados"); p.add_argument("--azimut", type=float, default=0.0)
    p.add_argument("--torsion", type=float, default=0.0)
    p = sub.add_parser("fk", help="ángulos de motor -> orientación"); p.add_argument("motor", type=float, nargs=3, help="grados de motor 1 2 3")
    p = sub.add_parser("demo", help="genera trayectorias, gráficas y animación"); p.add_argument("--salida", default="salida")
    p.add_argument("--inclinacion", type=float, default=12.0)
    p = sub.add_parser("modelo", help="imagen del mecanismo CAD en una pose (orientación -> modelo 3D)")
    p.add_argument("--inclinacion", type=float, default=12.0); p.add_argument("--azimut", type=float, default=40.0)
    p.add_argument("--torsion", type=float, default=0.0); p.add_argument("--salida", default="salida")
    p = sub.add_parser("demo3d", help="video del modelo CAD siguiendo una trayectoria de precesión"); p.add_argument("--salida", default="salida")
    p.add_argument("--inclinacion", type=float, default=12.0); p.add_argument("--duracion", type=float, default=6.0)
    p = sub.add_parser("caja", help="programa «4 caras de una caja + volcado»: CSV de motores y vídeo del CAD"); p.add_argument("--salida", default="salida")
    p.add_argument("--azimut", type=float, default=0.0, help="dirección del volcado (°)"); p.add_argument("--espera", type=float, default=5.0)
    p.add_argument("--sin-video", action="store_true")
    p = sub.add_parser("caja_motores", help="programa de la caja con los motores IDENTIFICADOS y su lazo de posición (identificacion/*.json)"); p.add_argument("--salida", default="salida")
    p.add_argument("--azimut", type=float, default=0.0); p.add_argument("--espera", type=float, default=5.0)
    p.add_argument("--tope-vel", type=float, default=130.0, help="tope de velocidad por motor (°/s) al planificar")
    sub.add_parser("colisiones", help="recalcula el mapa de colisiones con el CAD (varios minutos)")
    p = sub.add_parser("mapa", help="mapa del espacio de trabajo"); p.add_argument("--salida", default="salida")
    p.add_argument("--torsion", type=float, default=0.0)
    a = ap.parse_args(argv)
    spm = SPM(); trans, lim = cargar_config()

    if a.cmd == "ik":
        R = desde_inclinacion(*np.radians([a.inclinacion, a.azimut, a.torsion]))
        try:
            th = spm.ik(R)
        except FueraDeEspacioDeTrabajo as e:
            print("sin solución:", e); return 1
        ok, why = lim.valida(spm, R, th)
        print("NNR   (°):", _f(np.degrees(th)))
        print("motor (°):", _f(np.degrees(trans.a_motor(th))), "  (relación %.3f, signos por pata %s)" % (trans.relacion, [int(x) for x in trans.signos_pata]))
        print("dentro de límites" if ok else "FUERA de límites: " + why)
    elif a.cmd == "fk":
        th = trans.a_nnr(np.radians(a.motor))
        R = spm.fk(th)
        t, az, tor = a_inclinacion(R)
        print("inclinación %.3f°  azimut %.2f°  torsión %.3f°" % (math.degrees(t), math.degrees(az), math.degrees(tor)))
        print("normal de la plataforma:", R[:, 2].round(4))
    elif a.cmd == "demo":
        from . import visual
        out = Path(a.salida); out.mkdir(exist_ok=True)
        t1 = planificar(spm, trans, lim, CaminoPrecesion(a.inclinacion, vueltas=1), T=6.0, perfil="quintico")
        print("precesión %.0f°: %.2f s (x%.2f), %d muestras fuera de límites" % (a.inclinacion, t1.duracion, t1.factor_tiempo, len(t1.violaciones)))
        visual.graficar_trayectoria(t1, out / "precesion.png", "Precesión a %.0f°" % a.inclinacion)
        visual.animar(spm, t1, str(out / "precesion.gif"))
        reg = seguir_trayectoria(spm, trans, lim, t1)
        print("seguimiento: error de orientación máx %.2f°, medio %.2f°" % (reg.error_deg.max(), reg.error_deg.mean()))
        visual.graficar_seguimiento(reg, out / "seguimiento.png")
        wp = [desde_inclinacion(0, 0, 0), desde_inclinacion(*np.radians([10, 0, 0])), desde_inclinacion(*np.radians([10, 120, 15])),
              desde_inclinacion(*np.radians([6, 240, -15])), desde_inclinacion(0, 0, 0)]
        t2 = planificar_con_paradas(spm, trans, lim, wp, T_tramo=1.5)
        print("waypoints con paradas: %.2f s, %d muestras fuera de límites" % (t2.duracion, len(t2.violaciones)))
        visual.graficar_trayectoria(t2, out / "waypoints.png", "Waypoints con paradas")
        print("salida en", out.resolve())
    elif a.cmd == "modelo":
        from .modelo3d import ModeloCAD, imagen_pose
        from .render_gl import RenderGL
        out = Path(a.salida); out.mkdir(exist_ok=True)
        R = desde_inclinacion(*np.radians([a.inclinacion, a.azimut, a.torsion])); th = spm.ik(R)
        ok, why = lim.valida(spm, R, th)
        texto = "inclinación %.1f°  azimut %.0f°  torsión %.1f°\nmotores %s°\n%s" % (a.inclinacion, a.azimut, a.torsion, np.degrees(trans.a_motor(th)).round(1), "dentro de límites" if ok else "FUERA: " + why)
        gl = RenderGL.crear(spm)
        if gl is not None:
            gl.guardar_png(R, th, out / "modelo_pose.png", texto=texto, ancho=1100, alto=900)
        else:
            imagen_pose(ModeloCAD(spm), R, th, out / "modelo_pose.png", texto=texto)
        print("guardado", out / "modelo_pose.png", "|", "dentro de límites" if ok else "FUERA de límites: " + why)
    elif a.cmd == "demo3d":
        import time
        from .modelo3d import ModeloCAD, animar_modelo
        from .render_gl import RenderGL
        out = Path(a.salida); out.mkdir(exist_ok=True)
        tray = planificar(spm, trans, lim, CaminoPrecesion(a.inclinacion, vueltas=1), T=a.duracion)
        print("trayectoria: %.1f s, %d muestras fuera de límites" % (tray.duracion, len(tray.violaciones)))
        gl = RenderGL.crear(spm)
        def txt(k, tr):
            return "t = %5.2f s\ninclinación %5.1f°  azimut %4.0f°  torsión %5.1f°\nmotores  %+6.1f°  %+6.1f°  %+6.1f°" % (
                tr.t[k], math.degrees(tr.inclinacion[k]), math.degrees(tr.azimut[k]) % 360, math.degrees(tr.torsion[k]), *np.degrees(tr.motor[k]))
        t0 = time.time()
        if gl is not None:
            f = gl.video(tray, out / "modelo3d_precesion.mp4", texto=txt)
        else:
            f = animar_modelo(ModeloCAD(spm), tray, trans, out / "modelo3d_precesion.mp4", paso=max(1, len(tray.t) // 120), fps=20)
        print("video:", f, "(%.0f s)" % (time.time() - t0))
    elif a.cmd == "caja":
        from . import trayectoria as tj
        from .render_gl import RenderGL
        out = Path(a.salida); out.mkdir(exist_ok=True)
        filas, tmax = tj.programa_caja(spm, lim, a.azimut, a.espera)
        Rs = [desde_inclinacion(*np.radians(w[:3])) for w in filas]; Ts = [w[3] for w in filas[1:]]
        Rx, Tx = tj.expandir_esperas(Rs, [w[4] for w in filas], Ts)
        tray = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.02)
        print("volcado a %.1f° hacia azimut %.0f° · duración %.1f s · %d muestras fuera de límites · motores máx %.0f° · pico %.1f rpm" % (
            tmax, a.azimut, tray.duracion, len(tray.violaciones), np.degrees(np.abs(tray.motor)).max(), np.degrees(np.abs(tray.motor_vel)).max() / 6))
        for w in filas: print("   incl %5.1f° az %4.0f° tors %5.0f°  T %.1f s  espera %.1f s" % (w[0], w[1], w[2], w[3], w[4]))
        tj.a_csv(tray, out / "caja_trayectoria.csv"); print("CSV:", out / "caja_trayectoria.csv")
        gl = None if a.sin_video else RenderGL.crear(spm)
        if gl is not None:
            f = gl.video(tray, out / "caja_trayectoria.mp4", texto=lambda k, tr: "t = %5.1f s\ninclinación %5.1f°  azimut %4.0f°  torsión %6.1f°\nmotores %+7.1f° %+7.1f° %+7.1f°" % (
                tr.t[k], math.degrees(tr.inclinacion[k]), math.degrees(tr.azimut[k]) % 360, math.degrees(tr.torsion[k]), *np.degrees(tr.motor[k])))
            print("vídeo:", f)
    elif a.cmd == "caja_motores":
        from . import trayectoria as tj
        from .motores_identificados import seguir_con_motores_identificados
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        out = Path(a.salida); out.mkdir(exist_ok=True)
        lim.vel_motor_max = math.radians(a.tope_vel)
        filas, tmax = tj.programa_caja(spm, lim, a.azimut, a.espera)
        Rs = [desde_inclinacion(*np.radians(w[:3])) for w in filas]
        Rx, Tx = tj.expandir_esperas(Rs, [w[4] for w in filas], [w[3] for w in filas[1:]])
        tray = planificar_con_paradas(spm, trans, lim, Rx, Tx, 0.04)
        reg, info = seguir_con_motores_identificados(spm, trans, lim, tray)
        print("duración %.1f s · error del plato: máx %.2f°  rms %.2f° (incluye el retraso de un periodo de consigna)" % (tray.duracion, info["error_plato_max_deg"], info["error_plato_rms_deg"]))
        for j, (m, e, d) in enumerate(zip(info["por_motor"], info["error_motor_max_deg"], info["duty_max"]), 1):
            print("  motor %d: error máx %.2f° · rms %.2f° · en pausas %.3f° · ciclo de trabajo máx %.2f%s" % (j, m["error_max_deg"], m["error_rms_deg"], m["pausa_error_max_deg"], d, "  (SATURA)" if d >= 0.69 else ""))
        fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for j in range(3):
            ax[0].plot(reg.t, np.degrees(reg.motor_ref[:, j] - reg.motor_ref[0, j]), "--", lw=1); ax[0].plot(reg.t, np.degrees(reg.motor_med[:, j] - reg.motor_ref[0, j]), lw=1.2, label="motor %d" % (j + 1))
        ax[0].set_ylabel("ángulo de motor [°]"); ax[0].legend(); ax[0].grid(alpha=.3)
        ax[1].plot(reg.t, reg.error_deg); ax[1].set_ylabel("error de orientación del plato [°]"); ax[1].set_xlabel("t [s]"); ax[1].grid(alpha=.3)
        fig.tight_layout(); fig.savefig(out / "caja_motores_identificados.png", dpi=110); print("figura:", out / "caja_motores_identificados.png")
    elif a.cmd == "colisiones":
        from . import colisiones
        from .modelo3d import ModeloCAD
        m = colisiones.calcular(spm, ModeloCAD(spm)); m.guardar(); print("mapa guardado en", colisiones.RUTA_MAPA)
    elif a.cmd == "mapa":
        from . import visual
        out = Path(a.salida); out.mkdir(exist_ok=True)
        est, _ = visual.mapa_espacio_trabajo(spm, lim, out / "mapa_espacio_trabajo.png", torsion_deg=a.torsion)
        print("permitido: %.0f %% de las orientaciones muestreadas hasta 30°" % (100 * (est == 2).mean()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
