"""Trayectorias SmartPharma del SPM: cámara (4 caras, un tramo por orden de la Orange Pi) y clasificación (TIPO_A/B/C, DESCARTE_1/2).

Todo sale de un archivo de configuración (datos/smartpharma_config.json): azimuts del entorno medidos en el CAD, tiempos, inclinación de volcado...
Los tramos se planifican en el espacio de los MOTORES (interpolación articular, perfil quíntico por tramo, parada en cada punto): es exactamente lo que
ejecuta el firmware (tabla de tramos q0 -> q1), y la validación (colisión del mapa del CAD, singularidad, topes de motor) se hace sobre ese camino.

Convención: pose = (inclinación, azimut, torsión) en grados; home = (0, 0, 0) = motores a 0. Los ángulos de motor son los del eje de salida.
"""
import copy
import csv
import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

from .geometria import RUTA_DATOS
from .rot import desde_inclinacion, a_inclinacion
from .cinematica import FueraDeEspacioDeTrabajo
from .trayectoria import PERFILES, _construir, unir

RUTA_CONFIG = RUTA_DATOS / "smartpharma_config.json"
DT = 0.02

CONFIG_DEFECTO = json.loads(RUTA_CONFIG.read_text(encoding="utf-8")) if RUTA_CONFIG.exists() else {}


def _fusion(base, extra):
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusion(out[k], v)
        else:
            out[k] = v
    return out


def cargar_config(ruta=None):
    """Configuración (dict) desde el JSON editable; lo que falte se toma de datos/smartpharma_config.json."""
    cfg = copy.deepcopy(CONFIG_DEFECTO)
    if ruta:
        cfg = _fusion(cfg, json.loads(Path(ruta).read_text(encoding="utf-8")))
    return cfg


def _wrap180(x):
    return (x + 180.0) % 360.0 - 180.0


# ------------------------------------------------------------------ geometría del entorno
def orden_slots(cfg):
    """Slots del CAD ordenados de IZQUIERDA a DERECHA viendo desde el dispensador hacia el plato.
    El observador está en el lado de la salida (azimut_salida) y mira hacia azimut_salida + 180°. Izquierda = girar +90° (antihorario visto desde arriba)
    desde esa dirección de visión: el ángulo de vista de un slot es wrap(az_slot - az_visión); positivo = a la izquierda.
    Devuelve [{tipo, slot, az_deg, ang_vista_deg}] con TIPO_A el más a la izquierda."""
    e = cfg["entorno"]; off = e["offset_azimut_base_deg"]
    vision = e["azimut_salida_deg"] + off + 180.0
    filas = []
    for nombre, az in e["slots"].items():
        az = az + off
        filas.append({"slot": nombre, "az_deg": _wrap180(az), "ang_vista_deg": _wrap180(az - vision)})
    filas.sort(key=lambda f: -f["ang_vista_deg"])
    for f, tipo in zip(filas, cfg["tipos"]["izquierda_a_derecha"]):
        f["tipo"] = tipo
    return filas


def torsiones_camara(cfg):
    """Torsión (°) de cada pose de cámara, en orden. Las caras laterales de la caja salen alineadas con la dirección de entrada (caras con normal en
    salida+0/90/180/270). Girar el plato una torsión t gira la caja t (antihorario): la cara de normal φ queda mirando a la cámara si φ + t = az_cámara.
    Se usan solo torsiones NEGATIVAS (sentido horario): el tope medido del motor 1 (-85°) impide girar en sentido positivo más de ~36°. La primera cara es la
    que pide menos giro, y las siguientes siguen girando en el mismo sentido (90° cada una)."""
    e = cfg["entorno"]; off = e["offset_azimut_base_deg"]
    cam = e["azimut_camara_deg"] + off; sal = e["azimut_salida_deg"] + off
    n = cfg["camara"]["n_caras"]
    t0 = cfg["camara"]["torsion_offset_caja_deg"]
    caras = []
    for k in range(4):
        phi = sal + 90.0 * k + 90.0                                  # normales de las caras laterales: salida+90, +180, +270, +360
        t = -((phi - cam) % 360.0) + t0
        caras.append({"normal_deg": _wrap180(phi), "torsion_deg": t})
    caras.sort(key=lambda c: -c["torsion_deg"])                       # de la que pide menos giro a la que más
    return caras[:n]


