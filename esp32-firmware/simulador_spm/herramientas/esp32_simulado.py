#!/usr/bin/env python3
"""ESP32 simulado para probar la pestaña Serial sin hardware (solo Linux: usa un pseudo-terminal).

Crea un puerto virtual, imprime su ruta (p. ej. /dev/pts/7) y se comporta como el firmware del brazo 3DOF:
  recibe  {"m1":a,"m2":b,"m3":c} | {"motor":N,"pos":deg} | {"cmd":"reset"} | {"zero":N|"all"}   (grados de motor)
  envía   {"m1":{"posRef":pulsos,"pos":pulsos,"ok":true},"m2":{...},"m3":{...}}  cada 50 ms
Los motores siguen la consigna con un retardo de primer orden y velocidad limitada.
Uso:  python herramientas/esp32_simulado.py [--ppr 1000,1000,1000] [--tau 0.15] [--vmax 360]
"""
import argparse
import json
import os
import pty
import select
import time
import tty

ap = argparse.ArgumentParser()
ap.add_argument("--ppr", default="1000,1000,1000"); ap.add_argument("--tau", type=float, default=0.15); ap.add_argument("--vmax", type=float, default=360.0, help="deg/s")
a = ap.parse_args()
ppr = [float(x) for x in a.ppr.split(",")]
maestro, esclavo = pty.openpty()
tty.setraw(esclavo)                                   # sin eco ni procesado de líneas
print("PUERTO:", os.ttyname(esclavo), flush=True)
pos = [0.0, 0.0, 0.0]; ref = [0.0, 0.0, 0.0]; buf = b""; t_prev = time.monotonic(); t_tel = 0.0
while True:
    r, _, _ = select.select([maestro], [], [], 0.01)
    if r:
        try:
            buf += os.read(maestro, 1024)
        except OSError:
            break
        while b"\n" in buf:
            lin, buf = buf.split(b"\n", 1)
            try:
                d = json.loads(lin.decode())
            except ValueError:
                continue
            if all(k in d for k in ("m1", "m2", "m3")): ref = [float(d["m1"]), float(d["m2"]), float(d["m3"])]
            elif "motor" in d and "pos" in d: ref[int(d["motor"]) - 1] = float(d["pos"])
            elif d.get("cmd") == "reset": ref = list(pos)                    # el brazo real: pone la referencia en la posición actual (frena)
            elif "zero" in d:
                for j in (range(3) if d["zero"] == "all" else [int(d["zero"]) - 1]): pos[j] = ref[j] = 0.0
    now = time.monotonic(); dt = now - t_prev; t_prev = now
    for j in range(3):
        v = max(-a.vmax, min(a.vmax, (ref[j] - pos[j]) / a.tau)); pos[j] += v * dt
    if now - t_tel >= 0.05:
        t_tel = now
        msg = {"m%d" % (j + 1): {"posRef": round(ref[j] / 360 * ppr[j]), "pos": round(pos[j] / 360 * ppr[j]), "ok": abs(ref[j] - pos[j]) < 1.0} for j in range(3)}
        try:
            os.write(maestro, (json.dumps(msg, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass
