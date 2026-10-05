# SmartPharma Cart — Contexto del proyecto

Asistente mecatrónico e IoT para auditoría de inventario y clasificación FEFO
de medicamentos en droguerías. Carrito móvil con manipulador esférico paralelo
que captura 5 fotos por caja (lote/fecha de vencimiento) y las envía a un PC
externo para reconocimiento (OCR) y clasificación.

## Arquitectura de comunicación (resumen)

```
ESP32-S3  <--UART-->  Orange Pi Zero 2W  <--TCP/mDNS-->  PC  <--Panel de control-->  Regente de farmacia
(control físico          (gateway de captura              (reconocimiento y
 en tiempo real)           y red)                          votación)
```

- **Principio central:** flujo de control estrictamente secuencial. Ningún
  nodo avanza al siguiente objeto sin una confirmación explícita del actual —
  esto elimina condiciones de carrera sin necesitar locks ni IDs de
  correlación.
- **Documentación completa del protocolo:** @docs/arquitectura_comunicacion.md
- **Contrato de mensajes/constantes (fuente única de verdad):** @shared/protocol_constants.py
- **Historial de cambios al protocolo:** @docs/CHANGELOG_protocolo.md

## Regla de oro para todo el equipo

**Nunca escribas un nombre de mensaje, un timeout o un formato de datos
directamente dentro de `orange-pi/`, `pc/` o `esp32-firmware/`.** Todo cambio
al contrato se hace primero en `shared/`, se documenta en
`docs/CHANGELOG_protocolo.md`, y se avisa al resto del equipo antes de hacer
push. El código de cada lado solo *importa* las constantes de `shared/`,
nunca las repite a mano — así una incompatibilidad se detecta al programar,
no al probar con el hardware real.

**Todo cambio en `shared/` se commitea y se pushea en el mismo turno en que se
hace, nunca se deja acumulado para después.** Esto es lo que dispara el aviso
automático al equipo (ver siguiente sección) — un cambio sin pushear es un
cambio que el otro lado no puede ver.

## Sincronización automática entre los dos agentes (orange-pi / pc)

Como cada agente de Claude Code trabaja desde su propia subcarpeta
(`cd orange-pi && claude` / `cd pc && claude`), nunca se ven el uno al otro en
vivo. En vez de coordinarlos manualmente con `git push`/`git pull` a mano, el
repo trae tres piezas que hacen esto automático:

1. **Aviso al equipo (Telegram):** `.github/workflows/notify-shared-changes.yml`
   dispara un mensaje a un grupo de Telegram cada vez que un push toca
   `shared/**` o `docs/CHANGELOG_protocolo.md` — con quién, qué cambió, y un
   recordatorio de hacer `git pull`. Esto además sirve como historial legible
   del contrato para el resto del equipo. Setup (una vez, requiere el repo ya
   en GitHub): @docs/TELEGRAM_SETUP.md.
2. **Pull automático al iniciar sesión:** `orange-pi/.claude/settings.json` y
   `pc/.claude/settings.json` registran un hook `SessionStart` que corre
   `git pull --ff-only` apenas arranca el agente — así cada sesión nueva
   empieza siempre con la versión más reciente de `shared/`, sin que nadie
   tenga que acordarse.
3. **Aviso dentro de una sesión larga:** un hook `UserPromptSubmit`
   (`scripts/hooks/check-shared-freshness.sh`) revisa en cada mensaje que le
   escribas al agente si `shared/` cambió en `origin/main` desde el último
   pull, y si es así le inyecta un aviso para que haga `git pull` antes de
   seguir — útil si el otro agente pushea un cambio mientras una sesión ya
   está en curso.

Ningún agente necesita "saber" del otro directamente: el repo remoto + estos
hooks + el aviso en Telegram son el canal de comunicación completo.

## Entornos virtuales (venv) — obligatorio en ambos lados

