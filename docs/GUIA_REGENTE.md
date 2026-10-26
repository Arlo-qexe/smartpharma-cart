# Guía de uso para el regente de farmacia
### Panel de control de SmartPharma Cart

Esta guía explica, sin términos técnicos, cómo operar el panel desde el computador (la PC). El panel se abre
en el navegador de la misma PC, en **http://127.0.0.1:8080/**.

> Quien instala el sistema debe haber creado antes el primer administrador (ver el final de esta guía). Sin él
> nadie puede operar el panel.

---

## 1. Antes de empezar

1. El servidor de la PC debe estar encendido (lo arranca quien instala el sistema con `bash pc/scripts/start_pc.sh`).
2. La Orange Pi del carrito debe estar encendida y en la misma red. Arriba a la derecha del panel hay un
   indicador: **verde** = "Orange Pi conectada"; **rojo** = "sin contacto hace N s" (apagada, sin red, o ya en marcha
   sin consultar).
3. Abre el panel e **inicia tu turno** (sección 2). Mirar el panel es libre, pero operarlo exige turno.

## 2. Turno: quién opera

Todo lo que cambia algo (iniciar o detener el recorrido, resolver alertas, corregir el inventario, exportar,
usar las acciones del asistente) exige un **turno abierto** y queda anotado a nombre de quien lo hizo.

- **Iniciar turno** (arriba a la derecha):
  - **Auxiliar:** escribe tu **código de 6 dígitos**.
  - **Administrador:** escribe tu nombre y tu clave.
- **Solo hay un turno a la vez.** Si otra persona lo tiene abierto, debe cerrarlo (un administrador puede tomar el control).
- El turno se cierra solo tras **30 minutos sin actividad**. Al terminar, pulsa **Cerrar turno**.
- Si fallas varias veces seguidas, el acceso se bloquea unos segundos (cada fallo más, el bloqueo es mayor, hasta 15 minutos).
  Espera o pide ayuda a un administrador.
- Si **pierdes tu código**, un administrador te genera uno nuevo (el anterior deja de servir). Un código se muestra **una sola vez**.

## 3. Pestaña INICIO: el recorrido de auditoría

A la izquierda ves las **5 fotos** de la última caja (cara 1 a 5; clic en una para ampliarla). Al centro, el
**ciclo de inspección** con lo que leyó el sistema:

| Campo | Qué significa |
|---|---|
| **Clasificación** | Hacia dónde mandará el carrito la caja (`TIPO_A`, `TIPO_B`, `TIPO_C`, `DESCARTE`) o "revisión manual". |
| **Medicamento / Gramaje / Lote** | Lo que leyó el reconocimiento en la caja. |
| **Fecha de vencimiento** | Fecha leída, en día/mes/año. |
| **Estado FEFO** | Cuánto falta para vencer: **verde** (más de 90 días), **amarillo** (90 días o menos), **rojo** (ya venció). La barra se llena según los días que quedan (tope un año). |

Debajo aparece la **confianza** de la lectura (o el motivo, si pasó a revisión manual).
**Ojo:** el sistema llena estos datos solo, sin pedir confirmación. Si algo está mal, corrígelo en el inventario (sección 5).

**Botones del recorrido** (abajo a la derecha, exigen turno):

- **Iniciar recorrido:** da la orden para que el carrito empiece a auditar caja tras caja.
- **Cajas a procesar:** opcional. Escribe cuántas cajas quieres auditar; al llegar a ese número el carrito se detiene. Vacío ("Sin límite") = sigue hasta que pulses Detener.
- **Detener:** el carrito termina la caja que tiene en curso y **no toma más**. Para volver a empezar hay que pulsar **Iniciar recorrido** otra vez.

Notas:
- Detener actúa **entre cajas**: nunca interrumpe una caja a medias, ni una alerta pendiente.
- La orden de iniciar **vence a los 60 segundos** si la Orange Pi no la recoge (así no arranca sola más tarde).
- Si reinicias el servidor de la PC, el recorrido se detiene: hace falta una orden nueva.

## 4. Pestaña ALERTAS: cuando el sistema no puede decidir

Una caja pasa a **revisión manual** cuando el sistema no logra leerla o clasificarla. El carrito **se queda esperando
con la caja en su sitio** hasta que tú decidas. Motivos habituales:

| Mensaje | Qué pasó |
|---|---|
| **Sin consenso en el reconocimiento** | No se pudo leer con seguridad el lote o el vencimiento (por ejemplo, "no se leyó el lote"). |
| **Lote vacío (captura fallida o reintento tras perder la conexión)** | La cámara o el carrito falló y no hay fotos, o se perdió la conexión con la PC. |
| **Lote inválido** | Las fotos llegaron dañadas o fuera de lo esperado. |

**Qué hacer:**

1. Mira la caja (y las fotos en INICIO si hace falta) y decide su destino.
2. En la fila de la alerta, escribe el destino: un tipo de producto (`TIPO_A`, `TIPO_B`, `TIPO_C`…) o **`DESCARTE`** (también
   si retiraste la caja a mano).
3. Pulsa **Resolver / Confirmar**. La Orange Pi recibe el destino en su siguiente consulta (cada 2 segundos) y ordena clasificar.

Colores de las alertas: **rojo** = el carrito la está esperando (bloquea el ciclo); **amarillo** = sigue abierta pero ya nadie la espera
(solo falta cerrarla); **gris** = resuelta. Una alerta amarilla solo se cierra.

> El carrito **nunca sigue solo** tras un fallo de comunicación: siempre espera tu decisión, porque durante la alarma la caja pudo retirarse a mano.

