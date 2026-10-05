"""
Orquestador del ciclo completo en la Orange Pi (ver docs/arquitectura_comunicacion.md
para la descripción detallada de cada paso, y CLAUDE.md raíz para el resumen rápido).

Este archivo es un ESQUELETO: la lógica de alto nivel y las llamadas a los
demás módulos ya están encadenadas correctamente, pero varios detalles de
hardware (puerto serie real, índice de cámara, etc.) están marcados con TODO.

Flujo por objeto:
  1. introducir_objeto (con límite de reintentos)
  2. ráfaga de 5 fotos (girar_posicion / en_posicion por cada cara)
  3. envío del lote a la PC (o lote vacío si la captura falló)
  4. si falló la red: activar_alarma_local
  5. clasificar (destino automático o del regente)
  6. autorizar el siguiente objeto
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    ACCION_ACTIVAR_ALARMA_LOCAL,
    ACCION_CLASIFICAR,
    ACCION_GIRAR_POSICION,
    ACCION_INTRODUCIR_OBJETO,
    CARAS_POR_OBJETO,
    EVENTO_EN_POSICION,
    EVENTO_OBJETO_EN_POSICION,
    LIMITE_REINTENTOS_INTRODUCIR_OBJETO,
    PAUSA_ESTABILIZACION_MECANICA_S,
    RESULTADO_ERROR_REVISION_MANUAL,
)

from network.mdns_discovery import DescubridorPC
from network.tcp_client import procesar_lote
from uart.uart_link import EnlaceUART
from capture.camera import abrir_camara, capturar_con_autoenfoque, comprimir_jpeg

import time


def introducir_objeto(enlace: EnlaceUART) -> bool:
    """Ordena introducir un objeto, con el límite de reintentos definido en
    el contrato. Devuelve True si se confirmó objeto_en_posicion."""
    for intento in range(1, LIMITE_REINTENTOS_INTRODUCIR_OBJETO + 1):
        enlace.enviar({"accion": ACCION_INTRODUCIR_OBJETO})
        respuesta = enlace.recibir(timeout=5.0)
        if respuesta and respuesta.get("evento") == EVENTO_OBJETO_EN_POSICION:
            return True
        print(f"[main] Intento {intento}/{LIMITE_REINTENTOS_INTRODUCIR_OBJETO} sin confirmación")
    return False


def capturar_lote_completo(enlace: EnlaceUART, camara) -> list:
    """Ejecuta la ráfaga de 5 caras. Devuelve la lista de bytes JPEG, o una
    lista vacía si el lote debe abortarse (ver sección 6.2)."""
    imagenes = []
    for cara in range(1, CARAS_POR_OBJETO + 1):
        enlace.enviar({"accion": ACCION_GIRAR_POSICION, "cara": cara})
        respuesta = enlace.recibir(timeout=5.0)
        if not respuesta or respuesta.get("evento") != EVENTO_EN_POSICION:
            print(f"[main] Fallo confirmando la cara {cara}, se aborta el lote")
            return []
        time.sleep(PAUSA_ESTABILIZACION_MECANICA_S)
        frame = capturar_con_autoenfoque(camara)
        imagenes.append(comprimir_jpeg(frame))
    return imagenes


def ciclo_de_un_objeto(enlace: EnlaceUART, camara, descubridor: DescubridorPC):
    if not introducir_objeto(enlace):
        resultado = {"clasificacion": RESULTADO_ERROR_REVISION_MANUAL}
    else:
        imagenes = capturar_lote_completo(enlace, camara)
        resultado = procesar_lote(descubridor, imagenes)  # lote vacío si imagenes == []

        if resultado.get("clasificacion") == RESULTADO_ERROR_REVISION_MANUAL:
            # TODO: distinguir aquí si el error vino de la PC (falta de
            # consenso / lote vacío) o de un timeout de red -- solo en el
            # caso de timeout de red se debe activar la alarma local.
            # Ver docs/arquitectura_comunicacion.md sección 8.1.
            enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL})
            # TODO: bloquear el ciclo y esperar la intervención del regente
            # (ver sección 8.2) antes de continuar. Placeholder por ahora:
            input("Objeto en revisión manual. Presiona Enter cuando el regente resuelva...")

    destino = resultado.get("clasificacion", RESULTADO_ERROR_REVISION_MANUAL)
    enlace.enviar({"accion": ACCION_CLASIFICAR, "destino": destino})


def main():
    # Puerto confirmado en hardware real (Orange Pi Zero 2W, Armbian Trixie):
    # UART0 en los pines 8/10 del header, liberado de la consola serial.
    # Ver orange-pi/CLAUDE.md para el procedimiento completo de verificación.
    enlace = EnlaceUART(puerto="/dev/ttyS0")
    camara = abrir_camara(indice=0)
    descubridor = DescubridorPC()

    print("[main] Iniciando ciclo continuo. Ctrl+C para detener.")
    while True:
        ciclo_de_un_objeto(enlace, camara, descubridor)


if __name__ == "__main__":
    main()