# ------------------------------------------------------------------ poses y tramos
@dataclass
class Pose:
    nombre: str
    incl: float
    az: float
    tors: float
    theta: np.ndarray = field(repr=False, default=None)
    R: np.ndarray = field(repr=False, default=None)

    def fila(self):
        return (self.incl, self.az, self.tors)


@dataclass
class Tramo:
    nombre: str
    a: Pose
    b: Pose
    t_mov: float                 # s de movimiento (ya alargado si hizo falta)
    t_hold: float                # s quieto al final (estabilización / espera)
    t_mov_nominal: float
    tray: object = field(repr=False, default=None)   # Trayectoria muestreada del movimiento (None si es solo espera)
    q0: np.ndarray = None        # grados de motor
    q1: np.ndarray = None
    metricas: dict = field(default_factory=dict)

    @property
    def duracion(self):
        return self.t_mov + self.t_hold


def hacer_pose(spm, nombre, incl, az, tors, theta_prev, tors_prev):
    """Pose con sus ángulos de NNR. La rama de la IK se elige cerca de theta_prev + (giro de torsión): así se queda en el mismo modo de montaje aunque el
    tramo gire ~100° (la rama 'más cercana a theta_prev' a secas puede saltar al otro modo de montaje)."""
    R = desde_inclinacion(*np.radians([incl, az, tors]))
    th = spm.ik(R, np.asarray(theta_prev, float) + math.radians(tors - tors_prev))
    return Pose(nombre, float(incl), float(az), float(tors), th, R)


def _holgura(lim, tr):
    if lim.mapa is None:
        return np.full(len(tr.t), np.nan)
    return np.array([lim.mapa.consultar(math.degrees(i), math.degrees(a), math.degrees(s))[0]
                     for i, a, s in zip(tr.inclinacion, tr.azimut, tr.torsion)])


def planificar_tramo(spm, trans, lim, a, b, T, perfil="quintico", t_hold=0.0, nombre=""):
    """Tramo articular a -> b (poses con theta): perfil temporal en el espacio de los motores, alargando T si los límites de motor lo exigen."""
    if a is b or np.allclose(a.theta, b.theta, atol=1e-9):
        q = np.degrees(trans.a_motor(a.theta))
        return Tramo(nombre, a, b, 0.0, t_hold, 0.0, None, q, q.copy(), {"holgura_min_mm": float(_holg_pose(lim, a)), "factor": 1.0, "vel_pico_dps": 0.0, "violaciones": 0})
    Tn = T
    for _ in range(8):
        n = max(2, int(round(T / DT)) + 1); t = np.linspace(0, T, n)
        th = a.theta + (b.theta - a.theta) * PERFILES[perfil](t / T)[:, None]
        mv = trans.a_motor(th); v = np.gradient(mv, t, axis=0); ac = np.gradient(v, t, axis=0)
        extra = max(np.abs(v).max() / lim.vel_motor_max, math.sqrt(np.abs(ac).max() / lim.acc_motor_max))
        if extra <= 1.0:
            break
        T *= extra * 1.02
    T = math.ceil(T * 100 - 1e-9) / 100.0                                # a centésimas, siempre hacia arriba (no rompe el límite)
    n = max(2, int(round(T / DT)) + 1); t = np.linspace(0, T, n)
    th = a.theta + (b.theta - a.theta) * PERFILES[perfil](t / T)[:, None]
    tr = _construir(spm, trans, lim, t, th, R0=a.R)
    h = _holgura(lim, tr)
    sig = [spm.indice_singularidad(tr.R[i], tr.theta[i]) for i in range(0, len(t), 2)]
    met = {"holgura_min_mm": float(np.nanmin(h)) if np.isfinite(h).any() else float("nan"), "violaciones": len(tr.violaciones),
           "primera_violacion": tr.violaciones[0][1] if tr.violaciones else "", "sigma_min": float(min(s[0] for s in sig)), "B_min": float(min(s[1] for s in sig)),
           "vel_pico_dps": float(np.degrees(np.abs(tr.motor_vel)).max()), "incl_max_deg": float(np.degrees(tr.inclinacion).max()),
           "motor_min_deg": np.degrees(tr.motor.min(0)).round(1).tolist(), "motor_max_deg": np.degrees(tr.motor.max(0)).round(1).tolist(), "factor": T / Tn}
    return Tramo(nombre, a, b, T, t_hold, Tn, tr, np.degrees(tr.motor[0]), np.degrees(tr.motor[-1]), met)


