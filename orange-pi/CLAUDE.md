# Contexto: lado Orange Pi (gateway de captura y red)

Este código corre en la Orange Pi Zero 2W y es responsable de:

1. Comunicarse con la ESP32-S3 por UART (control físico: motores, alarma, clasificación).
2. Descubrir la PC por mDNS y enviarle el lote de imágenes por TCP.
3. Capturar y comprimir las fotos (cámara USB).
4. Orquestar el ciclo completo: nunca autoriza el siguiente objeto hasta resolver el actual.

## Entorno (venv)

Este lado corre en la Orange Pi Zero 2W real (ARM64/Armbian) — **nunca** se
comparte el entorno virtual con `pc/` (x86_64), cada máquina crea el suyo:

```bash
bash scripts/setup_venv.sh     # crea .venv/ e instala requirements.txt
source .venv/bin/activate      # en cada terminal nueva
```

- `.venv/` está en `.gitignore` — nunca se commitea.
- El hook `SessionStart` (ver `.claude/settings.json`) avisa automáticamente
  al agente si `.venv/` no existe todavía en una sesión nueva.
- Nunca instales con `pip` fuera del venv activado (el `python3`/`pip` del
  sistema de Armbian se queda tal cual, para no romper nada que dependa de
  él como `wiringOP` o paquetes del sistema).
- Una vez el entorno funcione de punta a punta, congela las versiones
  exactas para reproducibilidad: `pip freeze > requirements-lock.txt`
  (ese archivo sí se commitea, a diferencia de `.venv/`).

## Antes de escribir código

- Importa TODO desde `../shared/protocol_constants.py` — nunca repitas a mano
  un nombre de mensaje, timeout o puerto.
- Consulta @../docs/arquitectura_comunicacion.md si tienes dudas sobre el
  "por qué" de una decisión (framing, reintentos, alarma local, etc.).
- Si necesitas cambiar un mensaje o timeout, ese cambio va primero en
  `../shared/`, junto con una entrada en `../docs/CHANGELOG_protocolo.md`.

## Cómo probar sin la PC real ni el hardware real

- `tests/mock_pc_server.py` simula el servidor TCP de la PC (responde según
  el contrato, sin OCR real). Úsalo para desarrollar y probar el cliente TCP
  y la lógica de reintentos sin depender de que el otro lado esté listo:

  ```bash
  python3 tests/mock_pc_server.py
  ```

- Para probar el enlace UART sin la ESP32-S3 física, considera un mock
  similar que escuche en un puerto serie virtual (`socat` + `pyserial`).

## Estructura

- `src/network/mdns_discovery.py` — descubrimiento y caché de la PC vía mDNS.
- `src/network/tcp_client.py` — envío de lotes por TCP con reintentos.
- `src/uart/uart_link.py` — enlace serie con la ESP32-S3 (framing, checksum).
- `src/capture/camera.py` — control de cámara, ráfaga de 5 fotos, compresión JPEG en memoria.
- `src/main.py` — orquestador del ciclo completo (máquina de estados de alto nivel).
- `tests/mock_pc_server.py` — servidor TCP falso para desarrollo independiente.

## Entorno real de esta placa (Armbian sobre Orange Pi Zero 2W)

- **Distribución:** Armbian precompilado (Debian Trixie Minimal/CLI, rama de
  kernel "current"), descargado directo de `armbian.com/boards/orangepizero2w`
  — no se compila desde el código fuente.
- **GPIO:** esta placa no usa una librería de GPIO propia de Armbian, sino la
  del fabricante (wiringOP), confirmada funcional en Armbian para este
  modelo:

  ```bash
  git clone https://github.com/orangepi-xunlong/wiringOP.git -b next
  cd wiringOP && sudo ./build clean && sudo ./build
  gpio readall   # verifica que reconoce los pines
  ```

  Bindings de Python (los que usa `src/uart/`):

  ```bash
  sudo apt install swig python3-dev python3-setuptools
  git clone --recursive https://github.com/orangepi-xunlong/wiringOP-Python.git -b next
  cd wiringOP-Python && sudo python3 setup.py install
  ```

- **Puerto UART hacia la ESP32-S3:** en esta placa, UART0 sale en los pines
  **8 (TX) y 10 (RX)** del header de 40 pines — misma ubicación que en
  Raspberry Pi. **Por defecto viene configurado como consola serial**, no
  como puerto de datos libre, así que hay que liberarlo antes de usarlo:

  ```bash
  sudo armbian-config
  # System -> Hardware -> desmarcar la consola serial en UART0
  # (si esa opción no aparece en el menú, editar /boot/armbianEnv.txt a mano
  #  y quitar "console=ttyS0" de la línea de comandos del kernel, luego:)
  sudo systemctl disable serial-getty@ttyS0.service
  ```

  Device tree de referencia para overlays manuales: `overlay_prefix=sun50i-h618`.

