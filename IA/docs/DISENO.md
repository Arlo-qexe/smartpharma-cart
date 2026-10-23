# Diseño del módulo de reconocimiento (`IA/`)

Basado en el esquema `Estructura_de_procesamiento_de_datos.pdf` (versión inicial,
parcialmente desactualizada) y en el estado actual del repo (contrato en
`shared/` y `pc/src/ocr_interface.py`).

## 1. Del esquema original a este módulo

| Esquema (PDF) | Estado actual del proyecto | Dónde está en `IA/` |
|---|---|---|
| Microcontrolador captura, comprime y envía por WiFi | Ahora la **Orange Pi** captura y envía por TCP | Fuera de `IA/` (ya hecho) |
| 3 imágenes por cara lateral + giros de 90° + cara superior | Contrato vigente: **5 fotos, una por cara** (`CARAS_POR_OBJETO`) — confirmado por el usuario | Entrada de `Reconocedor.procesar` |
| Modelo CNN: regiones de interés (nombre, gramaje, lote, fecha) | Sin dataset todavía | `detector.py` (`DetectorCNN` pendiente; hoy `DetectorImagenCompleta`) |
| OCR en las regiones | — | `ocr.py` (RapidOCR, CPU) |
| Validación: formato de lote, formato de fecha, confianza, comparación entre fotos | — | `parseo.py` + `validacion.py` |
| ¿Nombre, lote y fecha detectados? → guardar datos | El almacenamiento/inventario es del panel (`pc/`) | Salida `ResultadoCaja` (campos para el panel) |
| Clasificación: “determinación de rango y destino” | Rangos FEFO por vencimiento (decidido por el usuario); rangos concretos sin definir | `fefo.py` + `config/reconocimiento.json` |
| PC envía orden de clasificación al microcontrolador | Ya implementado (Orange Pi ordena `clasificar` por UART) | Fuera de `IA/` |

## 2. Entrada, salida y errores

- **Entrada:** `list[bytes]` con los JPEG (0 o 5), en memoria.
- **Salida actual (contrato vigente):** `str` — destino de `config` o `ERROR_REVISION_MANUAL`.
- **Salida detallada:** `ResultadoCaja` (`clasificacion`, `lote`, `vencimiento`,
  `nombre`, `gramaje`, `confianza`, `dias_para_vencer`, `motivo`, `lecturas`).
- **Motivos de revisión manual** (campo `motivo`): `lote_vacio`,
  `<campo>:ausente`, `<campo>:confianza_baja`, `<campo>:conflicto_entre_fotos`,
  `<campo>:sin_mayoria`. Imagen ilegible/OCR caído quedan en `observaciones` de
  cada cara y, si impiden leer los campos obligatorios, terminan en `:ausente`.

## 3. Supuestos que hice (a confirmar)

1. **Fecha solo mes/año vence el último día del mes.** Práctica farmacéutica habitual.
2. **Orden DÍA/MES/AÑO** en fechas completas.
3. **Fecha de vencimiento solo si va tras una palabra clave** (VENC, FV, EXP, CAD, Vto…) cuando no hay región etiquetada, para no confundirla con fabricación (FAB/MFG).
4. **Un producto que vence hoy aún está vigente** (`dias = 0`); `dias < 0` es vencido.
5. **Lote y fecha suelen estar en UNA cara**, por eso la estrategia por defecto es `combinar`, no votación.
6. **Rangos FEFO y destinos son PLACEHOLDER:** vencido→`DESCARTE`, ≤90 d→`TIPO_A`, ≤180 d→`TIPO_B`, resto→`TIPO_C`.
7. **Campos obligatorios: lote y vencimiento.** El nombre no es obligatorio hoy porque sin detector entrenado el OCR+regex no puede extraerlo de forma fiable.

## 4. Decisiones abiertas (necesitan respuesta del equipo)

1. **Rangos FEFO reales:** ¿cuántos destinos y qué umbrales (en días o meses)? ¿Qué es cada `TIPO_X`? ¿Un vencido va a `DESCARTE` automáticamente o siempre pasa por el regente?
2. **Votación vs. combinación:** el contrato (`UMBRAL_VOTACION_MAYORIA` > 50 % de 5 lecturas) choca con el esquema (basta una cara con lote y fecha). Hoy está en `combinar`; para usar la votación hay que cambiar `estrategia_consolidacion`. Si se quiere eliminar el umbral del contrato, es un cambio en `shared/` que se consulta aparte.
3. **¿Nombre y gramaje obligatorios?** Dependen de tener el detector CNN.
4. **S-05 (salida del reconocimiento hacia el panel):** propuesta de campos que ya produce `ResultadoCaja`: `lote`, `vencimiento` (ISO `AAAA-MM-DD`), `confianza` (0-1), `nombre`, `gramaje`, `dias_para_vencer`, `motivo`. Falta decidir cómo viajan (hoy la respuesta TCP solo lleva `clasificacion`) — sería un cambio de contrato.
5. **Política de imágenes ilegibles/borrosas:** hoy cuentan como cara sin datos. ¿Se pide recaptura a la Orange Pi? (cambio de protocolo; no se asumió).