def _holg_pose(lim, p):
    if lim.mapa is None:
        return float("nan")
    return lim.mapa.consultar(p.incl, p.az, p.tors)[0]


def _partir(spm, a, b, max_giro):
    """Poses intermedias para que ningún tramo gire más de max_giro° de torsión (así la IK sigue la rama correcta y la interfaz, que trabaja con matrices de
    rotación, ve el mismo camino)."""
    n = max(1, math.ceil(abs(b.tors - a.tors) / max_giro - 1e-9))
    out = []
    prev = a
    for k in range(1, n):
        f = k / n
        p = hacer_pose(spm, "%s~%d" % (b.nombre, k), a.incl + (b.incl - a.incl) * f, a.az if a.incl > 1e-9 else b.az, a.tors + (b.tors - a.tors) * f, prev.theta, prev.tors)
        out.append(p); prev = p
    return out


RUTA_CACHE = RUTA_DATOS / "smartpharma_cache_torsion.json"


class _Planes(dict):
    """Diccionario de planes que se calculan al pedirlos (CAM_k, CLAS_<tipo>_E<etapa>, DESC2_E<etapa>)."""
    def __init__(self, gen):
        super().__init__(); self.gen = gen

    def __missing__(self, nombre):
        g = self.gen
        if nombre.startswith("CAM_"):
            pl = g._plan_camara(int(nombre[4:]))
        elif nombre.startswith("CLAS_"):
            _, tipo1, tipo2, e = nombre.split("_")
            pl = g._plan_clas("%s_%s" % (tipo1, tipo2), int(e[1:]))
        elif nombre.startswith("DESC2_E"):
            pl = g._plan_desc2(int(nombre[7:]))
        else:
            raise KeyError(nombre)
        self[nombre] = pl
        return pl


@dataclass
class Plan:
    nombre: str
    descripcion: str
    inicio: str                      # pose de partida (home, cara1..4)
    tramos: list = field(default_factory=list)

    @property
    def duracion(self):
        return sum(t.duracion for t in self.tramos)

    def filas_interfaz(self):
        """Puntos de paso [incl, az, tors, T, espera] para la pestaña Trayectoria (modo articular con paradas) y theta inicial."""
        p0 = self.tramos[0].a
        filas = [[p0.incl, p0.az, p0.tors, 0.0, 0.0]]
        for t in self.tramos:
            if t.t_mov > 0:
                filas.append([t.b.incl, t.b.az, t.b.tors, t.t_mov, t.t_hold])
            else:
                filas[-1][4] += t.t_hold
        return filas, p0.theta.copy()


