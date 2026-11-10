# Exporta las mallas del CAD agrupadas por cuerpo cinemático -> datos/malla_cuerpos.npz  (ejecutar con:  FreeCADCmd herramientas/exportar_malla_cad.py)
# Cuerpos: fijo | plataforma | nnrK (NNRK + su Brasop) | brazovK (Brazov + sus 2 rodamientos).  Coordenadas del ensamblaje (mm), pose de ensamblaje.
import json, os
import numpy as np
import FreeCAD as App

AQUI = os.environ.get("SPM_DIR", "/home/arlo/Documents/simulador_spm")
CAD = os.environ.get("SPM_CAD", "/home/arlo/Documents/FreeCAD/spm_trabajo/Ensamblaje2510_trabajo.FCStd")
G = json.load(open(AQUI + "/datos/geometria.json"))
doc = App.openDocument(CAD)
obj = {o.Label: o for o in doc.Objects if o.TypeId == "Part::Feature" and not o.Name.startswith("ver_")}

cuerpos = {"fijo": list(G["fijas"]), "plataforma": [p["plato"] for p in G["patas"]] + list(G["plataforma_extra"])}
for p in G["patas"]:
    k = p["nnr"]
    cuerpos["nnr%d" % k] = ["NNR%d" % k, p["brasop"]]
    cuerpos["brazov%d" % k] = [p["brazov"], p["rodamiento_brasop"], p["rodamiento_plato"]]

def decimar(V, F, celda):
    key = np.floor(V / celda).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    nv = np.zeros((len(uniq), 3)); np.add.at(nv, inv, V); nv /= np.bincount(inv)[:, None]
    F2 = inv[F]
    ok = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    return nv, F2[ok]

def malla(shape, objetivo):
    vs, tr = shape.tessellate(0.3)
    V = np.array([[v.x, v.y, v.z] for v in vs]); F = np.array(tr, dtype=np.int64)
    # puntos para colisiones: muestreo por área de la malla fina
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    ar = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    rng = np.random.default_rng(0); k = int(min(3000, max(600, ar.sum() / 2.0)))
    i = rng.choice(len(F), size=k, p=ar / ar.sum()); u = rng.random((k, 2)); m = u.sum(1) > 1; u[m] = 1 - u[m]
    P = A[i] + u[:, :1] * (B[i] - A[i]) + u[:, 1:] * (C[i] - A[i])
    n0 = len(F)
    for celda in (0.25, 0.4, 0.6, 0.9, 1.3, 1.9, 2.8):
        v2, f2 = decimar(V, F, celda)
        if len(f2) <= objetivo: break
    return v2.astype(np.float32), f2.astype(np.int32), P.astype(np.float32), n0, celda

salida = {}
total = 0
for cuerpo, nombres in cuerpos.items():
    for nm in nombres:
        o = obj[nm]
        objetivo = 700 if nm.startswith("rodamiento") else 5000 if nm.startswith(("NNR", "Ensamblaje1v1")) else 3500
        v, f, p, n0, celda = malla(o.Shape, objetivo)
        salida["%s|%s|V" % (cuerpo, nm)] = v; salida["%s|%s|F" % (cuerpo, nm)] = f; salida["%s|%s|P" % (cuerpo, nm)] = p
        total += len(f)
        print("%-9s %-40s %6d -> %5d triángulos (celda %.2f)" % (cuerpo, nm, n0, len(f), celda), flush=True)
np.savez_compressed(AQUI + "/datos/malla_cuerpos.npz", **salida)
print("TOTAL triángulos:", total, " archivo:", os.path.getsize(AQUI + "/datos/malla_cuerpos.npz") // 1024, "KB")