## 5. Presupuesto de tiempo por caja

La Orange Pi espera `TIMEOUT_RESPUESTA_RECONOCIMIENTO_S` (10 s) la respuesta de la PC; si no llega,
**reenvía el lote completo** y a los 30 s (`TIMEOUT_TOTAL_TRANSACCION_S`) da el fallo por de red y
enciende la alarma local. Un reconocimiento lento no es solo lento: provoca reenvíos (la PC procesaría el
mismo lote varias veces a la vez) y alarmas falsas. Por eso el módulo debe responder **bien por debajo de 10 s**.

- **Medido (2026-10-23)**, RapidOCR en CPU de 16 núcleos, 5 caras sintéticas de 1920×1080, sin detector CNN:
  - antes: 20 llamadas al OCR por caja (5 caras × 2 giros × 2 márgenes) = **21-30 s**;
  - ahora: 5 llamadas (una pasada por cara completa) = **4,8-6,3 s**, con la misma lectura.
- **Qué se cambió:** `Ocr.leer(imagen, exhaustivo=...)`. Con foto completa (`ETIQUETA_COMPLETA`) se hace UNA
  pasada: el borde no hace falta y RapidOCR ya endereza líneas al revés. Los recortes del detector CNN siguen
  siendo exhaustivos (giros y márgenes) porque ahí sí ayudan.
- **Tope de tiempo** (`tiempo_maximo_s`, 7 s por defecto en `config/reconocimiento.json`): pasado ese tiempo se
  dejan de leer caras y se decide con lo leído (si no alcanza, revisión manual con motivo `tiempo_agotado`). Se
  comprueba entre llamadas al OCR, así que puede pasarse lo que dure UNA llamada. Contra: al saltarse caras se
  pierde la comparación entre fotos de esas caras.
- **Probado y descartado:** reducir la imagen a 960 px (1,2 → 0,8 s por llamada, con riesgo para el texto
  pequeño) y leer las 5 caras en hilos (6,2 → 4,5 s; poco para el riesgo de concurrencia con un motor compartido).
- **Límites de esta medición:** texto sintético con pocas líneas; una caja real tiene más texto por cara (más
  trabajo de reconocimiento) y la PC de la demo puede ser más lenta. **Hay que repetirla con cajas reales en la
  máquina final** (`entrenamiento/probar.py` imprime el tiempo por caja) y calibrar `tiempo_maximo_s`.
- **Pendiente con el detector CNN:** cada región por cara puede costar hasta 6 llamadas (con recortes pequeños son
  más baratas, pero no está medido).

## 6. Dataset y detector CNN (cuando se pueda)

No hay fotos aún. Para pasar de “imagen completa + regex” al detector del esquema:

- Recolectar ~300–500 cajas distintas, 5 caras cada una, **con la cámara y la iluminación reales del carrito** (Arducam/webcam a 1080p, plataforma real). Variar distancia, brillo y ángulo.
- Etiquetar cuatro clases: `nombre`, `gramaje`, `lote`, `fecha` (cajas de comprimidos con lote troquelado/impreso en tinta son lo más difícil: incluirlas).
- Entrenar un detector de objetos liviano que corra en CPU (p. ej. una variante pequeña de YOLO exportada a ONNX), y evaluar por separado: detección de regiones y exactitud del OCR resultante.
- Las fotos de entrenamiento **no se suben al repo** (peso y posible contenido comercial); van a almacenamiento del equipo.
- Implementar `DetectorCNN` respetando la interfaz de `detector.py`; el resto del pipeline no cambia.

## 7. Verificación hecha

- 32 pruebas unitarias (parseo de fechas y lotes, rangos FEFO, consolidación, pipeline, presupuesto de tiempo, adaptador), sin OCR ni GPU.
- Una corrida con RapidOCR real sobre 5 caras **sintéticas** (texto limpio renderizado): lote `AB1234`, vencimiento 31/08/2027, confianza 0.98 → `TIPO_C` con los rangos placeholder. **No** demuestra el desempeño con cajas reales (brillo, curvatura, tinta troquelada): eso solo se mide con fotos reales.
