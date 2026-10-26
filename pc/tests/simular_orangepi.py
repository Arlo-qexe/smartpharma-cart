"""
Orange Pi SIMULADA con fotos ya tomadas: recorre el mismo ciclo que `orange-pi/src/main.py`
(espera la orden `iniciar` del botón del panel, consulta `estado_ciclo` antes de cada caja, envía el
lote de 5 fotos, y si la PC responde ERROR_REVISION_MANUAL espera la decisión del regente) pero sin
cámara, UART ni mDNS. Sirve para probar el panel, el inventario y el reconocimiento (PC_RECONOCIMIENTO=ia)
sin hardware. NO es parte del contrato.

Fotos: una carpeta o un .zip con archivos `cajaNNN_caraK.jpg` (por defecto el conjunto de
entrenamiento de IA/dataset_ia/roboflow/roboflow_v1.zip). Cada caja = un objeto con sus caras en orden.

Uso (con el servidor corriendo en otra terminal):
    python3 tests/simular_orangepi.py                       # espera el botón "Iniciar recorrido"
    python3 tests/simular_orangepi.py --sin-orden           # arranca de inmediato (no usa el botón)
    python3 tests/simular_orangepi.py --cajas 004 005 010   # solo esas cajas
    python3 tests/simular_orangepi.py --repetir             # vuelve a empezar al terminar las fotos
    python3 tests/simular_orangepi.py --lote-vacio-cada 4   # cada 4.ª caja simula un fallo de captura
"""
import argparse
import re
import socket
import struct
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_orangepi_client import (  # noqa: E402
    _leer_respuesta, consultar_decision, consultar_estado_ciclo, consultar_orden_ciclo,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    CICLO_DETENER, CLAVE_CICLO_JSON, CLAVE_DESTINO_JSON, CLAVE_ESTADO_DECISION_JSON,
    CLAVE_ORDEN_CICLO_JSON, CLAVE_RESULTADO_JSON, ESTADO_DECISION_NINGUNA, ESTADO_DECISION_RESUELTA,
    FRAMING_STRUCT_FORMAT, INTERVALO_CONSULTA_DECISION_S, ORDEN_CICLO_INICIAR,
    RESULTADO_ERROR_REVISION_MANUAL, TCP_PUERTO_DEFECTO,
)

FOTOS_DEFECTO = Path(__file__).resolve().parents[2] / "IA" / "dataset_ia" / "roboflow" / "roboflow_v1.zip"
_PATRON = re.compile(r"(caja\d+)_cara(\d+)(?:_jpg\.rf\.\w+)?\.jpe?g$", re.IGNORECASE)


def cargar_cajas(origen: Path) -> dict:
    """{'caja004': [jpeg cara1, ..., jpeg cara5]} desde una carpeta o un .zip."""
    cajas = defaultdict(dict)
    if origen.is_file() and origen.suffix.lower() == ".zip":
        with zipfile.ZipFile(origen) as z:
            for nombre in sorted(z.namelist()):     # Roboflow repite caras (aumentos): se usa una por cara
                m = _PATRON.search(nombre)
                if m:
                    cajas[m.group(1).lower()].setdefault(int(m.group(2)), z.read(nombre))
    else:
        for f in origen.rglob("*"):
            m = _PATRON.search(f.name)
            if m:
                cajas[m.group(1).lower()][int(m.group(2))] = f.read_bytes()
    return {c: [caras[k] for k in sorted(caras)] for c, caras in sorted(cajas.items())}


def enviar_lote(imagenes, ip, puerto=TCP_PUERTO_DEFECTO):
    with socket.create_connection((ip, puerto), timeout=30) as s:
        s.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(imagenes)))
        for im in imagenes:
            s.sendall(struct.pack(FRAMING_STRUCT_FORMAT, len(im)) + im)
        return _leer_respuesta(s)[CLAVE_RESULTADO_JSON]


