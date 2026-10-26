"""Pruebas de usuarios, turnos y permisos (usuarios.py). Correr desde pc/:
    python3 -m pytest tests/
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import auditoria as modulo_auditoria  # noqa: E402
import usuarios  # noqa: E402
from auditoria import Auditoria, ErrorRegistro  # noqa: E402
from usuarios import (  # noqa: E402
    FALLOS_ANTES_DE_BLOQUEO,
    LARGO_MIN_CLAVE,
    PERMISO_ADMINISTRAR,
    PERMISO_EDITAR_INVENTARIO,
    PERMISO_OPERAR,
    ROL_ADMINISTRADOR,
    ROL_AUXILIAR,
    ErrorUsuarios,
    GestorUsuarios,
)

CLAVE = "clave-larga-123"


@pytest.fixture(autouse=True)
def hash_rapido(monkeypatch):
    monkeypatch.setattr(usuarios, "ITERACIONES_HASH", 1_000)      # las pruebas no necesitan 100 000


@pytest.fixture
def reloj():
    return [1_000_000.0]


@pytest.fixture
def aud():
    a = Auditoria(":memory:")
    yield a
    a.cerrar()


@pytest.fixture
def g(aud, reloj):
    return GestorUsuarios(aud, reloj=lambda: reloj[0], inactividad_s=600)


@pytest.fixture
def admin(g):
    g.crear_usuario("consola local", "Root Admin", ROL_ADMINISTRADOR, CLAVE)
    return "Root Admin"


def eventos(aud, tipo=None):
    todos = list(reversed(aud.listar(limite=500)["eventos"]))
    return [e for e in todos if tipo is None or e["tipo"] == tipo]


def codigo_de(g, nombre="Ana Pérez", actor="Root Admin"):
    u, codigo = g.crear_usuario(actor, nombre, ROL_AUXILIAR)
    return u, codigo


def error(fn, *a, **k) -> ErrorUsuarios:
    with pytest.raises(ErrorUsuarios) as e:
        fn(*a, **k)
    return e.value


# ---------------------------------------------------------------------------
# Creación y credenciales
# ---------------------------------------------------------------------------
def test_el_auxiliar_recibe_un_codigo_de_6_digitos_una_sola_vez(g, aud, admin):
    u, codigo = codigo_de(g)
    assert len(codigo) == 6 and codigo.isdigit()
    assert u["rol"] == ROL_AUXILIAR and u["activo"] and not u["eliminado"] and u["creado_por"] == "Root Admin"
    # El código no se guarda en claro: ni en la tabla ni en el registro de eventos.
    volcado = json.dumps(aud.leer("SELECT * FROM usuarios"), default=str) + json.dumps(aud.listar(limite=500))
    assert codigo not in volcado
    assert "hash" not in g.listar()[0] and "sal" not in g.listar()[0]


def test_el_administrador_necesita_clave_larga_y_no_se_devuelve_nada(g):
    u, secreto = g.crear_usuario("consola local", "Root", ROL_ADMINISTRADOR, CLAVE)
    assert secreto is None and u["rol"] == ROL_ADMINISTRADOR
    for mala in (None, "", "corta", "x" * (LARGO_MIN_CLAVE - 1), " espacios-al-borde ", 123):
        assert error(g.crear_usuario, "consola local", "Otro", ROL_ADMINISTRADOR, mala).codigo == "clave_invalida"


@pytest.mark.parametrize("nombre", [None, "", " ", "A", "x" * 61, "con\x00nulo", "bell\x07", "esc\x1b[0m", 5])
def test_nombre_invalido(g, nombre):
    assert error(g.crear_usuario, "consola local", nombre, ROL_AUXILIAR).codigo == "nombre_invalido"


def test_los_espacios_raros_se_normalizan_a_uno_solo(g):
    u, _ = g.crear_usuario("consola local", "  Ana \n\t Pérez  ", ROL_AUXILIAR)
    assert u["nombre"] == "Ana Pérez"


def test_rol_invalido(g):
    assert error(g.crear_usuario, "x", "Ana", "jefe").codigo == "rol_invalido"


def test_los_nombres_son_unicos_sin_distinguir_mayusculas_ni_espacios_de_mas(g, admin):
    codigo_de(g, "Ana Pérez")
    assert error(g.crear_usuario, "Root Admin", "ana  pérez", ROL_AUXILIAR).codigo == "nombre_duplicado"
    assert error(g.crear_usuario, "Root Admin", "ANA PÉREZ", ROL_AUXILIAR).codigo == "nombre_duplicado"
    assert error(g.crear_usuario, "Root Admin", "Ana Perez", ROL_AUXILIAR).codigo == "nombre_duplicado"    # sin tilde
    assert error(g.crear_usuario, "Root Admin", "aNa pÉrEz", ROL_AUXILIAR).codigo == "nombre_duplicado"
    g.crear_usuario("Root Admin", "Ana Pérez Gómez", ROL_AUXILIAR)                                        # otro nombre: sí


def test_un_nombre_eliminado_no_se_reutiliza(g, admin):
    u, _ = codigo_de(g, "Ana Pérez")
    g.eliminar("Root Admin", u["id"])
    assert error(g.crear_usuario, "Root Admin", "Ana Pérez", ROL_AUXILIAR).codigo == "nombre_duplicado"


def test_los_codigos_no_se_repiten_entre_auxiliares(g, admin):
    codigos = {codigo_de(g, f"Auxiliar {i}")[1] for i in range(40)}
    assert len(codigos) == 40


def test_crear_un_usuario_queda_en_el_registro_con_quien_lo_hizo(g, aud, admin):
    codigo_de(g, "Ana Pérez")
    ev = eventos(aud, "usuario_creado")[-1]
    assert ev["responsable"] == "Root Admin"
    assert ev["detalle"]["nombre"] == "Ana Pérez" and ev["detalle"]["rol"] == ROL_AUXILIAR


# ---------------------------------------------------------------------------
# Inicio de turno
# ---------------------------------------------------------------------------
def test_el_auxiliar_abre_su_turno_con_el_codigo(g, aud, admin):
    u, codigo = codigo_de(g)
    token, s = g.iniciar_turno_con_codigo(codigo)
    assert s.nombre == "Ana Pérez" and s.rol == ROL_AUXILIAR and token
    assert g.sesion_de(token).usuario_id == u["id"]
    ev = eventos(aud, "turno_iniciado")[-1]
    assert ev["responsable"] == "Ana Pérez" and ev["detalle"]["usuario_id"] == u["id"]


def test_el_token_no_se_guarda_en_claro(g, aud, admin):
    _, codigo = codigo_de(g)
    token, s = g.iniciar_turno_con_codigo(codigo)
    assert token not in repr(s) and token not in json.dumps(aud.listar(limite=500))


@pytest.mark.parametrize("codigo", ["000000", "12345", "1234567", "abcdef", "12 456", "", None, 123456])
def test_codigo_incorrecto_o_mal_formado(g, aud, admin, codigo):
    codigo_de(g)
    assert error(g.iniciar_turno_con_codigo, codigo).codigo == "credenciales_invalidas"
    ev = eventos(aud, "login_fallido")[-1]
    assert ev["detalle"]["via"] == "codigo" and ev["responsable"] == "sistema"
    assert str(codigo) not in json.dumps(ev["detalle"]) or codigo in ("", None)


def test_un_usuario_desactivado_o_eliminado_no_entra(g, admin):
    u, codigo = codigo_de(g)
    g.cambiar_activo("Root Admin", u["id"], False)
    assert error(g.iniciar_turno_con_codigo, codigo).codigo == "credenciales_invalidas"
    g.cambiar_activo("Root Admin", u["id"], True)
    g.iniciar_turno_con_codigo(codigo)
    g.cerrar_turno(g.iniciar_turno_con_codigo(codigo)[0])
    g.eliminar("Root Admin", u["id"])
    assert error(g.iniciar_turno_con_codigo, codigo).codigo == "credenciales_invalidas"


def test_el_administrador_entra_con_nombre_y_clave(g, admin):
    token, s = g.iniciar_sesion_administrador("root admin", CLAVE)        # el nombre no distingue mayúsculas
    assert s.rol == ROL_ADMINISTRADOR and g.sesion_de(token)


@pytest.mark.parametrize("nombre, clave", [
    ("Root Admin", "otra-clave-larga"), ("Nadie", CLAVE), (None, CLAVE), ("Root Admin", None), ("", ""),
])
def test_credenciales_de_administrador_invalidas(g, aud, admin, nombre, clave):
    assert error(g.iniciar_sesion_administrador, nombre, clave).codigo == "credenciales_invalidas"
    assert eventos(aud, "login_fallido")[-1]["detalle"]["via"] == "administrador"


def test_un_auxiliar_no_puede_entrar_como_administrador(g, admin):
    codigo_de(g, "Ana Pérez")
    assert error(g.iniciar_sesion_administrador, "Ana Pérez", "cualquier-clave-larga").codigo == "credenciales_invalidas"


# ---------------------------------------------------------------------------
# Bloqueo por intentos fallidos
# ---------------------------------------------------------------------------
def test_tras_cinco_fallos_se_bloquea_y_el_bloqueo_crece(g, aud, reloj, admin):
    _, codigo = codigo_de(g)
    for _ in range(FALLOS_ANTES_DE_BLOQUEO):
        assert error(g.iniciar_turno_con_codigo, "000000").codigo == "credenciales_invalidas"
    e = error(g.iniciar_turno_con_codigo, codigo)                 # ni el código correcto sirve bloqueado
    assert e.codigo == "bloqueado" and 1 <= e.detalle["espera_s"] <= 61
    assert eventos(aud, "login_bloqueado")[-1]["detalle"]["espera_s"] == 60
    reloj[0] += 61
    assert error(g.iniciar_turno_con_codigo, "000000").codigo == "credenciales_invalidas"   # sexto fallo
    assert eventos(aud, "login_bloqueado")[-1]["detalle"]["espera_s"] == 120                # el doble
    reloj[0] += 121
    assert g.iniciar_turno_con_codigo(codigo)[1].nombre == "Ana Pérez"                       # y se resetea al acertar
    g.cerrar_turno(g.iniciar_turno_con_codigo(codigo)[0])
    assert error(g.iniciar_turno_con_codigo, "000000").codigo == "credenciales_invalidas"
    assert eventos(aud, "login_fallido")[-1]["detalle"]["fallos_seguidos"] == 1


def test_el_bloqueo_no_inunda_el_registro(g, aud, admin):
    for _ in range(FALLOS_ANTES_DE_BLOQUEO):
        error(g.iniciar_turno_con_codigo, "000000")
    antes = aud.total()
    for _ in range(50):
        assert error(g.iniciar_turno_con_codigo, "000000").codigo == "bloqueado"
    assert aud.total() == antes


def test_el_bloqueo_tambien_protege_el_acceso_de_administrador(g, admin):
    for _ in range(FALLOS_ANTES_DE_BLOQUEO):
        error(g.iniciar_turno_con_codigo, "000000")
    assert error(g.iniciar_sesion_administrador, "Root Admin", CLAVE).codigo == "bloqueado"


# ---------------------------------------------------------------------------
# Un solo turno a la vez
# ---------------------------------------------------------------------------
def test_con_un_turno_abierto_otro_auxiliar_no_puede_entrar(g, admin):
    _, c1 = codigo_de(g, "Ana Pérez")
    _, c2 = codigo_de(g, "Luis Gómez")
    t1, _ = g.iniciar_turno_con_codigo(c1)
    e = error(g.iniciar_turno_con_codigo, c2)
    assert e.codigo == "turno_en_curso" and e.detalle["nombre"] == "Ana Pérez"
    assert g.cerrar_turno(t1)
    assert g.iniciar_turno_con_codigo(c2)[1].nombre == "Luis Gómez"


def test_cerrar_el_turno_queda_anotado(g, aud, reloj, admin):
    _, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    reloj[0] += 90
    assert g.cerrar_turno(t) and g.sesion_de(t) is None
    ev = eventos(aud, "turno_cerrado")[-1]
    assert ev["responsable"] == "Ana Pérez" and ev["detalle"]["motivo"] == "voluntario" and ev["detalle"]["duracion_s"] == 90
    assert g.cerrar_turno(t) is False                        # ya cerrado


def test_un_administrador_puede_tomar_el_control(g, aud, admin):
    _, c = codigo_de(g)
    ta, _ = g.iniciar_turno_con_codigo(c)
    tadm, s = g.iniciar_sesion_administrador("Root Admin", CLAVE)
    assert g.sesion_de(ta) is None and g.sesion_de(tadm).rol == ROL_ADMINISTRADOR
    ev = eventos(aud, "turno_cerrado")[-1]
    assert ev["responsable"] == "Root Admin" and ev["detalle"]["nombre"] == "Ana Pérez"
    assert ev["detalle"]["motivo"] == "tomado_por_administrador"


def test_la_misma_persona_que_vuelve_a_entrar_invalida_su_token_anterior(g, admin):
    _, c = codigo_de(g)
    t1, _ = g.iniciar_turno_con_codigo(c)
    t2, _ = g.iniciar_turno_con_codigo(c)
    assert g.sesion_de(t1) is None and g.sesion_de(t2)


def test_el_turno_expira_por_inactividad_y_queda_anotado(g, aud, reloj, admin):
    _, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    reloj[0] += 601
    assert g.sesion_de(t) is None
    ev = eventos(aud, "turno_cerrado")[-1]
    assert ev["responsable"] == "sistema" and ev["detalle"]["motivo"] == "expirado"
    assert len(eventos(aud, "turno_cerrado")) == 1                       # no se anota dos veces
    assert g.estado_publico(None)["turno"] is None


def test_la_actividad_mantiene_vivo_el_turno(g, reloj, admin):
    _, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    for _ in range(5):
        reloj[0] += 500
        assert g.actividad(t)
    assert g.sesion_de(t)
    reloj[0] += 601
    assert not g.actividad(t)


def test_un_turno_vencido_deja_entrar_a_otro_auxiliar(g, reloj, admin):
    _, c1 = codigo_de(g, "Ana Pérez")
    _, c2 = codigo_de(g, "Luis Gómez")
    g.iniciar_turno_con_codigo(c1)
    reloj[0] += 601
    assert g.iniciar_turno_con_codigo(c2)[1].nombre == "Luis Gómez"


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------
def test_permisos_por_rol(g, admin):
    _, c = codigo_de(g)
    ta, _ = g.iniciar_turno_con_codigo(c)
    assert g.autorizar(ta, PERMISO_OPERAR).nombre == "Ana Pérez"
    assert g.autorizar(ta, PERMISO_EDITAR_INVENTARIO)
    e = error(g.autorizar, ta, PERMISO_ADMINISTRAR)
    assert e.codigo == "sin_permiso"
    tadm, _ = g.iniciar_sesion_administrador("Root Admin", CLAVE)
    for permiso in (PERMISO_OPERAR, PERMISO_EDITAR_INVENTARIO, PERMISO_ADMINISTRAR):
        assert g.autorizar(tadm, permiso)


@pytest.mark.parametrize("token", [None, "", "inventado", "x" * 43])
def test_sin_sesion_valida_no_hay_permiso(g, admin, token):
    _, c = codigo_de(g)
    g.iniciar_turno_con_codigo(c)
    assert error(g.autorizar, token, PERMISO_OPERAR).codigo == "sesion_requerida"


def test_autorizar_cuenta_como_actividad(g, reloj, admin):
    _, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    for _ in range(4):
        reloj[0] += 500
        g.autorizar(t, PERMISO_OPERAR)
    assert g.sesion_de(t)


# ---------------------------------------------------------------------------
# Administración de usuarios
# ---------------------------------------------------------------------------
def test_regenerar_el_codigo_invalida_el_anterior_y_cierra_su_turno(g, aud, admin):
    u, viejo = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(viejo)
    nuevo = g.regenerar_codigo("Root Admin", u["id"])
    assert nuevo != viejo and len(nuevo) == 6
    assert g.sesion_de(t) is None
    assert error(g.iniciar_turno_con_codigo, viejo).codigo == "credenciales_invalidas"
    assert g.iniciar_turno_con_codigo(nuevo)[1].nombre == "Ana Pérez"
    assert eventos(aud, "usuario_codigo_regenerado")[-1]["responsable"] == "Root Admin"
    assert eventos(aud, "turno_cerrado")[-1]["detalle"]["motivo"] == "codigo_regenerado"
    assert nuevo not in json.dumps(aud.listar(limite=500))


def test_no_se_regenera_codigo_de_un_administrador_ni_de_un_inexistente(g, admin):
    assert error(g.regenerar_codigo, "x", g.listar()[0]["id"]).codigo == "solo_auxiliares"
    assert error(g.regenerar_codigo, "x", 999).codigo == "usuario_inexistente"


def test_desactivar_cierra_el_turno_y_reactivar_lo_deja_volver(g, aud, admin):
    u, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    assert g.cambiar_activo("Root Admin", u["id"], False)["activo"] is False
    assert g.sesion_de(t) is None
    assert eventos(aud, "usuario_desactivado")[-1]["detalle"]["nombre"] == "Ana Pérez"
    g.cambiar_activo("Root Admin", u["id"], True)
    assert g.iniciar_turno_con_codigo(c)[1].nombre == "Ana Pérez"
    n = len(eventos(aud, "usuario_activado"))
    g.cambiar_activo("Root Admin", u["id"], True)                          # ya estaba activo: no duplica
    assert len(eventos(aud, "usuario_activado")) == n


def test_eliminar_es_logico_y_conserva_la_fila(g, aud, admin):
    u, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    r = g.eliminar("Root Admin", u["id"])
    assert r["eliminado"] and not r["activo"] and g.sesion_de(t) is None
    assert any(x["id"] == u["id"] and x["eliminado"] for x in g.listar())
    assert eventos(aud, "usuario_eliminado")[-1]["responsable"] == "Root Admin"
    assert error(g.eliminar, "Root Admin", u["id"]).codigo == "usuario_inexistente"      # ya no está "vivo"
    assert error(g.cambiar_activo, "Root Admin", u["id"], True).codigo == "usuario_inexistente"


def test_no_se_puede_dejar_el_sistema_sin_administrador(g, admin):
    adm = g.listar()[0]["id"]
    assert error(g.cambiar_activo, "x", adm, False).codigo == "ultimo_administrador"
    assert error(g.eliminar, "x", adm).codigo == "ultimo_administrador"
    otro, _ = g.crear_usuario("Root Admin", "Segundo Admin", ROL_ADMINISTRADOR, "otra-clave-larga")
    g.eliminar("Root Admin", adm)                                         # ahora sí: queda otro
    assert error(g.cambiar_activo, "x", otro["id"], False).codigo == "ultimo_administrador"
    assert g.hay_administrador()


def test_hay_administrador(g):
    assert g.hay_administrador() is False
    g.crear_usuario("consola local", "Root", ROL_ADMINISTRADOR, CLAVE)
    assert g.hay_administrador() is True
    g.crear_usuario("Root", "Solo auxiliar", ROL_AUXILIAR)


def test_cambiar_la_propia_clave(g, aud, admin):
    adm = g.listar()[0]["id"]
    assert error(g.cambiar_clave, adm, "no-es-la-actual", "nueva-clave-larga").codigo == "credenciales_invalidas"
    assert error(g.cambiar_clave, adm, CLAVE, "corta").codigo == "clave_invalida"
    g.cambiar_clave(adm, CLAVE, "nueva-clave-larga")
    assert error(g.iniciar_sesion_administrador, "Root Admin", CLAVE).codigo == "credenciales_invalidas"
    assert g.iniciar_sesion_administrador("Root Admin", "nueva-clave-larga")[1].nombre == "Root Admin"
    ev = eventos(aud, "usuario_clave_cambiada")[-1]
    assert ev["responsable"] == "Root Admin" and "nueva-clave-larga" not in json.dumps(aud.listar(limite=500))


def test_restablecer_la_clave_desde_la_consola(g, aud, admin):
    t, _ = g.iniciar_sesion_administrador("Root Admin", CLAVE)
    for _ in range(FALLOS_ANTES_DE_BLOQUEO):
        error(g.iniciar_turno_con_codigo, "000000")
    g.restablecer_clave("Root Admin", "clave-nueva-larga")
    assert g.sesion_de(t) is None                                          # su sesión se cerró
    assert g.iniciar_sesion_administrador("Root Admin", "clave-nueva-larga")                # y el bloqueo se levantó
    ev = eventos(aud, "usuario_clave_restablecida")[-1]
    assert ev["responsable"] == "consola local"
    assert error(g.restablecer_clave, "Nadie", "clave-nueva-larga").codigo == "usuario_inexistente"
    codigo_de(g, "Ana Pérez")
    assert error(g.restablecer_clave, "Ana Pérez", "clave-nueva-larga").codigo == "usuario_inexistente"   # solo administradores


# ---------------------------------------------------------------------------
# Atomicidad, persistencia y estado público
# ---------------------------------------------------------------------------
def test_un_cambio_sin_su_evento_no_ocurre(g, aud, admin, monkeypatch):
    def roto(*a, **k):
        raise ErrorRegistro("disco lleno")
    monkeypatch.setattr(modulo_auditoria, "_insertar_evento", roto)
    with pytest.raises(ErrorRegistro):
        g.crear_usuario("Root Admin", "Ana Pérez", ROL_AUXILIAR)
    assert [u["nombre"] for u in g.listar()] == ["Root Admin"]            # el alta se deshizo


def test_el_registro_sigue_integro_tras_toda_la_actividad(g, aud, reloj, admin):
    u, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    error(g.iniciar_turno_con_codigo, "000000")
    g.regenerar_codigo("Root Admin", u["id"])
    g.cambiar_activo("Root Admin", u["id"], False)
    assert aud.verificar()["ok"]


def test_los_usuarios_sobreviven_a_un_reinicio_pero_los_turnos_no(tmp_path, reloj):
    ruta = tmp_path / "r.db"
    a = Auditoria(ruta)
    g = GestorUsuarios(a, reloj=lambda: reloj[0])
    g.crear_usuario("consola local", "Root", ROL_ADMINISTRADOR, CLAVE)
    _, c = g.crear_usuario("Root", "Ana Pérez", ROL_AUXILIAR)
    t, _ = g.iniciar_turno_con_codigo(c)
    a.cerrar()
    b = Auditoria(ruta)
    g2 = GestorUsuarios(b, reloj=lambda: reloj[0])
    assert [u["nombre"] for u in g2.listar()] == ["Root", "Ana Pérez"]
    assert g2.sesion_de(t) is None                                         # el turno vivía en RAM
    assert g2.iniciar_turno_con_codigo(c)[1].nombre == "Ana Pérez"         # el código sigue sirviendo
    assert g2.iniciar_sesion_administrador("Root", CLAVE)


def test_estado_publico(g, admin):
    assert g.estado_publico(None) == {"sesion": None, "turno": None, "admin_configurado": True,
                                      "inactividad_s": 600, "bloqueado_s": 0}
    _, c = codigo_de(g)
    t, _ = g.iniciar_turno_con_codigo(c)
    propio = g.estado_publico(t)
    assert propio["sesion"]["nombre"] == "Ana Pérez" and propio["sesion"]["permisos"] == ["editar_inventario", "operar"]
    ajeno = g.estado_publico("otro-token")
    assert ajeno["sesion"] is None and ajeno["turno"]["nombre"] == "Ana Pérez"      # se ve quién está en turno


def test_la_base_rechaza_dos_usuarios_con_el_mismo_nombre(g, aud, admin):
    with pytest.raises(sqlite3.Error):
        aud._con.execute("INSERT INTO usuarios (nombre, nombre_clave, rol, sal, hash, creado_ms, creado_por) "
                         "VALUES ('Root  Admin', 'root admin', 'auxiliar', 'a', 'b', 0, 'x')")
