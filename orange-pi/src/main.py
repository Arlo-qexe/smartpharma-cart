"""
Orquestador del ciclo completo en la Orange Pi (ver docs/arquitectura_comunicacion.md
para la descripción detallada de cada paso, y CLAUDE.md raíz para el resumen rápido).

Pendiente (ver shared/checklist.md): cómo se reanuda el ciclo tras un fallo
de comunicación con la PC (S-02; hoy un `input()` provisional) y la prueba con
la ESP32-S3 real (OP-16).

Flujo por objeto:
  1. activar_dispensador (con límite de reintentos)
  2. ráfaga de 5 fotos (girar_posicion / en_posicion por cada cara)
  3. envío del lote a la PC (o lote vacío si la captura falló)
  4. si falló la red: activar_alarma_local; si la PC respondió
     ERROR_REVISION_MANUAL: consultar la decisión del regente (sin tope)
  5. clasificar (destino automático o del regente)
  6. autorizar el siguiente objeto
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from protocol_constants import (  # noqa: E402
    ACCION_ACTIVAR_ALARMA_LOCAL,
    ACCION_ACTIVAR_DISPENSADOR,
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
from network.decision_client import DecisionPerdida, esperar_decision
from network.tcp_client import enviar_lote
from uart.uart_link import EnlaceUART
from capture.camera import abrir_camara, capturar_con_autoenfoque, comprimir_jpeg

import time


def introducir_objeto(enlace: EnlaceUART) -> bool:
    """Ordena activar el dispensador (entra un objeto), con el límite de reintentos definido en
    el contrato. Devuelve True si se confirmó objeto_en_posicion."""
    for intento in range(1, LIMITE_REINTENTOS_INTRODUCIR_OBJETO + 1):
        enlace.enviar({"accion": ACCION_ACTIVAR_DISPENSADOR})
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


def ciclo_de_un_objeto(enlace: EnlaceUART, camara, descubridor: DescubridorPC) -> bool:
    """Ejecuta el ciclo de un objeto. Devuelve False si el ciclo debe
    detenerse (la caja queda en posición, sin clasificar)."""
    # Si introducir_objeto agota los reintentos igual se envía un lote vacío a
    # la PC (informe 5.4): así abre la alarma y la decisión del regente.
    imagenes = capturar_lote_completo(enlace, camara) if introducir_objeto(enlace) else []
    resultado, fallo_de_red = enviar_lote(descubridor, imagenes)  # lote vacío si imagenes == []
    destino = resultado.get("clasificacion", RESULTADO_ERROR_REVISION_MANUAL)

    if fallo_de_red:
        # Falla de red/PC (8.1 punto 3, 4.4): solo aquí va la alarma física.
        enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL})
        # TODO: no hay canal para que el regente desbloquee sin la PC. Mientras
        # tanto, placeholder manual.
        input("Fallo de red con la PC. Presiona Enter cuando el regente resuelva...")
    elif destino == RESULTADO_ERROR_REVISION_MANUAL:
        # La PC ya activó su alarma (8.1 puntos 1 y 2): se espera al regente
        # consultando su decisión, sin tope (sección 4.5).
        print("[main] Revisión manual: esperando la decisión del regente...")
        try:
            destino = esperar_decision(
                descubridor, lambda: enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL}))
        except DecisionPerdida as e:
            print(f"[main] {e}: se mantiene la caja en posición y se activa la alarma local")
            enlace.enviar({"accion": ACCION_ACTIVAR_ALARMA_LOCAL})
            return False

    enlace.enviar({"accion": ACCION_CLASIFICAR, "destino": destino})
    return True


def main():
    # Puerto confirmado en hardware real (Orange Pi Zero 2W, Armbian Trixie):
    # UART0 en los pines 8/10 del header, liberado de la consola serial.
    # Ver orange-pi/CLAUDE.md para el procedimiento completo de verificación.
    enlace = EnlaceUART(puerto="/dev/ttyS0")
    camara = abrir_camara()
    descubridor = DescubridorPC()

    print("[main] Iniciando ciclo continuo. Ctrl+C para detener.")
    while True:
        if not ciclo_de_un_objeto(enlace, camara, descubridor):
            print("[main] Ciclo detenido: requiere intervención manual.")
            break


if __name__ == "__main__":
    main()