def esperar(consulta, clave, valor, ip, texto):
    avisado = False
    while True:
        try:
            if consulta(ip)[clave] == valor:
                return
        except OSError:
            pass
        if not avisado:
            print(texto, flush=True)
            avisado = True
        time.sleep(INTERVALO_CONSULTA_DECISION_S)


def esperar_regente(ip):
    """Como la Orange Pi real: consulta cada 2 s hasta que el regente resuelve la alarma."""
    print("    ERROR_REVISION_MANUAL: esperando la decisión del regente en el panel…", flush=True)
    while True:
        try:
            r = consultar_decision(ip)
        except OSError:
            r = {}
        estado = r.get(CLAVE_ESTADO_DECISION_JSON)
        if estado == ESTADO_DECISION_RESUELTA:
            return r[CLAVE_DESTINO_JSON]
        if estado == ESTADO_DECISION_NINGUNA:
            return None
        time.sleep(INTERVALO_CONSULTA_DECISION_S)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default="127.0.0.1", help="IP de la PC (por defecto, esta misma)")
    p.add_argument("--fotos", type=Path, default=FOTOS_DEFECTO, help="carpeta o .zip con cajaNNN_caraK.jpg")
    p.add_argument("--cajas", nargs="*", help="números de caja a enviar (p. ej. 004 005); por defecto todas")
    p.add_argument("--sin-orden", action="store_true", help="no esperar el botón 'Iniciar recorrido'")
    p.add_argument("--repetir", action="store_true", help="volver a empezar al terminar las cajas")
    p.add_argument("--lote-vacio-cada", type=int, default=0, metavar="N",
                   help="cada N.ª caja envía un lote vacío (simula un fallo de captura)")
    p.add_argument("--pausa", type=float, default=2.0, help="segundos entre cajas (movimiento del carrito)")
    a = p.parse_args()

    cajas = cargar_cajas(a.fotos)
    if a.cajas:
        quiere = {f"caja{int(c):03d}" for c in a.cajas}
        cajas = {k: v for k, v in cajas.items() if k in quiere}
    if not cajas:
        sys.exit(f"No encontré fotos cajaNNN_caraK.jpg en {a.fotos}")
    print(f"Orange Pi simulada: {len(cajas)} cajas de {a.fotos.name} → PC {a.ip}", flush=True)

    while True:
        if not a.sin_orden:
            esperar(consultar_orden_ciclo, CLAVE_ORDEN_CICLO_JSON, ORDEN_CICLO_INICIAR, a.ip,
                    "Esperando que el regente pulse «Iniciar recorrido» en el panel…")
        print("Recorrido iniciado.", flush=True)
        detenido = False
        n = 0
        for nombre, fotos in list(cajas.items()) * (10**6 if a.repetir else 1):
            if not a.sin_orden:
                try:
                    if consultar_estado_ciclo(a.ip)[CLAVE_CICLO_JSON] == CICLO_DETENER:
                        detenido = True
                        break
                except OSError:
                    pass                      # sin PC la Orange Pi real continúa (4.8)
            n += 1
            vacio = a.lote_vacio_cada and n % a.lote_vacio_cada == 0
            t0 = time.perf_counter()
            try:
                resultado = enviar_lote([] if vacio else fotos, a.ip)
            except OSError as e:
                print(f"{nombre}: no pude hablar con la PC ({e}); reintento en 5 s", flush=True)
                time.sleep(5)
                continue
            print(f"[{n}] {nombre}{' (lote vacío)' if vacio else ''}: {resultado} "
                  f"({time.perf_counter() - t0:.1f} s)", flush=True)
            if resultado == RESULTADO_ERROR_REVISION_MANUAL:
                destino = esperar_regente(a.ip)
                print(f"    destino final: {destino or 'ninguno (la PC perdió el estado)'}", flush=True)
            time.sleep(a.pausa)
        if a.sin_orden or not detenido:
            print("Fin de las cajas.")
            return
        print("El regente detuvo el recorrido; espero una orden nueva.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido.")
