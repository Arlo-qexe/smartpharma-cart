"""
Orquestador del ciclo completo en la Orange Pi (ver docs/arquitectura_comunicacion.md
para la descripción detallada de cada paso, y CLAUDE.md raíz para el resumen rápido).

Fallo de comunicación con la PC: ver network/recuperacion.py (sección 4.6).
Al arrancar espera la orden de inicio de la PC (sección 4.7; `--sin-orden` la omite).
Pendiente (ver shared/checklist.md): la prueba con la ESP32-S3 real (OP-16).

Flujo por objeto:
  1. activar_dispensador (con límite de reintentos)
  2. ráfaga de 5 fotos (girar_posicion / en_posicion por cada cara)
  3. envío del lote a la PC (o lote vacío si la captura falló)
  4. si falló la red: activar_alarma_local y sondear con lote vacío hasta que la
     PC responda (4.6); si respondió ERROR_REVISION_MANUAL: consultar la
     decisión del regente (sin tope)
  5. clasificar (destino automático o del regente)
  6. autorizar el siguiente objeto
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    ACCION_ACTIVAR_ALARMA_LOCAL,
    ACCION_ACTIVAR_DISPENSADOR,
    ACCION_DESACTIVAR_ALARMA_LOCAL,
    ACCION_CLASIFICAR,
    ACCION_GIRAR_POSICION,
    CARAS_POR_OBJETO,
    EVENTO_EN_POSICION,
    EVENTO_OBJETO_EN_POSICION,
    LIMITE_REINTENTOS_INTRODUCIR_OBJETO,
    PAUSA_ESTABILIZACION_MECANICA_S,
    RESULTADO_ERROR_REVISION_MANUAL,
)

from network.mdns_discovery import DescubridorPC
from network.decision_client import DecisionPerdida, esperar_decision, esperar_orden_inicio
from network.recuperacion import recuperar_comunicacion
from network.tcp_client import enviar_lote
from uart.uart_link import EnlaceUART
from capture.camera import abrir_camara, capturar_con_autoenfoque, comprimir_jpeg

import argparse
import time


def introducir_objeto(enlace: EnlaceUART) -> bool:
    """Ordena activar el dispensador (entra un objeto), con el límite de reintentos definido en
    el contrato. Devuelve True si se confirmó objeto_en_posicion."""
    for intento in range(1, LIMITE_REINTENTOS_INTRODUCIR_OBJETO + 1):
        try:
            enlace.enviar({"accion": ACCION_ACTIVAR_DISPENSADOR})
            respuesta = enlace.recibir(timeout=5.0)
        except OSError as e:  # puerto serie caído: cuenta como intento fallido
            print(f"[main] Error de UART: {e}")
            respuesta = None
        if respuesta and respuesta.get("evento") == EVENTO_OBJETO_EN_POSICION:
            return True
        print(f"[main] Intento {intento}/{LIMITE_REINTENTOS_INTRODUCIR_OBJETO} sin confirmación")
    return False


def capturar_lote_completo(enlace: EnlaceUART, camara) -> list:
    """Ejecuta la ráfaga de 5 caras. Devuelve la lista de bytes JPEG, o una
    lista vacía si el lote debe abortarse (ver sección 6.2): cualquier fallo
    durante la captura (giro sin confirmar, cámara o puerto serie) aborta el
    lote completo, para que se envíe un lote vacío a la PC y no se caiga el
    programa con la caja en posición."""
    imagenes = []
    try:
        for cara in range(1, CARAS_POR_OBJETO + 1):
            enlace.enviar({"accion": ACCION_GIRAR_POSICION, "cara": cara})
            respuesta = enlace.recibir(timeout=5.0)
            if (not respuesta or respuesta.get("evento") != EVENTO_EN_POSICION
                    or respuesta.get("cara") != cara):
                print(f"[main] Fallo confirmando la cara {cara}, se aborta el lote")
                return []
            time.sleep(PAUSA_ESTABILIZACION_MECANICA_S)
            frame = capturar_con_autoenfoque(camara)
            imagenes.append(comprimir_jpeg(frame))
    except Exception as e:  # noqa: BLE001 - cualquier fallo de captura aborta el lote (6.2)
        print(f"[main] Fallo durante la captura ({type(e).__name__}: {e}), se aborta el lote")
        return []
    return imagenes


def esperar_regente(enlace: EnlaceUART, descubridor: DescubridorPC) -> str:
    """Espera (sin tope) la decisión del regente. Si la PC pierde el estado
    (`ninguna`, F2), la recuperación abre una decisión nueva y se vuelve a esperar."""
    activar = lambda: enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL})  # noqa: E731
    desactivar = lambda: enlace.enviar({"accion": ACCION_DESACTIVAR_ALARMA_LOCAL})  # noqa: E731
    while True:
        try:
            return esperar_decision(descubridor, activar, desactivar)
        except DecisionPerdida as e:
            print(f"[main] {e}: se mantiene la caja y se abre una decisión nueva (sección 4.6)")
            recuperar_comunicacion(descubridor, activar, desactivar)


def ciclo_de_un_objeto(enlace: EnlaceUART, camara, descubridor: DescubridorPC) -> bool:
    """Ejecuta el ciclo de un objeto. Devuelve True cuando el objeto quedó
    clasificado y se puede autorizar el siguiente."""
    # Si activar_dispensador agota los reintentos igual se envía un lote vacío a
    # la PC (informe 5.4): así abre la alarma y la decisión del regente.
    imagenes = capturar_lote_completo(enlace, camara) if introducir_objeto(enlace) else []
    resultado, fallo_de_red = enviar_lote(descubridor, imagenes)  # lote vacío si imagenes == []
    destino = resultado.get("clasificacion", RESULTADO_ERROR_REVISION_MANUAL)

    if fallo_de_red:
        # F1 (4.4 y 4.6): alarma física local y sondeo con lote vacío hasta que la
        # PC responda; luego el regente decide en el panel. El ciclo no se reanuda solo.
        enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL})
        recuperar_comunicacion(
            descubridor,
            lambda: enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL}),
            lambda: enlace.enviar({"accion": ACCION_DESACTIVAR_ALARMA_LOCAL}),
            alarma_encendida=True)
        destino = RESULTADO_ERROR_REVISION_MANUAL

    if destino == RESULTADO_ERROR_REVISION_MANUAL:
        # La PC ya activó su alarma (8.1 puntos 1 y 2): se espera al regente
        # consultando su decisión, sin tope (sección 4.5).
        print("[main] Revisión manual: esperando la decisión del regente...")
        destino = esperar_regente(enlace, descubridor)

    enlace.enviar({"accion": ACCION_CLASIFICAR, "destino": destino})
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gateway de captura y red (Orange Pi)")
    ap.add_argument("--sin-orden", action="store_true",
                    help="no esperar la orden de inicio de la PC (solo para pruebas sueltas; "
                         "no es parte del contrato)")
    args = ap.parse_args(argv)

    # Puerto confirmado en hardware real (Orange Pi Zero 2W, Armbian Trixie):
    # UART0 en los pines 8/10 del header, liberado de la consola serial.
    # Ver orange-pi/CLAUDE.md para el procedimiento completo de verificación.
    enlace = EnlaceUART(puerto="/dev/ttyS0")
    try:
        camara = abrir_camara()
    except Exception:
        enlace.close()
        raise
    descubridor = DescubridorPC()

    try:
        if args.sin_orden:
            print("[main] --sin-orden: no se espera la orden de inicio de la PC")
        else:
            # Sección 4.7: con UART y cámara listas, el ciclo espera el botón
            # "Iniciar recorrido" del panel. Ningún reinicio arranca solo.
            esperar_orden_inicio(descubridor)
        print("[main] Iniciando ciclo continuo. Ctrl+C para detener.")
        while True:
            ciclo_de_un_objeto(enlace, camara, descubridor)
    except KeyboardInterrupt:
        print("[main] Detenido por el usuario.")
    finally:
        camara.release()
        enlace.close()


if __name__ == "__main__":
    main()