## 5. Pestaña INVENTARIO: una fila por caja auditada

Cada caja que pasa por el carrito queda aquí, con lo que leyó el sistema (medicamento, gramaje, lote, vencimiento, confianza),
su clasificación y quién estaba a cargo.

- **Buscar y filtrar:** busca por medicamento, lote, destino o auxiliar (sin importar mayúsculas ni tildes); filtra por
  clasificación, por vencimiento (vencidos, próximos…), por fechas, y elige ver las activas, las eliminadas o todas.
  También hay una opción para ver solo las que quedaron en **revisión manual**.
- **Ordenar:** haz clic en el título de una columna. Lo que está vacío queda siempre al final.
- **Vista "Agrupado por medicamento y lote":** junta las cajas iguales y pone primero lo que vence antes (útil para FEFO: *primero en vencer, primero en salir*).
- **Editar** (exige turno): corrige medicamento, gramaje, lote, vencimiento y notas. El resultado del reconocimiento y el
  destino **no** se pueden editar. Cada corrección queda en el REGISTRO con el valor anterior y el nuevo.
- **Eliminar / Restaurar:** eliminar solo **oculta** la caja (no se borra nunca); se recupera con *Restaurar* desde la vista "Eliminadas".
- **Exportar CSV:** descarga **todas** las cajas que cumplen los filtros visibles (no solo la página). Ver sección 7.

**Ojo con los duplicados:** si se perdió la conexión con la PC y volvió, puede aparecer una caja de más (un lote vacío del
reintento). Elimínala a mano.

## 6. Pestaña REGISTRO: la bitácora que nadie puede editar

Aquí queda **todo lo que pasa**: lotes recibidos, alertas abiertas y resueltas (cuál, con qué destino y quién), órdenes de iniciar y
detener, correcciones del inventario, turnos, intentos fallidos y exportaciones, con fecha, hora y responsable.

- Es **de solo lectura**: no hay forma de editarlo ni borrarlo desde el panel. Cada evento lleva un sello que depende del anterior.
- **Verificar integridad:** comprueba que nadie haya alterado el archivo por fuera. Debe decir que el registro está íntegro.
  Si avisa de una alteración, informa de inmediato a quien administra el sistema.
- Filtra por tipo de evento, texto (responsable, lote, destino…) y fechas.
- Se conserva aunque se reinicie la PC.

## 7. Exportar a Excel (CSV)

En INVENTARIO y en REGISTRO hay un botón **Exportar CSV** (exige turno). Descarga un archivo con **todo** lo que cumple los
filtros que tengas puestos (para sacar todo, límpialos antes). Se abre con Excel (separador `;`, tildes correctas).
Cada exportación queda anotada en el registro con quién la hizo. En el CSV del registro, la columna "Sello" permite comprobar
el orden de los eventos.

## 8. Pestaña USUARIOS (solo administradores)

- **Crear** un auxiliar: el sistema genera su **código de 6 dígitos** y lo muestra **una sola vez**: anótalo y entrégaselo a la persona.
- **Regenerar código** si se pierde; **Desactivar / Activar** para suspender el acceso; **Eliminar** si ya no trabaja aquí (la persona
  ya no puede entrar, pero sus acciones pasadas siguen a su nombre).
- Los nombres **no se reutilizan**, ni siquiera tras eliminar, para que el registro nunca confunda a dos personas.

## 9. El asistente (panel lateral de INICIO)

Un chat para preguntar por el estado del sistema, las alarmas o las últimas clasificaciones. Hoy funciona en **modo de prueba**
(reglas fijas, no es un modelo de lenguaje): solo entiende preguntas sencillas. **No decide el destino de ninguna caja**: eso es siempre tuyo.

## 10. Si algo falla

| Situación | Qué hacer |
|---|---|
| "Orange Pi sin contacto" | Revisa que esté encendida y en la misma red que la PC. Si el recorrido estaba en marcha, el carrito se queda esperando. |
| Muchas alertas "Lote vacío" | Probable falla de cámara o de movimiento del carrito, o de red. Avisa al equipo técnico. |
| Muchas alertas "Sin consenso" | Las fotos no se leen bien: revisa luz, distancia y que las cajas estén limpias y bien orientadas. |
| No puedo iniciar turno | Comprueba el código/clave; si está bloqueado, espera. Si perdiste el código, pide uno nuevo a un administrador. |
| "Inicia tu turno…" al pulsar un botón | Tu turno venció (30 min sin actividad): vuelve a iniciarlo. |
| La pantalla se ve vieja tras una actualización | Recarga con **Ctrl + F5**. |
| Se reinició el servidor | Se pierden las fotos en pantalla y las alertas abiertas, y el recorrido se detiene (el inventario y el registro **no** se pierden). Pulsa **Iniciar recorrido** de nuevo. |

---

### Anexo: lo que debe hacer quien instala el sistema (una sola vez)

1. Crear el primer administrador, en una terminal: `bash pc/scripts/usuarios.sh crear-admin` (nombre y clave de al menos 10 caracteres).
2. Arrancar el servidor: `bash pc/scripts/start_pc.sh`. Con la lectura por IA: `PC_RECONOCIMIENTO=ia bash pc/scripts/start_pc.sh`
   (si no, el sistema clasifica **al azar**, solo para pruebas).
3. Si la PC cambia de red (por ejemplo, el router de la demo), **reiniciar el servidor** después de conectarse.
4. Los rangos de FEFO que usa el carrito para clasificar por fecha son **provisionales** hasta que el equipo de IA los confirme.