class Generador:
    """Calcula todas las poses, tramos y planes a partir de la configuración."""

    def __init__(self, spm, trans, lim, cfg=None):
        self.cfg = cfg or cargar_config()
        self.spm, self.trans = spm, trans
        c = self.cfg
        self.lim = dataclasses.replace(lim, vel_motor_max=math.radians(c["limites"]["vel_motor_max_dps"]), holgura_min_mm=c["limites"]["holgura_min_mm"])
        self.perfil = c["perfil"]
        self.t_mov = c["tiempos"]["t_mov_s"]; self.t_estab = c["tiempos"]["t_estab_s"]
        self.max_giro = c["limites"]["max_giro_por_tramo_deg"]
        self.slots = orden_slots(c)
        self.caras = torsiones_camara(c)
        self.poses_cam = self._poses_camara()
        self.torsion_volcado = {}
        self.planes = _Planes(self)
        self._cache = self._leer_cache()

    def todos(self):
        """Construye (si falta) todos los planes y los devuelve."""
        n = len(self.poses_cam) - 1
        for k in range(1, n + 1):
            self.planes["CAM_%d" % k]
        for s in self.slots:
            for e in range(n + 1):
                self.planes["CLAS_%s_E%d" % (s["tipo"], e)]
        for e in range(n + 1):
            self.planes["DESC2_E%d" % e]
        return self.planes

    def _huella_torsion(self):
        c = self.cfg
        return hashlib.sha1(json.dumps([c["entorno"], c["camara"], c["clasificacion"], c["tiempos"], c["limites"], c["perfil"]], sort_keys=True).encode()).hexdigest()[:12]

    def _leer_cache(self):
        try:
            d = json.loads(RUTA_CACHE.read_text(encoding="utf-8"))
            return d["torsion"] if d.get("huella") == self._huella_torsion() else {}
        except (OSError, ValueError, KeyError):
            return {}

    def guardar_cache(self):
        RUTA_CACHE.write_text(json.dumps({"_nota": "Cache de la torsión de volcado elegida en modo auto (se invalida sola si cambia la configuración). Generada por el generador.",
                                          "huella": self._huella_torsion(), "torsion": self.torsion_volcado}, indent=1), encoding="utf-8")

    # ---- cámara
    def _poses_camara(self):
        c = self.cfg["camara"]
        home = hacer_pose(self.spm, "home", 0, 0, 0, np.zeros(3), 0.0)
        if np.abs(home.theta).max() > 1e-9:
            raise ValueError("home no da motores a 0")
        poses = [home]
        for k, cara in enumerate(self.caras, 1):
            incl = c["inclinacion_deg"]
            az = (self.cfg["entorno"]["azimut_camara_deg"] + self.cfg["entorno"]["offset_azimut_base_deg"] + 180.0) if incl else 0.0   # se inclina alejándose de la cámara
            p = hacer_pose(self.spm, "cara%d" % k, incl, az, cara["torsion_deg"], poses[-1].theta, poses[-1].tors)
            p.normal_caja_deg = cara["normal_deg"]
            poses.append(p)
        return poses

    def _tramo(self, a, b, hold=None, nombre=""):
        return planificar_tramo(self.spm, self.trans, self.lim, a, b, self.t_mov, self.perfil, self.t_estab if hold is None else hold, nombre)

    def _plan_camara(self, k):
        a, b = self.poses_cam[k - 1], self.poses_cam[k]
        return Plan("CAM_%d" % k, "Cámara: cara %d (%s -> %s)" % (k, a.nombre, b.nombre), a.nombre, [self._tramo(a, b, nombre="%s->%s" % (a.nombre, b.nombre))])

    # ---- clasificación
    def _torsion_volcado(self, tipo):
        v = self.cfg["clasificacion"]["torsion_volcado_deg"]
        if isinstance(v, dict) and v.get(tipo) is not None:
            return float(v[tipo])
        if isinstance(v, (int, float)):
            return float(v)
        if tipo not in self._cache:
            self._cache[tipo] = self._auto_torsion(tipo)
        return self._cache[tipo]

    def _construir_clas(self, slot, tors_v, etapa):
        """Plan de clasificación del slot desde la pose de cámara `etapa` (0 = home ... 4 = cara 4): aproximación, volcado, espera y home."""
        c = self.cfg["clasificacion"]; t = self.cfg["tiempos"]
        p0 = self.poses_cam[etapa]
        f = c["reparto_giro_aproximacion"]
        tors_a = p0.tors + (tors_v - p0.tors) * f
        A = hacer_pose(self.spm, "aprox_%s" % slot["tipo"], c["inclinacion_aproximacion_deg"], slot["az_deg"], tors_a, p0.theta, p0.tors)
        V = hacer_pose(self.spm, "volcado_%s" % slot["tipo"], c["inclinacion_volcado_deg"], slot["az_deg"], tors_v, A.theta, A.tors)
        H = hacer_pose(self.spm, "home", 0, 0, 0, V.theta, V.tors)
        if np.abs(H.theta).max() > 1e-6:
            raise ValueError("el retorno a home no cae en motores = 0 (%s)" % np.degrees(H.theta))
        H.theta = np.zeros(3)
        tr = [self._tramo(p0, A, nombre="aproximacion"), self._tramo(A, V, nombre="volcado"),
              planificar_tramo(self.spm, self.trans, self.lim, V, V, 0, self.perfil, t["t_mantener_volcado_s"], "mantener"), self._tramo(V, H, nombre="home")]
        return Plan("CLAS_%s_E%d" % (slot["tipo"], etapa), "Clasificar %s desde %s" % (slot["tipo"], p0.nombre), p0.nombre, tr)

    def _auto_torsion(self, tipo):
        """Torsión de volcado que maximiza la holgura mínima del plan desde la cara 4 (rejilla de 10° entre -80° y +30°, sin violaciones)."""
        slot = next(s for s in self.slots if s["tipo"] == tipo)
        mejor = None
        for tv in np.arange(-80.0, 30.1, 10.0):
            try:
                pl = self._construir_clas(slot, float(tv), len(self.poses_cam) - 1)
            except (FueraDeEspacioDeTrabajo, ValueError):
                continue
            if sum(t.metricas.get("violaciones", 0) for t in pl.tramos):
                continue
            h = min(min(t.metricas["holgura_min_mm"] for t in pl.tramos), 8.0)           # más de 8 mm ya no se premia
            puntuacion = (round(h), -pl.duracion)                      # holgura (a 1 mm, tope 8) y, a igualdad, el plan más corto
            if mejor is None or puntuacion > mejor[0]:
                mejor = (puntuacion, float(tv))
        if mejor is None:
            raise ValueError("ninguna torsión de volcado válida para %s (revisa inclinación de volcado/aproximación)" % tipo)
        return mejor[1]

    def _plan_clas(self, tipo, e):
        slot = next(s for s in self.slots if s["tipo"] == tipo)
        tv = self._torsion_volcado(tipo); self.torsion_volcado[tipo] = tv
        return self._construir_clas(slot, tv, e)

    def _plan_desc2(self, e):
        esp = self.cfg["tiempos"]["espera_descarte2_s"]
        home = self.poses_cam[0]
        p0 = self.poses_cam[e]
        tr = [planificar_tramo(self.spm, self.trans, self.lim, p0, p0, 0, self.perfil, esp, "espera")]
        prev = p0
        for p in (_partir(self.spm, p0, home, self.max_giro) + [home]) if e > 0 else []:
            tr.append(self._tramo(prev, p, nombre="home")); prev = p
        return Plan("DESC2_E%d" % e, "DESCARTE_2 desde %s: espera %.0f s y home" % (p0.nombre, esp), p0.nombre, tr)

    # ---- consultas
    def tipo_destino(self, destino, descarte=None):
        """Plan estándar (desde la cara 4) de un destino: TIPO_A/B/C, DESCARTE_1, DESCARTE_2 (o DESCARTE con la variante dada/por defecto)."""
        n = len(self.poses_cam) - 1
        if destino == "DESCARTE":
            destino = "DESCARTE_%d" % (descarte or self.cfg["descarte"]["variante_por_defecto"])
        if destino == "DESCARTE_1":
            destino = self.cfg["tipos"]["izquierda_a_derecha"][0]
        if destino == "DESCARTE_2":
            return self.planes["DESC2_E%d" % n]
        return self.planes["CLAS_%s_E%d" % (destino, n)]

    def ciclo_completo(self, destino="TIPO_A", descarte=None):
        """Plan encadenado: cámara 1..4 y clasificación (para la pestaña Trayectoria)."""
        n = len(self.poses_cam) - 1
        tr = [self.planes["CAM_%d" % k].tramos[0] for k in range(1, n + 1)] + list(self.tipo_destino(destino, descarte).tramos)
        return Plan("CICLO_%s" % destino, "Ciclo completo: %d caras y %s" % (n, destino), "home", tr)

    def todas_las_poses(self):
        """{nombre: Pose} de cámara + aproximación/volcado de cada slot (desde la cara 4)."""
        d = {p.nombre: p for p in self.poses_cam}
        n = len(self.poses_cam) - 1
        for s in self.slots:
            pl = self.planes["CLAS_%s_E%d" % (s["tipo"], n)]
            d[pl.tramos[0].b.nombre] = pl.tramos[0].b; d[pl.tramos[1].b.nombre] = pl.tramos[1].b
        return d


