# Malla de alta fidelidad para el render por GPU -> datos/malla_gpu.npz   (ejecutar con:  FreeCADCmd herramientas/exportar_malla_gpu.py)
# Teselado fino del CAD (desviación 0.05-0.2 mm según el tamaño de la pieza) + normales suaves con ángulo de arista (las superficies curvas se ven lisas
# y las aristas vivas se conservan). Mismo agrupado por cuerpo cinemático que exportar_malla_cad.py.
import json, os, time
import numpy as np
import FreeCAD as App

AQUI = os.environ.get("SPM_DIR", "/home/arlo/Documents/simulador_spm")
CAD = os.environ.get("SPM_CAD", "/home/arlo/Documents/FreeCAD/spm_trabajo/Ensamblaje2510_trabajo.FCStd")
CREASE = float(os.environ.get("SPM_CREASE_DEG", "38"))
G = json.load(open(AQUI + "/datos/geometria.json"))
doc = App.openDocument(CAD)
obj = {o.Label: o for o in doc.Objects if o.TypeId == "Part::Feature" and not o.Name.startswith("ver_")}

cuerpos = {"fijo": list(G["fijas"]), "plataforma": [p["plato"] for p in G["patas"]] + list(G["plataforma_extra"])}
for p in G["patas"]:
    k = p["nnr"]
    cuerpos["nnr%d" % k] = ["NNR%d" % k, p["brasop"]]
    cuerpos["brazov%d" % k] = [p["brazov"], p["rodamiento_brasop"], p["rodamiento_plato"]]


def normales_suaves(P, F, crease_deg):
    """Normales por esquina: promedio ponderado por área de las caras vecinas (vértices soldados por posición) cuyo ángulo con la cara es < crease_deg."""
    m = len(F)
    _, inv = np.unique(np.round(P / 2e-3).astype(np.int64), axis=0, return_inverse=True)
    vid = inv.ravel()[F]                                              # (m,3) ids soldados
    A, B, C = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    fn = np.cross(B - A, C - A); a2 = np.linalg.norm(fn, axis=1); a2[a2 == 0] = 1; fn /= a2[:, None]
    cv = vid.reshape(-1); cf = np.repeat(np.arange(m), 3)
    orden = np.argsort(cv, kind="stable"); cvs, cfs = cv[orden], cf[orden]
    ini = np.searchsorted(cvs, cvs, "left"); fin = np.searchsorted(cvs, cvs, "right"); val = fin - ini
    acc = np.zeros((len(cvs), 3)); thr = np.cos(np.radians(crease_deg))
    for k in range(int(val.max())):
        sel = np.nonzero(val > k)[0]                                  # solo las esquinas cuyo vértice tiene más de k caras
        g = cfs[ini[sel] + k]; d = (fn[g] * fn[cfs[sel]]).sum(1); ok = d > thr
        acc[sel[ok]] += fn[g[ok]] * a2[g[ok]][:, None]
    nrm = np.zeros_like(acc); nrm[orden] = acc                         # de vuelta al orden de esquinas
    ln = np.linalg.norm(nrm, axis=1); mal = ln < 1e-12
    nrm[mal] = fn[cf[mal]]; ln[mal] = 1
    return (nrm / ln[:, None]).reshape(m, 3, 3), vid


def malla(shape):
    bb = shape.BoundBox; diag = bb.DiagonalLength
    tol = float(np.clip(diag * 0.0007, 0.04, 0.2))
    vs, tr = shape.tessellate(tol)
    P = np.array([[v.x, v.y, v.z] for v in vs]); F = np.array(tr, dtype=np.int64)
    nrm, vid = normales_suaves(P, F, CREASE)
    q = np.clip(np.round(nrm * 127), -127, 127).astype(np.int64)      # normales cuantizadas a 8 bits
    pack = ((q[..., 0] + 127) << 16) | ((q[..., 1] + 127) << 8) | (q[..., 2] + 127)
    clave = (vid.astype(np.int64) << 24) | pack                        # un vértice final = (posición soldada, normal cuantizada)
    uniq, first, inv = np.unique(clave.reshape(-1), return_index=True, return_inverse=True)
    V = P[F.reshape(-1)][first]; N = q.reshape(-1, 3)[first].astype(np.int8)
    return V.astype(np.float32), N, inv.astype(np.uint32).reshape(-1, 3), tol, len(F)


out = {}; tot_t = tot_v = 0; t0 = time.time()
for cuerpo, nombres in cuerpos.items():
    for nm in nombres:
        V, N, I, tol, nt = malla(obj[nm].Shape)
        out["%s|%s|V" % (cuerpo, nm)] = V; out["%s|%s|N" % (cuerpo, nm)] = N; out["%s|%s|I" % (cuerpo, nm)] = I
        tot_t += nt; tot_v += len(V)
        print("%-9s %-40s tol %.2f  %7d triángulos  %7d vértices  (%.0f s)" % (cuerpo, nm, tol, nt, len(V), time.time() - t0), flush=True)
np.savez_compressed(AQUI + "/datos/malla_gpu.npz", **out)
print("TOTAL: %d triángulos, %d vértices, archivo %.1f MB" % (tot_t, tot_v, os.path.getsize(AQUI + "/datos/malla_gpu.npz") / 1e6), flush=True)
