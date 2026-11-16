#!/usr/bin/env python3
"""ESP32 simulado para probar la pestaña Serial sin hardware (solo Linux: usa un pseudo-terminal).

Crea un puerto virtual, imprime su ruta (p. ej. /dev/pts/7) y se comporta como el firmware del brazo 3DOF:
  recibe  {"m1":a,"m2":b,"m3":c} | {"motor":N,"pos":deg} | {"cmd":"reset"} | {"zero":N|"all"}   (grados de motor)
  envía   {"m1":{"posRef":pulsos,"pos":pulsos,"ok":true},"m2":{...},"m3":{...}}  cada 50 ms
Los motores siguen la consigna con un retardo de primer orden y velocidad limitada.
Además entiende las órdenes SmartPharma del firmware (tablas de trayectorias_smartpharma/trayectorias.json, que genera herramientas/generar_trayectorias_smartpharma.py):
  {"cmd":"cam","cara":N} · {"cmd":"clas","destino":"TIPO_A|TIPO_B|TIPO_C|DESCARTE|DESCARTE_1|DESCARTE_2"} · {"cmd":"home"} · {"cfg":"descarte","val":1|2}
  y responde {"ack":"traj_done",...} / {"err":"traj","why":...}. (La fidelidad completa, con el lazo de control y el canal UART, está en firmware/test_host/esp32_host.cpp.)
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
import pathlib
_J = pathlib.Path(__file__).resolve().parent.parent / "trayectorias_smartpharma" / "trayectorias.json"
PLANES = json.loads(_J.read_text(encoding="utf-8"))["planes"] if _J.exists() else {}
seq = None; descarte = 1                                  # seq = {"legs": [...], "i": 0, "t": 0.0, "msg": ...}


def _etapa(pos):
    """0 = home, k = cara k (cada CAM_k parte de la pose k-1 y termina en la k), None si no coincide con ninguna."""
    cand = {0: PLANES.get("CAM_1", {}).get("tramos", [{}])[0].get("q0_deg")}
    for k in range(1, 5):
        if "CAM_%d" % k in PLANES: cand[k] = PLANES["CAM_%d" % k]["tramos"][0]["q1_deg"]
    for e, q in cand.items():
        if q and max(abs(q[j] - pos[j]) for j in range(3)) <= 4.0: return e
    return None


def _orden_smartpharma(d):
    global seq
    if seq is not None: return {"err": "traj", "why": "ocupado"}
    if d.get("cmd") == "cam":
        k = int(d.get("cara", 0)); e = _etapa(pos)
        if k == 5: return {"err": "traj", "why": "cara_no_implementada"}
        if e != k - 1 or "CAM_%d" % k not in PLANES: return {"err": "traj", "why": "fuera_de_secuencia"}
        seq = {"legs": PLANES["CAM_%d" % k]["tramos"], "i": 0, "t": 0.0, "msg": {"ack": "traj_done", "kind": "cam", "cara": k}}
    elif d.get("cmd") == "clas":
        dest = d.get("destino"); e = _etapa(pos)
        if dest == "DESCARTE": dest = "DESCARTE_%d" % descarte
        if dest == "DESCARTE_1": dest = "TIPO_A"
        nombre = "DESC2_E%s" % e if dest == "DESCARTE_2" else "CLAS_%s_E%s" % (dest, e)
        if e is None: return {"err": "traj", "why": "pose_desconocida"}
        if nombre not in PLANES: return {"err": "traj", "why": "destino_invalido"}
        seq = {"legs": PLANES[nombre]["tramos"], "i": 0, "t": 0.0, "msg": {"ack": "traj_done", "kind": "clas", "destino": d.get("destino")}}
    elif d.get("cmd") == "home":
        m = max(abs(x) for x in pos); T = max(3.0, 1.875 * m / 100.0)
        seq = {"legs": [{"q0_deg": list(pos), "q1_deg": [0, 0, 0], "t_mov_s": T, "t_hold_s": 0.5}], "i": 0, "t": 0.0, "msg": {"ack": "traj_done", "kind": "home"}}
    return None


def _avanzar_seq(dt):
    global seq
    seq["t"] += dt
    while True:
        L = seq["legs"][seq["i"]]; tot = L["t_mov_s"] + L["t_hold_s"]
        if seq["t"] < tot or seq["i"] >= len(seq["legs"]) - 1: break
        seq["t"] -= tot; seq["i"] += 1
    x = min(1.0, seq["t"] / L["t_mov_s"]) if L["t_mov_s"] > 0 else 1.0
    f = 10 * x ** 3 - 15 * x ** 4 + 6 * x ** 5
    for j in range(3): ref[j] = L["q0_deg"][j] + (L["q1_deg"][j] - L["q0_deg"][j]) * f
    if seq["i"] == len(seq["legs"]) - 1 and seq["t"] >= tot:
        msg, seq = seq["msg"], None
        return msg
    return None
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
            if d.get("cmd") in ("cam", "clas", "home"):
                r = _orden_smartpharma(d)
                if r: os.write(maestro, (json.dumps(r) + "\n").encode())
            elif d.get("cfg") == "descarte": descarte = 2 if int(d.get("val", 1)) == 2 else 1
            elif seq is not None and ("m1" in d or "motor" in d or d.get("cmd") in ("reset", "stop") or "zero" in d):
                if d.get("cmd") in ("reset", "stop"): seq = None; ref = list(pos)
                else: os.write(maestro, (json.dumps({"err": "traj", "why": "ocupado"}) + "\n").encode())
            elif all(k in d for k in ("m1", "m2", "m3")): ref = [float(d["m1"]), float(d["m2"]), float(d["m3"])]
            elif "motor" in d and "pos" in d: ref[int(d["motor"]) - 1] = float(d["pos"])
            elif d.get("cmd") == "reset": ref = list(pos)                    # el brazo real: pone la referencia en la posición actual (frena)
            elif "zero" in d:
                for j in (range(3) if d["zero"] == "all" else [int(d["zero"]) - 1]): pos[j] = ref[j] = 0.0
    now = time.monotonic(); dt = now - t_prev; t_prev = now
    if seq is not None:
        fin = _avanzar_seq(dt)
        if fin: os.write(maestro, (json.dumps(fin) + "\n").encode())
    for j in range(3):
        v = max(-a.vmax, min(a.vmax, (ref[j] - pos[j]) / a.tau)); pos[j] += v * dt
    if now - t_tel >= 0.05:
        t_tel = now
        msg = {"m%d" % (j + 1): {"posRef": round(ref[j] / 360 * ppr[j]), "pos": round(pos[j] / 360 * ppr[j]), "ok": abs(ref[j] - pos[j]) < 1.0} for j in range(3)}
        try:
            os.write(maestro, (json.dumps(msg, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass
