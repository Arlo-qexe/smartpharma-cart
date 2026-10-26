"""Pruebas de sesiones y permisos en el panel web (cookie, 401/403, administración de usuarios,
atribución del responsable). Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import usuarios as modulo_usuarios  # noqa: E402
from auditoria import Auditoria  # noqa: E402
from estado_panel import EstadoPanel  # noqa: E402
from panel_web import iniciar_panel  # noqa: E402
from usuarios import FALLOS_ANTES_DE_BLOQUEO, ROL_ADMINISTRADOR, ROL_AUXILIAR, GestorUsuarios  # noqa: E402

CLAVE = "clave-larga-123"


class Cliente:
    """Un navegador mínimo: guarda la cookie de sesión que le da el servidor."""

    def __init__(self, base):
        self.base = base
        self.cookie = None

    def _pedir(self, metodo, ruta, cuerpo=None, cabeceras=None):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else (b"" if metodo == "POST" else None)
        req = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        for k, v in (cabeceras or {}).items():
            req.add_header(k, v)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                estado, cab, crudo = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            estado, cab, crudo = e.code, e.headers, e.read()
        for valor in cab.get_all("Set-Cookie") or []:
            nombre_valor = valor.split(";")[0]
            self.cookie = None if nombre_valor.endswith("=") else nombre_valor
        return estado, (json.loads(crudo) if crudo else {}), cab

    def get(self, ruta):
        e, c, _ = self._pedir("GET", ruta)
        return e, c

    def post(self, ruta, cuerpo=None, cabeceras=None):
        e, c, _ = self._pedir("POST", ruta, cuerpo, cabeceras)
        return e, c

    def post_con_cabeceras(self, ruta, cuerpo=None):
        return self._pedir("POST", ruta, cuerpo)


@pytest.fixture(autouse=True)
def hash_rapido(monkeypatch):
    monkeypatch.setattr(modulo_usuarios, "ITERACIONES_HASH", 1_000)


@pytest.fixture
def reloj():
    return [1_000_000.0]


@pytest.fixture
def entorno(reloj):
    aud = Auditoria(":memory:")
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    estado.registrar_arranque(base=":memory:")
    g = GestorUsuarios(aud, reloj=lambda: reloj[0], inactividad_s=600)
    g.crear_usuario("consola local", "Root Admin", ROL_ADMINISTRADOR, CLAVE)
    servidor = iniciar_panel(estado, "127.0.0.1", 0, auditoria=aud, usuarios=g)
    yield types_ns(estado=estado, aud=aud, g=g, base=f"http://127.0.0.1:{servidor.server_address[1]}")
    servidor.shutdown()
    servidor.server_close()


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def eventos(aud, tipo=None):
    todos = list(reversed(aud.listar(limite=500)["eventos"]))
    return [e for e in todos if tipo is None or e["tipo"] == tipo]


def entrar_admin(c):
    return c.post("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": CLAVE})


def crear_auxiliar(c_admin, nombre="Ana Pérez"):
    estado, cuerpo = c_admin.post("/api/usuarios", {"nombre": nombre, "rol": "auxiliar"})
    assert estado == 200
    return cuerpo["usuario"], cuerpo["codigo"]


# ---------------------------------------------------------------------------
# Sin sesión: se mira, no se toca
# ---------------------------------------------------------------------------
def test_sin_sesion_se_puede_mirar_el_panel_y_el_registro(entorno):
    c = Cliente(entorno.base)
    assert c.get("/api/estado")[0] == 200
    assert c.get("/api/log")[0] == 200 and c.get("/api/log/verificar")[0] == 200
    assert urllib.request.urlopen(entorno.base + "/", timeout=3).status == 200


def test_sin_sesion_el_estado_dice_quien_esta_en_turno_y_si_falta_administrador(entorno):
    c = Cliente(entorno.base)
    acceso = c.get("/api/estado")[1]["acceso"]
    assert acceso == {"sesion": None, "turno": None, "admin_configurado": True,
                      "inactividad_s": 600, "bloqueado_s": 0}


@pytest.mark.parametrize("ruta, cuerpo", [
    ("/api/ciclo/iniciar", {"cantidad": 3}), ("/api/ciclo/detener", None),
    ("/api/alarmas/1/resolver", {"destino": "DESCARTE"}),
])
def test_sin_sesion_ninguna_accion_pasa_y_el_estado_no_cambia(entorno, ruta, cuerpo):
    alarma = entorno.estado.registrar_alarma("fallo_captura", 1)
    entorno.estado.abrir_decision(alarma)
    c = Cliente(entorno.base)
    assert c.post(ruta, cuerpo) == (401, {"error": "sesion_requerida"})
    snap = entorno.estado.snapshot()
    assert snap["orden_ciclo"]["estado"] == "sin_orden" and snap["ciclo"]["estado"] == "inactivo"
    assert snap["alarmas"][0]["activa"] is True and entorno.estado.consultar_decision() == {"estado": "pendiente"}


def test_un_intento_sin_sesion_queda_anotado_pero_sin_inundar(entorno):
    c = Cliente(entorno.base)
    for _ in range(25):
        c.post("/api/ciclo/iniciar", {})
    ev = eventos(entorno.aud, "accion_denegada")
    assert len(ev) == 1                                     # el mismo intento no se repite en 10 s
    assert ev[0]["responsable"] == "operador sin sesión"
    assert ev[0]["detalle"]["accion"] == "iniciar_ciclo" and ev[0]["detalle"]["motivo"] == "sesion_requerida"
    c.post("/api/ciclo/detener")                            # otra acción distinta sí se anota
    assert len(eventos(entorno.aud, "accion_denegada")) == 2


def test_un_token_inventado_no_sirve(entorno):
    c = Cliente(entorno.base)
    c.cookie = "sesion=inventado"
    assert c.post("/api/ciclo/iniciar", {})[0] == 401
    c.cookie = "sesion"
    assert c.post("/api/ciclo/iniciar", {})[0] == 401
    c.cookie = "otra=cosa; sesion="
    assert c.post("/api/ciclo/iniciar", {})[0] == 401


# ---------------------------------------------------------------------------
# Inicio de sesión
# ---------------------------------------------------------------------------
def test_la_cookie_es_httponly_samesite_estricta(entorno):
    c = Cliente(entorno.base)
    estado, cuerpo, cab = c.post_con_cabeceras("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": CLAVE})
    assert estado == 200 and cuerpo["acceso"]["sesion"]["nombre"] == "Root Admin"
    cookie = cab.get("Set-Cookie")
    assert cookie.startswith("sesion=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "token" not in json.dumps(cuerpo)               # el token solo viaja en la cookie


def test_el_administrador_opera_y_queda_a_su_nombre(entorno):
    c = Cliente(entorno.base)
    entrar_admin(c)
    assert c.post("/api/ciclo/iniciar", {"cantidad": 3})[0] == 200
    ev = eventos(entorno.aud, "ciclo_inicio_ordenado")[-1]
    assert ev["responsable"] == "Root Admin" and ev["detalle"]["cantidad"] == 3
    assert c.get("/api/estado")[1]["orden_ciclo"]["ordenada_por"] == "Root Admin"


def test_credenciales_malas_responden_401_y_tras_cinco_se_bloquea(entorno):
    c = Cliente(entorno.base)
    for _ in range(FALLOS_ANTES_DE_BLOQUEO):
        assert c.post("/api/sesion/iniciar", {"codigo": "000000"}) == (401, {"error": "credenciales_invalidas"})
    estado, cuerpo = c.post("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": CLAVE})
    assert estado == 429 and cuerpo["error"] == "bloqueado" and cuerpo["espera_s"] >= 1
    assert c.get("/api/estado")[1]["acceso"]["bloqueado_s"] >= 1


@pytest.mark.parametrize("cuerpo", [{}, {"codigo": 123456}, {"codigo": ["1"]}, {"nombre": "Root Admin"}, {"clave": CLAVE}])
def test_cuerpos_de_inicio_mal_formados(entorno, cuerpo):
    assert Cliente(entorno.base).post("/api/sesion/iniciar", cuerpo)[0] == 401


def test_el_inicio_de_sesion_rechaza_un_origen_ajeno(entorno):
    c = Cliente(entorno.base)
    assert c.post("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": CLAVE},
                  {"Origin": "http://otro.sitio"})[0] == 403
    assert c.cookie is None


def test_cerrar_turno_borra_la_cookie_y_cierra_el_acceso(entorno):
    c = Cliente(entorno.base)
    entrar_admin(c)
    estado, cuerpo, cab = c.post_con_cabeceras("/api/sesion/cerrar")
    assert estado == 200 and "Max-Age=0" in cab.get("Set-Cookie")
    assert c.cookie is None
    assert c.post("/api/ciclo/iniciar", {})[0] == 401
    assert eventos(entorno.aud, "turno_cerrado")[-1]["detalle"]["motivo"] == "voluntario"


def test_el_turno_vence_por_inactividad_y_la_actividad_lo_alarga(entorno, reloj):
    c = Cliente(entorno.base)
    entrar_admin(c)
    reloj[0] += 500
    assert c.post("/api/sesion/actividad") == (200, {"ok": True})
    reloj[0] += 500
    assert c.post("/api/ciclo/iniciar", {})[0] == 200       # la actividad (y la acción) lo mantuvieron vivo
    reloj[0] += 601
    assert c.post("/api/sesion/actividad")[0] == 401
    assert c.post("/api/ciclo/detener")[0] == 401
    assert c.get("/api/estado")[1]["acceso"]["sesion"] is None


# ---------------------------------------------------------------------------
# Auxiliar en turno
# ---------------------------------------------------------------------------
def test_flujo_del_auxiliar_de_punta_a_punta(entorno):
    admin, aux = Cliente(entorno.base), Cliente(entorno.base)
    entrar_admin(admin)
    usuario, codigo = crear_auxiliar(admin)
    assert len(codigo) == 6 and usuario["rol"] == ROL_AUXILIAR
    # El administrador tiene el turno: el auxiliar no puede entrar hasta que lo cierre.
    estado, cuerpo = aux.post("/api/sesion/iniciar", {"codigo": codigo})
    assert (estado, cuerpo["error"], cuerpo["nombre"]) == (409, "turno_en_curso", "Root Admin")
    admin.post("/api/sesion/cerrar")
    assert aux.post("/api/sesion/iniciar", {"codigo": codigo})[0] == 200
    # Opera, y todo queda a su nombre.
    alarma = entorno.estado.registrar_alarma("fallo_captura", 7)
    entorno.estado.abrir_decision(alarma)
    assert aux.post(f"/api/alarmas/{alarma}/resolver", {"destino": "DESCARTE"})[0] == 200
    assert aux.post("/api/ciclo/iniciar", {"cantidad": 2})[0] == 200
    res = eventos(entorno.aud, "alarma_resuelta")[-1]
    assert res["responsable"] == "Ana Pérez" and res["detalle"]["destino"] == "DESCARTE"
    assert res["detalle"]["alarma_id"] == alarma and res["detalle"]["lote_id"] == 7
    assert eventos(entorno.aud, "ciclo_inicio_ordenado")[-1]["responsable"] == "Ana Pérez"
    # Y si algo falla en su sesión, quien ve el panel sabe quién está en turno.
    otro = Cliente(entorno.base)
    acceso = otro.get("/api/estado")[1]["acceso"]
    assert acceso["sesion"] is None and acceso["turno"]["nombre"] == "Ana Pérez"


def test_el_auxiliar_no_puede_administrar_usuarios(entorno):
    admin, aux = Cliente(entorno.base), Cliente(entorno.base)
    entrar_admin(admin)
    _, codigo = crear_auxiliar(admin)
    admin.post("/api/sesion/cerrar")
    aux.post("/api/sesion/iniciar", {"codigo": codigo})
    assert aux.get("/api/usuarios")[0] == 403
    assert aux.post("/api/usuarios", {"nombre": "Intruso", "rol": "administrador", "clave": CLAVE})[0] == 403
    assert aux.post("/api/usuarios/1/eliminar")[0] == 403
    assert aux.post("/api/sesion/clave", {"actual": CLAVE, "nueva": "otra-clave-larga"})[0] == 403
    den = eventos(entorno.aud, "accion_denegada")
    assert {e["detalle"]["accion"] for e in den} >= {"listar_usuarios", "administrar_usuarios"}
    assert all(e["responsable"] == "Ana Pérez" and e["detalle"]["motivo"] == "sin_permiso" for e in den)
    assert [u["nombre"] for u in entorno.g.listar()] == ["Root Admin", "Ana Pérez"]


def test_el_administrador_puede_tomar_el_control_de_un_turno_ajeno(entorno):
    admin, aux = Cliente(entorno.base), Cliente(entorno.base)
    entrar_admin(admin)
    _, codigo = crear_auxiliar(admin)
    admin.post("/api/sesion/cerrar")
    aux.post("/api/sesion/iniciar", {"codigo": codigo})
    assert entrar_admin(admin)[0] == 200                      # no hace falta que Ana cierre
    assert aux.post("/api/ciclo/iniciar", {})[0] == 401       # el turno de Ana ya no existe
    assert eventos(entorno.aud, "turno_cerrado")[-1]["detalle"]["motivo"] == "tomado_por_administrador"


# ---------------------------------------------------------------------------
# Administración de usuarios por HTTP
# ---------------------------------------------------------------------------
def test_la_lista_de_usuarios_nunca_trae_codigos_ni_hashes(entorno):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    _, codigo = crear_auxiliar(admin)
    estado, cuerpo = admin.get("/api/usuarios")
    assert estado == 200 and [u["nombre"] for u in cuerpo["usuarios"]] == ["Root Admin", "Ana Pérez"]
    texto = json.dumps(cuerpo)
    assert codigo not in texto and "hash" not in texto and "sal" not in texto


def test_un_codigo_nunca_aparece_en_el_registro_de_eventos(entorno):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    _, c1 = crear_auxiliar(admin, "Ana Pérez")
    uid = entorno.g.listar()[1]["id"]
    c2 = admin.post(f"/api/usuarios/{uid}/codigo")[1]["codigo"]
    todo = json.dumps(admin.get("/api/log?limite=500")[1])
    assert c1 not in todo and c2 not in todo and CLAVE not in todo


def test_crear_regenerar_desactivar_activar_y_eliminar(entorno):
    admin, aux = Cliente(entorno.base), Cliente(entorno.base)
    entrar_admin(admin)
    usuario, viejo = crear_auxiliar(admin)
    uid = usuario["id"]
    nuevo = admin.post(f"/api/usuarios/{uid}/codigo")[1]["codigo"]
    assert nuevo != viejo
    admin.post("/api/sesion/cerrar")
    assert aux.post("/api/sesion/iniciar", {"codigo": viejo})[0] == 401          # el código viejo ya no sirve
    assert aux.post("/api/sesion/iniciar", {"codigo": nuevo})[0] == 200
    aux.post("/api/sesion/cerrar")
    entrar_admin(admin)
    assert admin.post(f"/api/usuarios/{uid}/desactivar")[1]["usuario"]["activo"] is False
    assert aux.post("/api/sesion/iniciar", {"codigo": nuevo})[0] == 401
    assert admin.post(f"/api/usuarios/{uid}/activar")[1]["usuario"]["activo"] is True
    assert admin.post(f"/api/usuarios/{uid}/eliminar")[1]["usuario"]["eliminado"] is True
    assert aux.post("/api/sesion/iniciar", {"codigo": nuevo})[0] == 401
    assert admin.post(f"/api/usuarios/{uid}/eliminar")[0] == 404                   # ya no existe "vivo"
    tipos = [e["tipo"] for e in eventos(entorno.aud) if e["tipo"].startswith("usuario_")]
    assert tipos == ["usuario_creado", "usuario_creado", "usuario_codigo_regenerado",
                     "usuario_desactivado", "usuario_activado", "usuario_eliminado"]
    assert {e["responsable"] for e in eventos(entorno.aud) if e["tipo"].startswith("usuario_") and
            e["detalle"].get("nombre") == "Ana Pérez"} == {"Root Admin"}


@pytest.mark.parametrize("cuerpo, estado, error", [
    ({"nombre": "root admin", "rol": "auxiliar"}, 409, "nombre_duplicado"),
    ({"nombre": "", "rol": "auxiliar"}, 400, "nombre_invalido"),
    ({"nombre": "Otro", "rol": "jefe"}, 400, "rol_invalido"),
    ({"nombre": "Otro", "rol": "administrador"}, 400, "clave_invalida"),
    ({"nombre": "Otro", "rol": "administrador", "clave": "corta"}, 400, "clave_invalida"),
])
def test_creacion_con_datos_invalidos(entorno, cuerpo, estado, error):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    assert admin.post("/api/usuarios", cuerpo)[0:2] == (estado, {"error": error, **({"minimo": 10} if error == "clave_invalida" else {})})


def test_no_se_puede_dejar_el_sistema_sin_administrador_por_http(entorno):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    uid = entorno.g.listar()[0]["id"]
    assert admin.post(f"/api/usuarios/{uid}/desactivar")[1] == {"error": "ultimo_administrador"}
    assert admin.post(f"/api/usuarios/{uid}/eliminar")[0] == 409
    assert admin.post("/api/usuarios", {"nombre": "Segundo", "rol": "administrador", "clave": "otra-clave-larga"})[0] == 200
    assert admin.post(f"/api/usuarios/{uid}/desactivar")[0] == 200


def test_el_administrador_cambia_su_clave(entorno):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    assert admin.post("/api/sesion/clave", {"actual": "mal", "nueva": "nueva-clave-larga"})[0] == 401
    assert admin.post("/api/sesion/clave", {"actual": CLAVE, "nueva": "corta"})[0] == 400
    assert admin.post("/api/sesion/clave", {"actual": CLAVE, "nueva": "nueva-clave-larga"})[0] == 200
    admin.post("/api/sesion/cerrar")
    assert entrar_admin(admin)[0] == 401
    assert admin.post("/api/sesion/iniciar", {"nombre": "Root Admin", "clave": "nueva-clave-larga"})[0] == 200


# ---------------------------------------------------------------------------
# Asistente, recorrido ya en marcha e integridad
# ---------------------------------------------------------------------------
class AsistenteFalso:
    modo = "prueba"

    def __init__(self):
        self.confirmadas = []

    def mensaje(self, texto):
        return {"tipo": "respuesta", "texto": "hola"}

    def confirmar(self, pid):
        self.confirmadas.append(pid)
        return {"tipo": "respuesta", "texto": "hecho"}

    def cancelar(self, pid):
        return {"tipo": "respuesta", "texto": "cancelado"}

    def reiniciar(self):
        pass


def test_confirmar_una_accion_del_asistente_exige_turno_pero_chatear_no(reloj):
    aud = Auditoria(":memory:")
    estado = EstadoPanel()
    estado.conectar_auditoria(aud)
    g = GestorUsuarios(aud, reloj=lambda: reloj[0])
    g.crear_usuario("consola local", "Root Admin", ROL_ADMINISTRADOR, CLAVE)
    asistente = AsistenteFalso()
    servidor = iniciar_panel(estado, "127.0.0.1", 0, asistente=asistente, auditoria=aud, usuarios=g)
    try:
        c = Cliente(f"http://127.0.0.1:{servidor.server_address[1]}")
        assert c.post("/api/asistente/mensaje", {"texto": "hola"})[0] == 200
        assert c.post("/api/asistente/confirmar", {"id": 1}) == (401, {"error": "sesion_requerida"})
        assert asistente.confirmadas == []
        entrar_admin(c)
        assert c.post("/api/asistente/confirmar", {"id": 1})[0] == 200
        assert asistente.confirmadas == [1]
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_un_recorrido_en_marcha_sigue_aunque_se_cierre_el_turno_y_se_detiene_con_otra_sesion(entorno):
    admin, aux = Cliente(entorno.base), Cliente(entorno.base)
    entrar_admin(admin)
    _, codigo = crear_auxiliar(admin)
    admin.post("/api/sesion/cerrar")
    aux.post("/api/sesion/iniciar", {"codigo": codigo})
    aux.post("/api/ciclo/iniciar", {})
    entorno.estado.consultar_orden_ciclo()
    entorno.estado.confirmar_entrega_orden()
    assert entorno.estado.consultar_estado_ciclo() == {"ciclo": "continuar"}
    aux.post("/api/sesion/cerrar")                                         # termina su turno con el recorrido activo
    assert entorno.estado.consultar_estado_ciclo() == {"ciclo": "continuar"}
    entrar_admin(admin)
    assert admin.post("/api/ciclo/detener")[0] == 200
    entorno.estado.consultar_estado_ciclo()
    fin = eventos(entorno.aud, "ciclo_detenido")[-1]
    assert fin["responsable"] == "Root Admin" and fin["detalle"]["iniciado_por"] == "Ana Pérez"


def test_el_registro_sigue_integro_con_toda_la_actividad(entorno):
    admin = Cliente(entorno.base)
    entrar_admin(admin)
    crear_auxiliar(admin)
    Cliente(entorno.base).post("/api/ciclo/iniciar", {})
    assert entorno.aud.verificar()["ok"]
