"""
Consola de usuarios de la PC: crear el PRIMER administrador y recuperar el acceso de un
administrador que olvidó su clave. Se usa desde la propia PC (quien tiene acceso al equipo):

    bash scripts/usuarios.sh crear-admin [--nombre "Ana Pérez"] [--clave-env VARIABLE]
    bash scripts/usuarios.sh listar
    bash scripts/usuarios.sh restablecer-clave "Ana Pérez" [--clave-env VARIABLE]

La clave se pide por teclado (sin mostrarla). Sin terminal interactiva (p. ej. un script) puede
leerse de una variable de entorno con --clave-env. Nada de esto pasa por el panel; cada acción
queda en el registro de eventos con el responsable "consola local".
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

from auditoria import Auditoria, ErrorRegistro, ruta_db_desde_entorno
from usuarios import (
    LARGO_MIN_CLAVE,
    RESPONSABLE_CONSOLA,
    ROL_ADMINISTRADOR,
    ErrorUsuarios,
    GestorUsuarios,
)

MENSAJES = {
    "nombre_invalido": "El nombre debe tener entre 2 y 60 caracteres, sin caracteres de control.",
    "nombre_duplicado": "Ya existe un usuario con ese nombre (los nombres no se reutilizan).",
    "clave_invalida": f"La clave debe tener al menos {LARGO_MIN_CLAVE} caracteres y no empezar ni terminar con espacios.",
    "usuario_inexistente": "No existe un administrador con ese nombre.",
}


def _clave(args) -> str:
    if args.clave_env:
        valor = os.environ.get(args.clave_env)
        if not valor:
            sys.exit(f"La variable de entorno {args.clave_env} no está definida o está vacía.")
        return valor
    if not sys.stdin.isatty():
        sys.exit("No hay terminal interactiva: usa --clave-env NOMBRE_DE_VARIABLE o ejecútalo en una terminal.")
    clave = getpass.getpass(f"Clave (mínimo {LARGO_MIN_CLAVE} caracteres): ")
    if clave != getpass.getpass("Repite la clave: "):
        sys.exit("Las claves no coinciden.")
    return clave


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Usuarios de la PC (consola local).")
    sub = ap.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("crear-admin", help="crea un administrador")
    p.add_argument("--nombre")
    p.add_argument("--clave-env", metavar="VARIABLE")
    sub.add_parser("listar", help="lista los usuarios")
    p = sub.add_parser("restablecer-clave", help="nueva clave para un administrador")
    p.add_argument("nombre")
    p.add_argument("--clave-env", metavar="VARIABLE")
    args = ap.parse_args(argv)

    try:
        aud = Auditoria(ruta_db_desde_entorno())
        gestor = GestorUsuarios(aud)
        if args.orden == "listar":
            usuarios = gestor.listar()
            if not usuarios:
                print("No hay usuarios todavía. Crea el primer administrador con: crear-admin")
            for u in usuarios:
                estado = "eliminado" if u["eliminado"] else ("activo" if u["activo"] else "desactivado")
                alta = datetime.fromtimestamp(u["creado_ms"] / 1000).strftime("%d/%m/%Y %H:%M")
                print(f"#{u['id']:<3} {u['nombre']:<30} {u['rol']:<14} {estado:<11} alta {alta} por {u['creado_por']}")
        elif args.orden == "crear-admin":
            nombre = args.nombre or input("Nombre del administrador: ")
            u, _ = gestor.crear_usuario(RESPONSABLE_CONSOLA, nombre, ROL_ADMINISTRADOR, _clave(args))
            print(f"Administrador creado: {u['nombre']} (#{u['id']}). Ya puede iniciar sesión en el panel.")
        else:
            gestor.restablecer_clave(args.nombre, _clave(args))
            print(f"Clave de {args.nombre} restablecida. Sus sesiones abiertas se cerraron.")
    except ErrorUsuarios as e:
        print("Error:", MENSAJES.get(e.codigo, e.codigo), file=sys.stderr)
        return 1
    except ErrorRegistro as e:
        print("Error con la base de datos:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
