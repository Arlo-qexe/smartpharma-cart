# Contexto: módulo de reconocimiento (IA) — carpeta `ia/`

Este código lee las 5 fotos de una caja de medicamento y decide su destino
(rango FEFO) o la manda a revisión manual. **Está separado de `pc/` a propósito**:
mientras el equipo de IA no entregue su módulo, aquí se adelanta ese trabajo sin
tocar el servidor de comunicaciones. Estado y decisiones abiertas: @docs/DISENO.md.

## Qué hace (pipeline)

```
5 JPEG en RAM -> por cada cara: detector (regiones) -> OCR -> parseo (lote, vencimiento)
              -> consolidar entre caras (validación) -> rango FEFO -> destino
```

Módulos en `smartpharma_ia/`: `detector.py` (hoy imagen completa; CNN pendiente),
`ocr.py` (RapidOCR / guionado para pruebas), `parseo.py`, `validacion.py`,
`fefo.py`, `pipeline.py` (`Reconocedor.procesar`), `adaptador_pc.py` (puente al
contrato vigente), `config.py` + `config/reconocimiento.json`.

## Reglas (no negociables)

1. **No toques `shared/`, `pc/`, `orange-pi/` ni `esp32-firmware/` sin permiso explícito**
   (por eso esas rutas piden confirmación). El contrato con la Orange Pi es
   `procesar_lote_ocr(imagenes: list[bytes]) -> str` (destino o
   `ERROR_REVISION_MANUAL`); no cambia desde aquí. Una salida más rica
   (lote, vencimiento, confianza: pendiente S-05) es una **propuesta** en
   `docs/DISENO.md`, no un cambio.
2. **No redefinas constantes del protocolo.** Solo se importa
   `RESULTADO_ERROR_REVISION_MANUAL` de `../shared/protocol_constants.py`. Los
   nombres de destino (`TIPO_A`…) salen de `config/reconocimiento.json`.
3. **Todo en RAM**: nunca escribas las fotos recibidas a disco (ni en logs ni en
   archivos temporales). Las imágenes de entrenamiento del dataset son otra cosa
   y viven fuera del repo.
4. **Nunca lances una excepción hacia el servidor**: cualquier fallo se convierte
   en `ERROR_REVISION_MANUAL` con un `motivo`.
5. **Lo no decidido va en la config, no en el código.** Los rangos FEFO actuales
   son PLACEHOLDER (`_estado_rangos`); no los trates como definitivos ni los
   copies a otro lado.
6. **Consulta antes de asumir**: si algo de abajo (sección “Decisiones abiertas”
   de `docs/DISENO.md`) bloquea tu trabajo, pregunta al usuario.

## Entorno (venv)

```bash
bash scripts/setup_venv.sh        # .venv/ con núcleo + OCR (CPU)
OCR=0 bash scripts/setup_venv.sh  # solo núcleo
source .venv/bin/activate
python -m unittest discover -s tests -t .   # 21 pruebas, sin OCR ni GPU
```

`.venv/` no se commitea. Todo corre en CPU; la GPU es opcional y se activaría por
variable de entorno, nunca fija en el código.

## Variables de entorno

`IA_OCR` (motor, defecto `rapidocr`) · `IA_CONFIG` (ruta a otro `reconocimiento.json`).

## Cómo se conectará a `pc/` (cuando el equipo lo decida)

`pc/src/ocr_interface.py::procesar_lote_ocr` delega en
`smartpharma_ia.adaptador_pc.procesar_lote_ocr` (ver el docstring de
`adaptador_pc.py`). Hasta entonces `pc/` conserva su placeholder.
