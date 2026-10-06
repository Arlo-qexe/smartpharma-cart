# Propuesta: cómo se entera la Orange Pi de la decisión del regente

> **Estado: opción A APROBADA por el lado Orange Pi (2026-10-05) e implementada
> en `shared/` y en el lado PC.** Ver `arquitectura_comunicacion.md`, sección 4.5,
> y `CHANGELOG_protocolo.md`. Este documento se conserva como registro de las
> alternativas y de las preguntas abiertas (sección 5), cuyo estado actual está
> anotado allí.
> Autor original: Arlo.exe (con Claude Code, lado PC) — 2026-10-05.

## 1. El problema

La sección 8.2 de `arquitectura_comunicacion.md` dice que, ante un
`ERROR_REVISION_MANUAL`:

1. La caja queda en posición y la Orange Pi no autoriza el siguiente objeto.
2. El regente revisa la caja y decide la clasificación o el descarte.
3. El regente desactiva la alarma y **se notifica el desbloqueo a la Orange Pi**.
4. Solo entonces la Orange Pi ordena `clasificar` con el destino automático o
   el definido por el regente.

El paso 3 no tiene mecanismo en el contrato actual. Hoy la PC responde
`ERROR_REVISION_MANUAL` de inmediato y **cierra la conexión** (una conexión por
lote, sección 4.1), así que no hay por dónde avisar después. El panel web de la
PC ya permite "Desactivar" una alarma, pero eso solo la marca como resuelta en
la PC; la Orange Pi no se entera. Tampoco hay forma de que el regente elija un
destino, ni de implementar el botón "Iniciar recorrido".

## 2. Restricciones que vienen del diseño actual

- Flujo **estrictamente secuencial**: nunca hay dos objetos en vuelo, por eso no
  se usan IDs de correlación. Una solución no debería exigirlos.
- **Una conexión TCP nueva por intercambio** (4.1): cada conexión es su propia
  prueba de vida; el descubrimiento mDNS se cachea y se invalida al fallar.
- Los **30 s** de `TIMEOUT_TOTAL_TRANSACCION_S` son un límite de producto para
  el mecanismo físico y, al agotarse, activan la alarma física local (4.4).
  Un regente tarda mucho más de 30 s en revisar una caja.
- Sin autenticación (sección 9: red local controlada).

## 3. Opciones

### A. La Orange Pi consulta a la PC (polling por conexión corta) — **recomendada**

Tras recibir `ERROR_REVISION_MANUAL`, la Orange Pi **no** ordena `clasificar` y
entra en un bucle: abre una conexión corta a la PC, pregunta "¿hay decisión
pendiente?" y la cierra. Cuando el regente resuelve, la respuesta trae el
destino. Se repite cada pocos segundos hasta obtener respuesta.

- Como solo hay un lote en espera a la vez, la consulta no necesita ID de
  lote: la PC responde por "la alarma activa" (conserva el principio sin IDs).
- Reutiliza el patrón de conexión corta + mDNS con caché, y los mismos
  mecanismos de reintento.
- Si la PC cae mientras espera, la Orange Pi lo detecta (la consulta falla) y
  puede activar la alarma local, igual que en 4.4.
- Respuesta ilustrativa: `{"estado": "pendiente"}` o
  `{"estado": "resuelto", "destino": "TIPO_X" | "DESCARTE"}`.
- **Cambios en `shared/`:** nombre de la consulta/estados, intervalo de consulta
  (p. ej. 2 s), y un tope de espera total opcional (ver preguntas abiertas).
  Por el lado de la PC, el panel ya tiene el estado de alarmas: falta exponer
  la consulta y guardar el destino elegido.
- **Cuidado de diseño:** el servidor TCP actual interpreta el primer campo como
  "cantidad de imágenes". La consulta necesita distinguirse (otro puerto, otro
  tipo de servicio mDNS, o un valor reservado en ese campo). Esto lo debe
  decidir quien mantiene el contrato.

### B. La PC mantiene abierta la conexión del lote hasta que el regente decida

La PC no responde `ERROR_REVISION_MANUAL` de inmediato: retiene la conexión y
responde con el destino final cuando el regente resuelva.

