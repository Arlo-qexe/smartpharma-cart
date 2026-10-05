# SmartPharma Cart

Asistente mecatrónico e IoT para auditoría de inventario y clasificación FEFO
de medicamentos en droguerías.

## Para desarrollar con Claude Code

Este repo está organizado para que cada persona trabaje desde su propio
dispositivo (Orange Pi o PC) sin perder sincronía con el otro lado. Antes de
empezar:

1. **Sube este repo a GitHub** (`git init && git remote add origin <url> && git push -u origin main`)
   si no lo has hecho — la sincronización automática entre agentes (ver abajo) lo necesita.
2. Configura el aviso de Telegram una sola vez: @docs/TELEGRAM_SETUP.md.
3. Lee `CLAUDE.md` (raíz) — resumen de la arquitectura, la regla de oro y cómo se
   sincronizan los dos agentes automáticamente (hooks + Telegram).
4. Entra a la carpeta que te corresponde (`orange-pi/` o `pc/`) y lee su `CLAUDE.md`.
5. Corre Claude Code **desde esa subcarpeta** (`cd orange-pi && claude` o `cd pc && claude`)
   para que cargue automáticamente el contexto general, el específico, y los hooks de
   sincronización (`.claude/settings.json` de esa subcarpeta).

## Estructura

```
smartpharma-cart/
├── CLAUDE.md                  # contexto general del proyecto
├── .claude/settings.json      # permisos base (redundante con los de cada subcarpeta)
├── .github/workflows/         # aviso automático a Telegram cuando cambia shared/
├── scripts/hooks/             # scripts de los hooks (session_start, frescura de shared/)
├── docs/                      # arquitectura, decisiones de diseño, setup de Telegram
├── shared/                    # contrato de comunicación — fuente única de verdad
├── orange-pi/                 # gateway de captura y red (Python)
│   ├── scripts/setup_venv.sh  # crea el venv de este lado (ARM64)
│   └── .claude/settings.json  # hooks + permisos de este agente
├── pc/                        # servidor de reconocimiento (Python)
│   ├── assistant/             # asistente conversacional del panel de control (LLM local)
│   ├── scripts/setup_venv.sh  # crea el venv de este lado (x86_64/CUDA)
│   └── .claude/settings.json  # hooks + permisos de este agente
├── esp32-firmware/            # control físico en tiempo real (C++/Arduino)
└── tests/integration/         # pruebas de extremo a extremo
```

## Documentos clave

- [`docs/arquitectura_comunicacion.md`](docs/arquitectura_comunicacion.md) — informe completo del protocolo.
- [`docs/CHANGELOG_protocolo.md`](docs/CHANGELOG_protocolo.md) — historial de cambios al contrato.
- [`shared/protocol_constants.py`](shared/protocol_constants.py) — constantes que ambos lados importan.