# ------------------------------------------------------------------ exportación
def _pulsos(q_deg, ppr):
    return [int(round(x / 360.0 * ppr)) for x in q_deg]


def a_dict(gen):
    cfg, ppr = gen.cfg, gen.cfg["ppr"]
    poses = {}
    for nombre, p in gen.todas_las_poses().items():
        q = np.degrees(gen.trans.a_motor(p.theta))
        sig = gen.spm.indice_singularidad(p.R, p.theta)
        poses[nombre] = {"incl_deg": round(p.incl, 3), "az_deg": round(p.az, 3), "torsion_deg": round(p.tors, 3), "motor_deg": q.round(3).tolist(),
                         "motor_pulsos": _pulsos(q, ppr), "holgura_mm": round(float(_holg_pose(gen.lim, p)), 2), "sigma_min": round(sig[0], 3), "B_min": round(sig[1], 3),
                         "normal_caja_hacia_camara_deg": round(getattr(p, "normal_caja_deg", float("nan")), 2) if hasattr(p, "normal_caja_deg") else None}
    planes = {}
    for nombre, pl in gen.todos().items():
        tr = []
        for t in pl.tramos:
            tr.append({"nombre": t.nombre, "desde": t.a.nombre, "hasta": t.b.nombre, "t_mov_s": t.t_mov, "t_hold_s": t.t_hold, "t_mov_nominal_s": t.t_mov_nominal,
                       "factor_tiempo": round(t.metricas.get("factor", 1.0), 3), "q0_deg": t.q0.round(3).tolist(), "q1_deg": t.q1.round(3).tolist(),
                       "q0_pulsos": _pulsos(t.q0, ppr), "q1_pulsos": _pulsos(t.q1, ppr),
                       "orientacion_ini": [round(x, 3) for x in t.a.fila()], "orientacion_fin": [round(x, 3) for x in t.b.fila()],
                       "holgura_min_mm": round(t.metricas.get("holgura_min_mm", float("nan")), 2), "vel_pico_dps": round(t.metricas.get("vel_pico_dps", 0.0), 1),
                       "violaciones": t.metricas.get("violaciones", 0)})
        planes[nombre] = {"descripcion": pl.descripcion, "inicio": pl.inicio, "duracion_s": round(pl.duracion, 3), "tramos": tr}
    return {"configuracion": cfg, "huella_config": hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12],
            "slots_izquierda_a_derecha": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()} for s in gen.slots],
            "torsion_volcado_deg": gen.torsion_volcado, "poses": poses, "planes": planes}