- No necesita mensajes nuevos.
- **Pero choca con los 30 s:** la Orange Pi agotaría `TIMEOUT_TOTAL_TRANSACCION_S`
  mientras el regente revisa y activaría la alarma física local por error.
  Habría que exceptuar este caso del timeout, lo que debilita la protección
  contra fallos de red (4.4), porque una caída real de la PC no se distinguiría
  de un regente lento.
- Mezcla en un mismo mensaje "no pude clasificar" con "espera humana".

### C. Canal persistente PC → Orange Pi (la Orange Pi expone un servidor)

La PC se conecta a la Orange Pi y le empuja la decisión.

- Latencia mínima, pero contradice la decisión de 4.1 (conexiones persistentes
  requieren verificación de vida) y obliga a la Orange Pi a ser servidor y a
  descubrirse por mDNS en sentido inverso. Es la opción con más piezas nuevas.

### D. Confirmación física en el carrito (sin canal de red)

El regente resuelve con un botón/llave en el carrito que llega a la ESP32-S3.

- No usa la red, pero no permite elegir destino desde el panel ni da
  trazabilidad en la PC; además agrega un mensaje ESP32-S3 → Orange Pi nuevo.

| | Mensajes nuevos | Respeta 30 s / alarma 4.4 | Reusa patrón actual | Complejidad |
|---|---|---|---|---|
| **A. Polling** | 1 consulta | Sí | Sí | Baja |
| B. Conexión retenida | 0 | **No** | Parcial | Baja |
| C. Canal inverso | varios | Sí | No | Alta |
| D. Botón físico | 1 evento | Sí | No | Media (hardware) |

## 4. Recomendación

**Opción A.** Es la que menos contradice el diseño existente (conexión corta,
mDNS con caché, sin IDs, flujo secuencial), conserva la alarma física ante fallo
de red y no requiere que la Orange Pi acepte conexiones entrantes.

## 5. Preguntas abiertas para el equipo

> **Estado tras elegir A** (lo asumido por la implementación del lado PC, a
> confirmar por el equipo): 1) sin tope de espera en la PC; 2) destinos =
> `TIPO_X` o `DESCARTE`, formato `[A-Z0-9_]{1,32}`; 3) si la PC se reinicia,
> responde `ninguna`; 4) los fallos de comunicación siguen con alarma local;
> 5) sin autenticación (alcance de la sección 9); 6) "Iniciar recorrido"
> sigue pendiente y necesitará su propio mensaje.

1. **¿Existe un tope de espera al regente?** ¿Se espera indefinidamente, o a
   cierto tiempo se descarta/se avisa de nuevo?
2. **Destinos permitidos:** ¿el regente puede elegir cualquier `TIPO_X` o solo
   algunos? ¿Cómo se representa el descarte definitivo (8.2, paso 3)? Hoy
   `ACCION_CLASIFICAR` solo documenta `"destino": "<TIPO_X>"`.
3. **Si la Orange Pi se reinicia mientras espera:** ¿la decisión pendiente se
   descarta o se recupera? (Hoy el estado del panel vive solo en RAM.)
4. **Fallos de captura y de red:** el informe distingue tres orígenes de
   `ERROR_REVISION_MANUAL` (8.1). En los fallos de comunicación con la PC no
   hay a quién consultar; ¿ese caso se resuelve solo con la alarma física
   local, como hoy?
5. **Seguridad:** el panel y la consulta quedarían en la LAN sin autenticación.
   ¿Basta con el alcance declarado en la sección 9, o se quiere al menos un
   token compartido?
6. **Botón "Iniciar recorrido" del panel:** ¿es una orden PC → Orange Pi
   distinta (iniciar/pausar el ciclo)? Si es así necesita su propio mensaje y
   probablemente la misma solución de canal.

## 6. Qué haría el lado PC una vez decidido

- Exponer la consulta de la opción elegida y guardar el destino que el regente
  seleccione en el panel (selector de destino + "Desactivar").
- Registrar cada decisión en el historial del panel para trazabilidad.
- Pruebas con pytest y con un cliente mock de la Orange Pi.
