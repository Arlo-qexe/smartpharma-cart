"""Salida del generador SmartPharma: JSON, CSV, cabecera C para el firmware, tabla de poses y gráficas. Lo usan `python -m spm smartpharma` y
`herramientas/generar_trayectorias_smartpharma.py`."""
import argparse
import json
import math
from pathlib import Path
import numpy as np

from . import SPM, cargar_config as cargar_limites
from . import smartpharma as sp

RAIZ = Path(__file__).resolve().parent.parent
CABECERA = RAIZ / "firmware" / "spm_esp32s3" / "spm_trayectorias.h"
SALIDA = RAIZ / "trayectorias_smartpharma"
C_M = ["#0072B2", "#D55E00", "#009E73"]          # Okabe-Ito: distinguibles con daltonismo


def tabla_poses_md(gen):
    d = sp.a_dict(gen)
    L = ["| Pose | incl (°) | azimut (°) | torsión (°) | M1 (°) | M2 (°) | M3 (°) | holgura (mm) | sigma_min | B_min |", "|---|---|---|---|---|---|---|---|---|---|"]
    for n, p in d["poses"].items():
        L.append("| %s | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %.1f | %.2f | %.2f |" % (n, p["incl_deg"], p["az_deg"], p["torsion_deg"], *p["motor_deg"], p["holgura_mm"], p["sigma_min"], p["B_min"]))
    L += ["", "| Plan | duración (s) | tramos (t_mov + t_hold, holgura mín mm, pico °/s motor) |", "|---|---|---|"]
    for n, pl in d["planes"].items():
        if n.endswith(("E0", "E1", "E2", "E3")) and not n.startswith("CAM"):
            continue
        L.append("| %s | %.2f | %s |" % (n, pl["duracion_s"], "; ".join("%s %.2f+%.2f, %.1f, %.0f" % (t["nombre"], t["t_mov_s"], t["t_hold_s"], t["holgura_min_mm"], t["vel_pico_dps"]) for t in pl["tramos"])))
    return "\n".join(L)


def graficar_plan(gen, plan, archivo, titulo):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tr = sp.muestrear(plan, gen.trans); t = tr.t
    h = sp._holgura(gen.lim, tr)
    fig, ax = plt.subplots(4, 1, figsize=(9, 9), sharex=True)
    for j in range(3):
        ax[0].plot(t, np.degrees(tr.motor[:, j]), color=C_M[j], lw=1.6, label="M%d" % (j + 1))
        ax[3].plot(t, np.degrees(tr.motor_vel[:, j]), color=C_M[j], lw=1.2, label="M%d" % (j + 1))
    ax[0].set_ylabel("motor (°)"); ax[0].legend(ncol=3, loc="best")
    ax[1].plot(t, np.degrees(tr.inclinacion), color="#333333", lw=1.6, label="inclinación")
    tors = np.degrees(np.unwrap(tr.torsion)); tors = tors + 360.0 * round((plan.tramos[0].a.tors - tors[0]) / 360.0)      # torsión acumulada (sin saltos de 360°)
    ax[1].plot(t, tors, color="#CC79A7", lw=1.6, label="torsión")
    ax[1].set_ylabel("plato (°)"); ax[1].legend(ncol=2, loc="best")
    ax[2].plot(t, h, color="#E69F00", lw=1.6); ax[2].axhline(gen.lim.holgura_min_mm, color="#B00020", lw=1, ls="--"); ax[2].text(t[-1], gen.lim.holgura_min_mm, " mínimo %.1f mm" % gen.lim.holgura_min_mm, va="bottom", ha="right", fontsize=8, color="#B00020")
    ax[2].set_ylabel("holgura CAD (mm)"); ax[2].set_ylim(0, max(10, np.nanmax(h) * 1.05))
    ax[3].axhline(math.degrees(gen.lim.vel_motor_max), color="#B00020", lw=1, ls="--"); ax[3].axhline(-math.degrees(gen.lim.vel_motor_max), color="#B00020", lw=1, ls="--")
    ax[3].set_ylabel("velocidad motor (°/s)"); ax[3].set_xlabel("t (s)")
    t0 = 0.0
    for tm in plan.tramos:                                   # límites de tramo
        t0 += tm.duracion
        for a in ax:
            a.axvline(t0, color="#999999", lw=0.6, ls=":")
    for a in ax:
        a.grid(alpha=0.25); a.set_xlim(0, t[-1])
    ax[0].set_title(titulo, fontsize=11)
    fig.text(0.01, 0.005, "Gráfica regenerada con los datos de la trayectoria planificada (no es una captura de la interfaz). Líneas punteadas verticales: fin de cada tramo.", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.015, 1, 1)); fig.savefig(archivo, dpi=110); plt.close(fig)