def a_csv_puntos(gen, archivo):
    ppr = gen.cfg["ppr"]
    with open(archivo, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["plan", "tramo", "nombre", "t_inicio_s", "t_fin_mov_s", "t_fin_s", "incl_deg", "az_deg", "torsion_deg", "m1_deg", "m2_deg", "m3_deg",
                    "m1_pulsos", "m2_pulsos", "m3_pulsos", "holgura_min_mm", "factor_tiempo"])
        for nombre, pl in gen.todos().items():
            t0 = 0.0
            for i, t in enumerate(pl.tramos, 1):
                w.writerow([nombre, i, t.nombre, "%.2f" % t0, "%.2f" % (t0 + t.t_mov), "%.2f" % (t0 + t.duracion)] + ["%.3f" % x for x in t.b.fila()] +
                           ["%.3f" % x for x in t.q1] + _pulsos(t.q1, ppr) + ["%.2f" % t.metricas.get("holgura_min_mm", float("nan")), "%.3f" % t.metricas.get("factor", 1.0)])
                t0 += t.duracion


def muestrear(plan, trans):
    """Trayectoria completa (con esperas) de un plan, muestreada a DT, lista para graficar o para a_csv / transmitir."""
    partes = []
    for t in plan.tramos:
        if t.tray is not None:
            partes.append(t.tray)
        if t.t_hold > 0:
            partes.append(_espera(t, trans))
    return unir(partes)


