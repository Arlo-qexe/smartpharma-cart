# Flujo con la Arducam: capturar, probar, etiquetar, entrenar

Todos los comandos se corren desde la carpeta `IA\` en Windows. La Arducam va por USB al PC.

## 0. Montaje (se hace una vez y no se cambia entre sesiones)

- Cámara **fija en soporte**, perpendicular a la caja; la caja ocupa 70–90 % del encuadre y el texto del lote queda con al menos ~40 px de alto.
- **Fondo negro mate**, igual que las fotos de celular (el detector ya aprendió con ese fondo).
- **Luz fija y difusa** (sin reflejos sobre el cartón brillante). Si cambia la luz, el modelo se entrena con otra cosa que la de uso.
- **Luz rasante** (solo para la cara del lote troquelado): linterna o lámpara casi paralela a la superficie, desde un lado, a 10–20° del plano. Es una segunda luz aparte de la fija.
- **Autoenfoque activo**, igual que la cámara del carrito (decisión **D-12** de `shared/checklist.md`):
  no se fija el enfoque a mano. Así las fotos de entrenamiento se toman con el mismo modo con que se
  usará el sistema. Lo que sí queda fijo es la **distancia**, para que el autoenfoque converja siempre igual.

## 1. Carpetas

```
dataset_ia/
  celular/  crudo/                  ya existen (cajas 001-027)
  arducam/
    camara.json                     exposición y autofoco=true (lo crea --ajustar)
    sesion01/                       cajas 101, 102, ... (cajaNNN_caraK.jpg)
      rasante/                      misma cara con luz rasante (cajaNNN_caraK.jpg)
      captura_log.csv               nitidez, brillo, enfoque de cada foto
    referencia.csv                  caja,lote,vencimiento REALES (los escribes tú mirando la caja)
    mapa_cajas.csv                  caja_arducam,caja_celular (solo si repites una caja física)
  roboflow/                         exportes .zip de cada versión
modelos/
  v1_celular/best.onnx              el que ya tienes
  v2_arducam/best.onnx              el nuevo
```

Las cajas de la Arducam se numeran desde **101** para no chocar con las de celular (001–027).

## 2. Pasos

1. **Encontrar la cámara:** `python entrenamiento\capturar.py --listar` (si hay webcam del portátil, la Arducam suele ser el índice 1).
2. **Ajustar una vez:** `python entrenamiento\capturar.py --ajustar --camara 1 --config ..\dataset_ia\arducam\camara.json`
   - Teclas: `A` autofoco, `E`/`D` exposición, `R` exposición automática, `S` guarda, `Q` sale.
   - **Deja el autoenfoque activo:** la pantalla debe mostrar `autofoco=True` antes de guardar con `S`. **No uses `+`/`-`**: fijan el enfoque a mano y apagan el autoenfoque (`A` lo alterna; si lo apagaste, vuelve a pulsarla). Sin `camara.json` el valor por defecto ya es autoenfoque.
   - Lo que sí se ajusta aquí es la **exposición**. Comprueba con la nitidez que muestra la pantalla que el autoenfoque enfoca el texto del lote a esa distancia. La pantalla muestra lo que la cámara reporta: si no cambia al apretar la tecla, el driver ignora ese ajuste.
3. **Capturar:** `python entrenamiento\capturar.py --camara 1 --config ..\dataset_ia\arducam\camara.json --salida ..\dataset_ia\arducam\sesion01`
   - 5 caras por caja, siempre en el mismo orden. `ESPACIO` guarda.
   - **Espera ~0,5 s tras girar la caja** antes de guardar, para que el autoenfoque converja: el carrito hace lo mismo (`TIEMPO_CONVERGENCIA_AUTOENFOQUE_S = 0,5 s`, descarta frames mientras tanto). Las fotos borrosas por esa causa se detectan con `--nitidez-min`.
   - En la cara del lote: `ESPACIO` (luz fija), enciende la luz rasante y `R` (misma cara, queda en `rasante/`).
   - `U` deshace la última foto, `N` salta a la caja siguiente, `Q` sale.
   - Opcional: `--nitidez-min 40` para que no guarde fotos borrosas (el valor lo calibras con `--ajustar`).
4. **Escribir `referencia.csv`** mientras capturas (una línea por caja):
   ```
   caja,lote,vencimiento
   101,A50939,09/2027
   102,60925,2028-10-31
   ```
5. **Probar sin reentrenar** (modelo v1 con fotos de la cámara nueva):
   ```
   python -u entrenamiento\probar.py --modelo modelos\best.onnx --fotos dataset_ia\arducam\sesion01 --referencia dataset_ia\arducam\referencia.csv --csv res_arducam.csv
   python -u entrenamiento\probar.py --modelo modelos\best.onnx --fotos dataset_ia\arducam\sesion01\rasante --referencia dataset_ia\arducam\referencia.csv --csv res_rasante.csv
   ```
   Al final imprime la **exactitud real** (lote, vencimiento y ambos) y el tiempo por caja. Compara las dos corridas: la luz que lea más lotes es la que queda en el montaje.
6. **Probar en vivo** (captura las caras y corre todo en el momento):
   `python entrenamiento\en_vivo.py --modelo modelos\best.onnx --camara 1 --config dataset_ia\arducam\camara.json`
7. **Etiquetar:** sube las fotos normales de `sesion01` al **mismo proyecto de Roboflow** (no la carpeta `rasante`), usa Auto Label solo en ese lote nuevo y revisa a mano. Crea una versión nueva y descarga el zip en formato YOLOv8.
8. **Entrenar** (Colab, igual que antes) cambiando la línea de `entrenar.py` por:
   ```
   --arducam-desde 101 --mapa /content/mapa_cajas.csv
   ```
   (sin `--mapa` si no repetiste cajas). Así el celular siempre entrena y **solo las cajas de la Arducam validan**.
9. **Evaluar con cajas que el modelo no vio** y comparar con v1 usando `referencia.csv`.

## 3. Reglas para que el resultado sea honesto

- **Una caja física no puede estar en entrenamiento y en validación.** Si repites cajas que ya fotografiaste con el celular, anota el par en `mapa_cajas.csv`; `entrenar.py` saca esa caja de celular del entrenamiento.
- **La prueba final se hace con cajas que nunca se usaron para entrenar** (idealmente cajas nuevas), y siempre contra `referencia.csv`: medir "hubo lectura" no es medir "la lectura es correcta".
- **No cambies distancia, luz ni el modo de enfoque (autoenfoque) a mitad de sesión.** Si cambian, abre otra sesión (`sesion02`) y anótalo.
- Las fotos **no van al repositorio** (están en `dataset_ia/`, fuera de él).

## 4. Cuánto capturar

| Meta | Mínimo sugerido |
| --- | --- |
| Cajas nuevas con la Arducam | 60 (300 fotos) |
| Caras con lote y fecha | ≥ 100 en total, con variedad de laboratorios y de troquelado/tinta |
| Cajas de validación (solo Arducam) | ≥ 10, con `referencia.csv` completo |

Con menos de eso, el mAP de lote y fecha se mide sobre pocos ejemplos (en la validación actual son 5 de cada uno) y no es confiable.
