import json, numpy as np, FreeCAD as App, Part
from FreeCAD import Vector as V
d = App.openDocument("/home/arlo/Documents/FreeCAD/spm_trabajo/Ensamblaje2510_trabajo.FCStd")
L = {o.Label: o for o in d.Objects if o.TypeId == "Part::Feature"}
S = lambda n: L[n].Shape
def axc(b):
    s = b.Shape.Solids[0]; a = s.PrincipalProperties["FirstAxisOfInertia"]
    return np.array([s.CenterOfMass.x, s.CenterOfMass.y, s.CenterOfMass.z]), np.array([a.x, a.y, a.z])
B1 = [axc(L["rodamiento 628ZZ (Brasop %d)" % i]) for i in (1,2,3)]
B2 = [axc(L["rodamiento 628ZZ (platosupv %d)" % i]) for i in (1,2,3)]
brasop = ["Brasop","Brasop001","Brasop002"]; plato = ["platosupv2","platosupv001","platosupv002"]; brz = ["Brazov2","Brazov001","Brazov002"]
nnr = ["NNR1","NNR2","NNR3"]
def near(shape, names):
    return [round(shape.distToShape(S(n))[0], 3) for n in names]
print("Brasop_i vs Brazov:", [near(S(b), brz) for b in brasop])
print("platosupv_i vs Brazov:", [near(S(p), brz) for p in plato])
print("Brasop_i vs NNR:", [near(S(b), nnr) for b in brasop])
print("platosupv_i vs platov3:", [round(S(p).distToShape(S("platov3"))[0],3) for p in plato])
print("platosupv_i vs Brasop:", [near(S(p), brasop) for p in plato])
print("Brazov vs Brasop(min dist):", [[round(S(z).distToShape(S(b))[0],2) for b in brasop] for z in brz])
# punto común: mínimos cuadrados de las 6 rectas
A = np.zeros((3,3)); bb = np.zeros(3)
for c, a in B1 + B2:
    a = a/np.linalg.norm(a); P = np.eye(3) - np.outer(a,a); A += P; bb += P @ c
O = np.linalg.solve(A, bb)
res = [np.linalg.norm((np.eye(3)-np.outer(a/np.linalg.norm(a),a/np.linalg.norm(a))) @ (O-c)) for c,a in B1+B2]
print("O =", O.round(3), " distancia de O a cada eje:", np.round(res,3))
print("eje central pasa por (7.9899,3.6933); O desplazado:", (O[:2]-np.array([7.9899,3.6933])).round(3))
for i,(c,a) in enumerate(B1): print("eje1", i, (a/np.linalg.norm(a)).round(4), c.round(1))
for i,(c,a) in enumerate(B2): print("eje2", i, (a/np.linalg.norm(a)).round(4), c.round(1))
json.dump({"O": O.tolist(), "a1": [ (a/np.linalg.norm(a)).tolist() for c,a in B1], "a2": [ (a/np.linalg.norm(a)).tolist() for c,a in B2],
  "legs": [ {"brasop":"Brasop","nnr":"NNR3","brazov":"Brazov002","plato":"platosupv002","rod1":1,"rod2":3},
            {"brasop":"Brasop001","nnr":"NNR2","brazov":"Brazov001","plato":"platosupv2","rod1":2,"rod2":1},
            {"brasop":"Brasop002","nnr":"NNR1","brazov":"Brazov2","plato":"platosupv001","rod1":3,"rod2":2}]},
  open("/home/arlo/Documents/FreeCAD/spm_animacion/cinem.json","w"))
print("JSON ok")