def _espera(t, trans):
    from .trayectoria import Trayectoria
    tr = t.tray
    n = max(2, int(round(t.t_hold / DT)) + 1); tt = np.linspace(0, t.t_hold, n)
    if tr is not None:
        R, th, mv = tr.R[-1], tr.theta[-1], tr.motor[-1]
        inc, az, to = tr.inclinacion[-1], tr.azimut[-1], tr.torsion[-1]
    else:
        R, th = t.b.R, t.b.theta; mv = trans.a_motor(th); inc, az, to = a_inclinacion(R)
    rep = lambda x: np.repeat(np.asarray(x)[None], n, 0)
    z = np.zeros((n, 3))
    return Trayectoria(tt, rep(R), rep(th), rep(mv), z, z.copy(), np.full(n, inc), np.full(n, az), np.full(n, to), float(t.t_hold), 1.0, [])


def a_cabecera(gen, ruta, descarte_defecto=None):
    """Cabecera C para el firmware: tramos en grados de motor + tiempos. NO editar a mano: la genera generar_trayectorias_smartpharma.py."""
    d = a_dict(gen)
    n = len(gen.poses_cam) - 1
    tipos = [s["tipo"] for s in gen.slots]
    L = []
    L.append("// GENERADO por herramientas/generar_trayectorias_smartpharma.py (huella de la configuración %s). NO editar a mano." % d["huella_config"])
    L.append("// Tramos de las trayectorias SmartPharma en GRADOS DE MOTOR (eje de salida, 0 = home) con perfil quíntico. Valores iniciales; calibrar con el mecanismo real.")
    L.append("#pragma once")
    L.append("struct SpmLeg { float q0[3]; float q1[3]; float t_mov; float t_hold; };   // t_mov = 0: solo espera t_hold en q0 (= q1)")
    L.append("struct SpmPlan { const SpmLeg *legs; unsigned char n; };")
    L.append("#define SPM_N_CARAS %d" % n)
    L.append("#define SPM_N_TIPOS %d" % len(tipos))
    L.append("#define SPM_PPR_GENERADO %d" % gen.cfg["ppr"])
    L.append("#define SPM_DESCARTE_VARIANTE_DEFECTO %d" % (descarte_defecto or gen.cfg["descarte"]["variante_por_defecto"]))
    L.append("#define SPM_DESCARTE2_ESPERA_S %.1ff" % gen.cfg["tiempos"]["espera_descarte2_s"])
    L.append("#define SPM_TOL_POSE_DEG 4.0f          // tolerancia para reconocer en qué pose está el plato (home, cara 1..N)")
    L.append("static const char *const SPM_TIPOS[SPM_N_TIPOS] = { %s };   // de izquierda a derecha viendo desde el dispensador" % ", ".join('"%s"' % t for t in tipos))
    L.append("")

    def fl(v):
        return "{%s}" % ", ".join("%.3ff" % x for x in v)

    def leg(t):
        return "{ %s, %s, %.2ff, %.2ff }" % (fl(t["q0_deg"]), fl(t["q1_deg"]), t["t_mov_s"], t["t_hold_s"])

    def emite(ident, nombre):
        pl = d["planes"][nombre]
        L.append("// %s: %s (%.2f s)" % (nombre, pl["descripcion"], pl["duracion_s"]))
        L.append("static const SpmLeg %s[] = {" % ident)
        for t in pl["tramos"]:
            L.append("    %s,   // %s" % (leg(t), t["nombre"]))
        L.append("};")

    pose_q = [d["poses"][p.nombre]["motor_deg"] for p in gen.poses_cam]
    L.append("static const float SPM_POSE_Q[SPM_N_CARAS + 1][3] = {   // 0 = home, k = cara k")
    for q in pose_q:
        L.append("    %s," % fl(q))
    L.append("};")
    L.append("")
    for k in range(1, n + 1):
        emite("SPM_CAM_%d" % k, "CAM_%d" % k)
    L.append("static const SpmPlan SPM_CAM[SPM_N_CARAS] = { %s };" % ", ".join("{ SPM_CAM_%d, 1 }" % k for k in range(1, n + 1)))
    L.append("")
    for ti, tipo in enumerate(tipos):
        for e in range(0, n + 1):
            emite("SPM_CLAS_%d_E%d" % (ti, e), "CLAS_%s_E%d" % (tipo, e))
    L.append("// SPM_CLAS[tipo][etapa]: etapa = pose de partida (0 home, 1..N cara). Lo normal es la etapa N (desde la última cara).")
    L.append("static const SpmPlan SPM_CLAS[SPM_N_TIPOS][SPM_N_CARAS + 1] = {")
    for ti in range(len(tipos)):
        L.append("    { %s }," % ", ".join("{ SPM_CLAS_%d_E%d, %d }" % (ti, e, len(d["planes"]["CLAS_%s_E%d" % (tipos[ti], e)]["tramos"])) for e in range(0, n + 1)))
    L.append("};")
    L.append("")
    for e in range(0, n + 1):
        emite("SPM_DESC2_E%d" % e, "DESC2_E%d" % e)
    L.append("static const SpmPlan SPM_DESC2[SPM_N_CARAS + 1] = { %s };" % ", ".join("{ SPM_DESC2_E%d, %d }" % (e, len(d["planes"]["DESC2_E%d" % e]["tramos"])) for e in range(0, n + 1)))
    L.append("")
    Path(ruta).write_text("\n".join(L), encoding="utf-8")
    return ruta