def graficar_planta(gen, archivo):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    e = gen.cfg["entorno"]; off = e["offset_azimut_base_deg"]
    fig = plt.figure(figsize=(6.5, 6.5)); ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("E"); ax.set_theta_direction(1)
    th = np.linspace(0, 2 * np.pi, 200); ax.plot(th, np.full_like(th, 125), color="#333333", lw=1.5)
    for s in gen.slots:
        a = np.radians(s["az_deg"]); ancho = np.radians(26)
        ax.bar(a, 117, width=2 * ancho, bottom=128, color="#009E73", alpha=0.35, edgecolor="#009E73")
        ax.text(a, 190, "%s\n%.1f°" % (s["tipo"], s["az_deg"]), ha="center", va="center", fontsize=9)
    sal = np.radians(e["azimut_salida_deg"] + off); cam = np.radians(e["azimut_camara_deg"] + off)
    ax.annotate("", xy=(sal + np.pi, 120), xytext=(sal, 215), arrowprops=dict(arrowstyle="->", color="#D55E00", lw=2))
    ax.text(sal, 235, "dispensador\n(salida %.1f°)" % (e["azimut_salida_deg"] + off), ha="center", fontsize=9, color="#D55E00")
    ax.plot([cam], [181], "s", color="#0072B2", ms=9); ax.text(cam, 215, "cámara %.1f°" % (e["azimut_camara_deg"] + off), ha="center", fontsize=9, color="#0072B2")
    ax.set_rmax(250); ax.set_rticks([]); ax.set_title("Planta (marco del simulador, azimut antihorario). Orden TIPO_A→C: izquierda→derecha\nviendo desde el dispensador hacia el plato", fontsize=9)
    fig.tight_layout(); fig.savefig(archivo, dpi=110); plt.close(fig)


def generar(salida=SALIDA, cabecera=CABECERA, ruta_config=None, descarte=None, graficas=True, verbose=True):
    spm = SPM(); trans, lim = cargar_limites()
    gen = sp.Generador(spm, trans, lim, sp.cargar_config(ruta_config))
    gen.todos()
    salida = Path(salida); (salida / "muestras").mkdir(parents=True, exist_ok=True)
    d = sp.a_dict(gen)
    (salida / "trayectorias.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    sp.a_csv_puntos(gen, salida / "puntos_de_paso.csv")
    for nombre, pl in gen.planes.items():
        _csv_muestras(gen, pl, salida / "muestras" / ("%s.csv" % nombre))
    if cabecera:
        sp.a_cabecera(gen, cabecera, descarte)
    gen.guardar_cache()
    md = tabla_poses_md(gen)
    (salida / "tabla_poses.md").write_text(md, encoding="utf-8")
    if graficas:
        (salida / "graficas").mkdir(exist_ok=True)
        graficar_planta(gen, salida / "graficas" / "planta_azimuts.png")
        n = len(gen.poses_cam) - 1
        cam = sp.Plan("CAMARA", "Cámara", "home", [gen.planes["CAM_%d" % k].tramos[0] for k in range(1, n + 1)])
        graficar_plan(gen, cam, salida / "graficas" / "camara_4caras.png", "Cámara: caras 1 a %d (un tramo por orden de la Orange Pi)" % n)
        for s in gen.slots:
            graficar_plan(gen, gen.planes["CLAS_%s_E%d" % (s["tipo"], n)], salida / "graficas" / ("clasificar_%s.png" % s["tipo"]),
                          "Clasificar %s (slot %.1f°, torsión de volcado %.0f°)" % (s["tipo"], s["az_deg"], gen.torsion_volcado[s["tipo"]]))
        graficar_plan(gen, gen.planes["DESC2_E%d" % n], salida / "graficas" / "descarte_2.png", "DESCARTE_2: espera %.0f s en la cara %d y vuelve a home" % (gen.cfg["tiempos"]["espera_descarte2_s"], n))
    if verbose:
        print("Orden de slots (izquierda -> derecha viendo desde el dispensador):")
        for s in gen.slots:
            print("  %s: slot CAD %s, azimut %.2f°, ángulo de vista %+.1f° (torsión de volcado %.0f°)" % (s["tipo"], s["slot"], s["az_deg"], s["ang_vista_deg"], gen.torsion_volcado[s["tipo"]]))
        print(md)
        print("\nSalida en", salida, "| cabecera:", cabecera)
    return gen, d


def _csv_muestras(gen, pl, archivo):
    from .trayectoria import a_csv
    a_csv(sp.muestrear(pl, gen.trans), archivo)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spm smartpharma", description="Genera las trayectorias SmartPharma (JSON, CSV, cabecera del firmware y gráficas).")
    ap.add_argument("--config", help="JSON con valores que reemplazan a datos/smartpharma_config.json")
    ap.add_argument("--salida", default=str(SALIDA)); ap.add_argument("--cabecera", default=str(CABECERA), help="'' para no escribir la cabecera del firmware")
    ap.add_argument("--descarte", type=int, choices=(1, 2), help="variante de DESCARTE por defecto escrita en la cabecera (1 = igual que TIPO_A, 2 = espera y home)")
    ap.add_argument("--sin-graficas", action="store_true")
    a = ap.parse_args(argv)
    generar(a.salida, a.cabecera or None, a.config, a.descarte, not a.sin_graficas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
