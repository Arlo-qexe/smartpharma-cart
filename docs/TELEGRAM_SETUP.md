# Setup: grupo de Telegram para avisos de cambios en `shared/`

Esto conecta GitHub Actions (`.github/workflows/notify-shared-changes.yml`) con un
grupo de Telegram: cada vez que alguien (o un agente de Claude Code) hace push a
`shared/**` o a `docs/CHANGELOG_protocolo.md`, el grupo recibe un mensaje con quién,
qué cambió y un recordatorio de hacer `git pull`. Sirve como historial legible para
el equipo, sin depender de que los agentes se "hablen" entre sí en tiempo real.

Requiere que el repo ya esté en GitHub (no funciona solo en local).

## 1. Crear el bot con BotFather

1. Abre Telegram y busca el usuario **@BotFather** (es el bot oficial para crear bots).
2. Envíale `/newbot`.
3. Dale un nombre visible, ej. `SmartPharma Cart Bot`.
4. Dale un username único terminado en `bot`, ej. `smartpharma_cart_bot`.
5. BotFather responde con un **token** parecido a:
   ```
   123456789:AAHk3f...resto_del_token...
   ```
   Guárdalo — es `TELEGRAM_BOT_TOKEN`. No lo subas al repo ni lo compartas en texto plano.

## 2. Crear el grupo y agregar el bot

1. En Telegram, crea un grupo nuevo (ej. "SmartPharma Cart — Dev").
2. Agrega al bot que acabas de crear como miembro del grupo (búscalo por su username).
3. Envía cualquier mensaje en el grupo (ej. "hola") para que quede una actualización
   pendiente que puedas leer en el siguiente paso.

## 3. Obtener el `chat_id` del grupo

1. En el navegador (reemplaza `<TOKEN>` por tu token real), abre:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
2. Busca en la respuesta JSON un bloque `"chat": {"id": -1001234567890, ...}` —
   para grupos el id es **negativo**. Ese número es `TELEGRAM_CHAT_ID`.
   - Si no ves nada, asegúrate de haber enviado el mensaje del paso 2 *después* de
     agregar el bot, y vuelve a refrescar la URL.

## 4. Agregar los secrets en GitHub

En el repositorio de GitHub: **Settings → Secrets and variables → Actions → New
repository secret**. Crea dos:

| Nombre | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | el token de BotFather |
| `TELEGRAM_CHAT_ID` | el id del grupo (con el signo negativo incluido) |

## 5. Probar

Haz un cambio pequeño en cualquier archivo dentro de `shared/` (por ejemplo, un
comentario), commitéalo y pushéalo a GitHub:

```bash
git add shared/protocol_constants.py
git commit -m "test: probar notificación de Telegram"
git push
```

A los pocos segundos debería llegar el mensaje al grupo. Si no llega, revisa la
pestaña **Actions** del repo en GitHub — el log del workflow dice exactamente qué
falló (token/chat_id mal copiado es el error más común).

## Qué hace esto automáticamente a partir de aquí

- **Cualquier push** (de cualquiera de los dos agentes, o tuyo) que toque `shared/`
  o el changelog dispara el aviso — no hay que acordarse de avisar manualmente.
- Cada agente, al **iniciar una sesión** de Claude Code en `orange-pi/` o `pc/`,
  hace `git pull --ff-only` automáticamente (hook `SessionStart`, ver
  `orange-pi/.claude/settings.json` / `pc/.claude/settings.json`).
- Durante una sesión larga, si `shared/` cambia en GitHub mientras el agente sigue
  trabajando, el hook `UserPromptSubmit` lo detecta en cada mensaje que le escribas
  y le inyecta un aviso para que haga `git pull` antes de seguir (ver
  `scripts/hooks/check-shared-freshness.sh`).