`orange-pi/` y `pc/` corren en máquinas con arquitectura distinta (ARM64 vs
x86_64 + CUDA), así que **nunca comparten un venv**. Cada una crea el suyo
localmente la primera vez con `bash scripts/setup_venv.sh` (ver la sección
"Entorno" del `CLAUDE.md` de cada lado) — `.venv/` está en `.gitignore`, nunca
se commitea ni se copia entre máquinas. El hook `SessionStart` revisa esto
automáticamente y avisa al agente si falta crear el venv.

## Permisos de cada agente

`orange-pi/.claude/settings.json` y `pc/.claude/settings.json` ya traen un
perfil de permisos pensado para este proyecto (además de los hooks de
sincronización):

- **Permitido sin preguntar:** lectura de todo el repo, `git status/log/diff/fetch/pull/add/commit`, correr Python/pytest, `pip install` (siempre dentro del venv activado), editar archivos **dentro de su propia carpeta**.
- **Pide confirmación:** `git push`, cualquier instalación a nivel de sistema (`apt`, `sudo`), descargas (`curl`/`wget` — relevante para modelos GGUF grandes en `pc/assistant/models/`), y **editar `shared/` o la carpeta del otro agente** — esto refuerza la regla de oro a nivel de permisos, no solo de convención.
- **Bloqueado siempre:** `git push --force`, `git reset --hard`, `rm -rf`, lectura de `.env`/llaves SSH.

Por si Claude Code resuelve los permisos desde la raíz del repo en vez de la
subcarpeta donde se invoca (hay cierta ambigüedad documental al respecto), el
mismo perfil base también vive en `.claude/settings.json` de la raíz.

## Qué decirle a cada agente la primera vez

No hace falta un prompt especial — `CLAUDE.md` se carga automático y el hook
`SessionStart` ya hace `git pull` y revisa el venv. Lo único que vale la pena
escribir en el primer mensaje de una sesión de trabajo (no de setup) es algo
como:

> "Antes de proponer cambios, resume en qué quedó [orange-pi/pc] según tu
> CLAUDE.md y los pendientes de la sección correspondiente, y confirma que el
> venv está activado."

Esto obliga al agente a anclarse en el estado real del proyecto (no en lo que
"cree recordar" de una sesión anterior) antes de tocar código.

## Estructura del repo

- `shared/` — contrato de comunicación (constantes + esquemas). Fuente única de verdad.
- `orange-pi/` — gateway de captura y red (Python). Ver `orange-pi/CLAUDE.md`.
- `pc/` — servidor de reconocimiento (Python). Ver `pc/CLAUDE.md`.
- `esp32-firmware/` — control físico en tiempo real (C++/Arduino). Ver `esp32-firmware/CLAUDE.md`.
- `docs/` — arquitectura y decisiones de diseño.
- `tests/integration/` — pruebas de extremo a extremo con ambos lados reales conectados.

## Flujo de un objeto (referencia rápida)

1. Orange Pi ordena `introducir_objeto` → ESP32-S3 confirma `objeto_en_posicion` (máx. 5 reintentos).
2. Por cada una de las 5 caras: Orange Pi ordena `girar_posicion` → ESP32-S3 confirma `en_posicion` → Orange Pi captura y comprime la foto.
3. Orange Pi envía el lote (o un lote vacío si algo falló) a la PC por TCP.
4. La PC responde `{"clasificacion": "<TIPO_X>" | "ERROR_REVISION_MANUAL"}`.
5. Si hubo error de red (no de reconocimiento), Orange Pi ordena `activar_alarma_local` en la ESP32-S3.
6. Orange Pi ordena `clasificar` con el destino final (automático o definido por el regente).
7. Solo entonces se autoriza el siguiente objeto.

## Pendientes conocidos del proyecto

- Calibración de tiempos (pausas, timeouts) con hardware real — ver `shared/protocol_constants.py`.
- Diseño de la interfaz del panel de control / alarma (contrato ya definido, interfaz sin construir).
- Decisión: control del servomotor del dispensador desde la ESP32-S3 (recomendado) vs. la Orange Pi — ver sección 5.5 de `docs/arquitectura_comunicacion.md`.
- El módulo de reconocimiento (OCR/clasificación) es responsabilidad del equipo de IA — este repo solo define el contrato de entrada/salida con él (ver `pc/src/ocr_interface.py`).