# ------------------------------------------------------------------ para la interfaz
def programa_interfaz(gen, plan):
    """(filas, thetas) de un plan para la pestaña Trayectoria: puntos de paso [incl, az, tors, T, espera] y los ángulos de NNR de cada punto EXPANDIDOS como los
    expande `expandir_esperas` (un punto repetido por cada espera), para planificar en modo articular con `planificar_articular(..., thetas=...)`."""
    filas = []; thetas = []
    p0 = plan.tramos[0].a
    filas.append([p0.incl, p0.az, p0.tors, 0.0, 0.0]); thetas.append(p0.theta)
    for t in plan.tramos:
        if t.t_mov > 0:
            filas.append([t.b.incl, t.b.az, t.b.tors, t.t_mov, t.t_hold]); thetas.append(t.b.theta)
        else:
            filas[-1][4] += t.t_hold
    exp = []
    for f, th in zip(filas, thetas):
        exp.append(th)
        if f[4] > 0:
            exp.append(th)
    return filas, exp


def recortar(tray, t0, t1):
    """Sub-trayectoria entre t0 y t1 (s), con el tiempo reiniciado en 0; para transmitir un tramo (paso a paso) al ESP32."""
    from .trayectoria import Trayectoria
    i0 = int(np.searchsorted(tray.t, t0 - 1e-9)); i1 = int(np.searchsorted(tray.t, t1 + 1e-9))
    sl = slice(i0, max(i1, i0 + 2))
    viol = [(i - i0, m) for i, m in tray.violaciones if sl.start <= i < sl.stop]
    t = tray.t[sl] - tray.t[sl.start]
    return Trayectoria(t, tray.R[sl], tray.theta[sl], tray.motor[sl], tray.motor_vel[sl], tray.motor_acc[sl], tray.inclinacion[sl], tray.azimut[sl], tray.torsion[sl],
                       float(t[-1]), tray.factor_tiempo, viol)


_COMPARTIDO = {}


def generador_compartido(spm, trans, lim):
    """Un Generador por (spm, configuración) para la interfaz y las pruebas (los planes se calculan al pedirlos y se reutilizan)."""
    clave = (id(spm), RUTA_CONFIG.stat().st_mtime if RUTA_CONFIG.exists() else 0)
    if clave not in _COMPARTIDO:
        _COMPARTIDO.clear()
        _COMPARTIDO[clave] = Generador(spm, trans, lim)
    return _COMPARTIDO[clave]