- **Antes de conectar la ESP32-S3 física**, verifica el puerto liberado con
  un loopback (unir TX con RX temporalmente) y confirma con **dos sesiones
  SSH separadas** (no con `screen` en una sola — no tiene eco local, así que
  no es una prueba inequívoca por sí sola):
  ```bash
  # Terminal 1:
  sudo cat /dev/ttyS0
  # Terminal 2 (con el puente físico puesto):
  echo "prueba123" | sudo tee /dev/ttyS0
  ```
  Si "prueba123" aparece en la Terminal 1, el puerto está libre y
  funcionando — confirmado así en hardware real el 16 de septiembre de 2026
  (Armbian Trixie, Orange Pi Zero 2W). Solo entonces actualiza el `puerto=`
  real en `src/main.py` (`/dev/ttyS0`, no el `/dev/ttyUSB0` que aparece ahí
  como placeholder).

- **Nota sobre GPIO/wiringOP:** el device tree de esta placa sí expone el
  GPIO correctamente (a diferencia de algún reporte de la comunidad sobre
  otras builds de Trixie) — `gpio readall` funciona sin overlays
  adicionales, y confirma los pines 8/10 en modo `ALT2` (UART0). Si el
  compilador se queja de `make: command not found`, falta
  `sudo apt install build-essential` (esta imagen "Minimal" no lo trae de
  fábrica).

## Acceso remoto y red (dos fases: desarrollo multi-red / demo en la universidad)

**Stack de red real de esta placa: netplan** (no NetworkManager, no
systemd-networkd editado a mano directamente). Netplan genera los archivos
reales de systemd-networkd en `/run/systemd/network/` en cada arranque — por
eso nunca se edita ahí, siempre en `/etc/netplan/*.yaml`. El archivo que
controla el WiFi en esta placa es específicamente:

```
/etc/netplan/30-wifis-dhcp.yaml
```

(el otro archivo presente, `10-dhcp-all-interfaces.yaml`, es para Ethernet —
no se toca para nada de lo siguiente).

- **Fase actual (desarrollo, varias redes):** DHCP, tal como ya está
  configurado de fábrica en `30-wifis-dhcp.yaml`. Acceso por nombre en vez de
  IP, vía `avahi-daemon` (mDNS, ya instalado):
  ```bash
  ssh <usuario>@<hostname>.local
  ```
  Misma tecnología (mDNS) que usa el protocolo de la app para descubrir la
  PC — ver `MDNS_SERVICE_TYPE` en `shared/protocol_constants.py`.

- **Fase de demo final (universidad, con hotspot propio):** netplan (como
  NetworkManager) no tiene forma nativa de decir "en esta red usa DHCP, en
  esta otra usa IP fija" automáticamente — la solución adoptada es tener dos
  plantillas guardadas y copiar la que corresponda a mano, justo antes de
  necesitarla (no vale la pena automatizar algo tan infrecuente):

  ```bash
  # Plantillas ya guardadas en el home del usuario:
  #   ~/netplan-dhcp-casa.yaml.template   (la normal, DHCP)
  #   ~/netplan-static-demo.yaml.template (IP fija, para el hotspot del día de la demo)

  # Activar modo demo (ajustar SSID/contraseña del hotspot en la plantilla primero):
  sudo cp ~/netplan-static-demo.yaml.template /etc/netplan/30-wifis-dhcp.yaml
  sudo netplan try      # revierte solo en 120s si no se confirma -- probar así, no con apply directo
  sudo netplan apply    # una vez confirmado que funciona

  # Volver a DHCP después:
  sudo cp ~/netplan-dhcp-casa.yaml.template /etc/netplan/30-wifis-dhcp.yaml
  sudo netplan apply
  ```

- **Reserva DHCP por MAC (opcional, solo para la red de casa):** configurada
  desde el router (no desde la placa) — el router siempre entrega la misma
  IP a esa MAC, y en cualquier otra red se sigue comportando como DHCP normal.

- **Aislamiento de clientes en redes de universidad:** varias redes WiFi
  académicas (eduroam y similares) bloquean que un dispositivo alcance a
  otro en la misma red ("client/AP isolation"), sin importar el método de
  direccionamiento. Esto podría explicar problemas de comunicación en
  proyectos anteriores del curso. Por eso el plan para la demo es un hotspot
  propio — probar con anticipación (dos celulares haciendo ping entre sí en
  la red de la universidad) antes del día de la demo, no ese mismo día.

## Recordatorios de diseño clave

- Nunca escribas imágenes a disco — todo en memoria (bytes de JPEG en RAM).
- Un socket TCP nuevo por lote, no uno persistente (ver arquitectura, sección 4.1).
- Si la captura falla antes de completarse, igual se envía el framing con
  cantidad de imágenes = 0 — no inventes un mensaje nuevo para reportar el fallo.
- El límite de reintentos para `introducir_objeto` vive en
  `LIMITE_REINTENTOS_INTRODUCIR_OBJETO` — no lo hardcodees en el código.
- Al agotar el límite total de 30 s sin respuesta de la PC, además de fijar
  `ERROR_REVISION_MANUAL`, envía `activar_alarma_local` a la ESP32-S3.
