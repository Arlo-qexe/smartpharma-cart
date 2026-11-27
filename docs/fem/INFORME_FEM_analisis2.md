# FEM estático del carrito SmartPharma, análisis 2: cuerpo único con SPM y dispensador reales

Fecha: 2026-11-17. Unidades mm / N / MPa / tonne. CalculiX 2.23 estático lineal + Gmsh (Python de `~/.local/share/cessna-fem-env`). Sin SimScale. No se tocó `DiseñoGeneral_SPMv2` ni el análisis 1 (`../INFORME_FEM_CARRITO.md`). Eje vertical +Y, mismas cargas y apoyos que el análisis 1.

**Lectura rápida.** Al tratar el ensamblaje como un solo cuerpo y mallar el SPM, el dispensador y el soporte de cámara con su geometría, la placa superior de MDF **deja de ser la zona flexible**: el desplazamiento máximo pasa de **8,4 mm (análisis 1) a 0,33 mm**, y la σ de referencia de esa placa de ≈4 MPa a ≈0,4 MPa. Esto ocurre porque el CAD ligado como "pegado perfecto" hace que la base v3 del SPM (PETG, 540 cm³), `st11` y las paredes del dispensador puenteen el anillo débil de la placa. **Es un límite rígido, no una predicción**: depende por completo de que esas piezas estén realmente fijadas a la placa (tornillos, pegamento), lo cual **no está en el CAD**. La realidad queda entre este resultado y el del análisis 1 (carga sin rigidizar). Todas las tensiones son bajas (≤ 1,1 MPa en MDF, ≤ 0,5 MPa en PETG, picos de 4–8 MPa en tubos de aluminio por uniones), pero **no demuestran que el diseño pase**: ver sección 8.

## 1. Qué se modeló (y cómo cambia respecto al análisis 1)

| Elemento | Análisis 1 | Análisis 2 |
|---|---|---|
| Paredes, bases, cajones, toboganes, placa superior (MDF 4 mm), perfiles y columnas (tubos de 4 cáscaras) | cáscaras S6 / tubos | igual (misma geometría extraída) |
| Placas del dispensador (`Basetrasera`, `Basedelantera`, `Baselateral` ×2, `Baselateral 2`, `placasoporte`) | masa sobre la placa superior | **cáscaras S6 de MDF 4 mm** (material *supuesto*, por confirmar: podría ser acrílico/otro) |
| Plato del SPM (`platov3`, disco Ø250 × 4 mm) | masa | **cáscara S6 PETG** (contorno poligonal con tolerancia 1,5 mm) |
| SPM: base v3, st11, NNR1/2/3 (baja y alta), 3 Brasop, 3 Brazov, 3 platosupv, stopper | masa | **sólidos hexaédricos C3D8I voxelizados** (PETG) |
| Dispensador: soporte motor, soporte lateral, 3 cremalleras, placa de empuje, placa empuje-resorte, manivela | masa | **sólidos voxelizados** (PETG supuesto) |
| Soporte de cámara | masa | **sólido voxelizado** (PETG supuesto) |
| Rodamientos (628ZZ, axial, 6800, 608), piñones, engranes del dispensador, anillos de retención, tuerca M20, servo MG996R, tapa | masa | **masa puntual** sobre los nodos bajo su caja (acero 7850 / PETG 1270 kg/m³; servo 0,055 kg) |
| Motores JGA25 (no están en el CAD) | 0,6 kg repartidos en la huella del SPM | **3 × 0,2 kg** bajo cada piñón (nodos de la base v3 a < 30 mm del eje del piñón) |
| Motores de tracción, batería/electrónica, cámara, acoples, KP001, riel, manijas | masas | igual (la cámara 0,2 kg va sobre el soporte) |

Cargas (iguales para comparar): gravedad 9,81 m/s² en −Y sobre **todo** el material mallado (densidad por pieza); 2 kg por gabinete sobre el inclinador (fuerza por área tributaria); **1 kg de tolva sobre la repisa interna `placasoporte` del dispensador** (antes sobre la placa superior); **0,15 kg de caja sobre `platov3`** (antes sobre la huella del SPM). Apoyos: las mismas 4 ruedas (araña rígida; UY en las 4, UX en una motriz, UZ en las 2 motrices). Peso total aplicado 285,66 N (29,1 kg; análisis 1: 287,0 N): el MDF es el mismo; la diferencia (≈1,3 N) viene de las masas de PETG y acero por volumen real y del voxelizado.

## 2. Simplificaciones nuevas (todas documentadas)

1. **Voxelización (hexaedros C3D8I) en vez de tetraedros.** Gmsh falló al mallar `base_integrada_v3` (el sólido del CAD se autointersecta: caras de área 0 y facetas solapadas; no hay forma de repararlo con los módulos disponibles) y los NNR por sus dientes helicoidales (≈17 mil tetraedros mal formados por pieza, calidad ≈ 0). Cada pieza se tesela a 0,25 mm en FreeCAD y se voxeliza por lanzado de rayos (paridad en Y); la superficie queda **escalonada**.
2. **Tamaño de voxel** 5 mm para base v3, st11, Brazov, cremalleras y cámara; 4 mm para soportes, platosupv, stopper, placas; 3,2–4,0 mm para NNR y Brasop; 2,8–3,5 mm para la manivela (nivel 1: 5 mm × factor 0,7–1,0). **Los dientes, chaflanes, agujeros < voxel y las paredes < 1–2 voxeles se pierden o se engrosan**: el voxelizado conserva 86–99 % del volumen del CAD (stopper 126 %; platosupv 86–88 %; st11 y base v3 ≈ 92 %). Los dientes de los NNR quedan como cilindros ásperos: la rigidez de los engranes no es la real.
3. **Voxeles sueltos**: un trozo de 7 voxeles de `st11` quedó sin contacto con nada y se eliminó (señalado por el control de conectividad; con él el solver daba desplazamientos de 5·10⁹ mm).
4. **Interferencias del CAD**: no se fusionan los sólidos. Las piezas que se solapan (base v3 × st11 × piñones × NNR, pares Brasop–Brazov, plato–platosupv, rodamientos) quedan **como cuerpos separados ligados por proximidad** (pegado, solo traslaciones, distancia entre superficies ≤ 2 mm + semiespesor/semivoxel); el volumen solapado cuenta dos veces en la masa (no cuantificado, pequeño). Esto equivale a **soldar rígidamente todo el mecanismo en la pose home**: se pierde cualquier articulación (es un mecanismo en el mundo real) y todas las holguras de montaje.
5. **Ligaduras de rescate**: `Placa de empuje` (pieza que desliza en el dispensador) no tocaba nada a < 2 mm; se ligó a la parte conexa más cercana (a 5,5 mm) con 32 nodos (supuesto: guía/apoyo no modelado).
6. Las guías de los cajones, el anclaje rígido de las ruedas, los tubos, el MDF (cáscaras sin tornillos ni pestañas) y los materiales son los del análisis 1.
7. Materiales supuestos (por confirmar): MDF E 3 GPa, ν 0,25, ρ 700; Al 6063 E 69 GPa, σy 145 MPa (valor típico de memoria); **PETG E 2 GPa, ν 0,38, ρ 1270 (sólido, sin relleno parcial → sobrestima la masa)**, σy 45 MPa, isótropo (el real es anisótropo).

## 3. Mallas, ejecución y límite de memoria/solver

| Nivel | Voxel (mm) | Cáscaras: pared / placa sup. / placas / tubos / dispensador (mm) | Nodos | Elem. cáscara + sólido | Ligaduras | Ecuaciones | CalculiX | Memoria |
|---|---|---|---|---|---|---|---|---|
| 3 (grueso) | 6 | 50 / 12 / 34 / 22 / 18 | 77 100 | — / 13 633 | 16 625 | — | 21 s | 2,9 GB |
| 1 (medio) | 5 | 40 / 9 / 26 / 18 / 14 | 117 889 | 33 103 / 24 357 | 22 657 | 774 462 | 36 s | 4,8 GB |
| 2 (fino) | 4,5 | 60 / 6 / 34 / 24 / 16 | 126 013 | 30 775 / 32 345 | 23 722 | 848 022 | 35 s | 5,0 GB |

**Límite del solver**: el solver directo (spooles) se cae con segmentation fault a partir de ≈ 1,0 millón de ecuaciones (1,02 / 1,12 / 1,2 M probadas: voxel 4 mm con cáscaras medias, voxel 3,6 mm, voxel 4 mm con placa 9 mm), aunque la memoria usada era 4,5–6,5 GB; con 1 hilo igual. Por eso **no se pudo refinar más**: el nivel fino solo baja el voxel de 5 a 4,5 mm y la placa superior de 9 a 6 mm, **engrosando las cáscaras del resto**. La malla del SPM (voxel 3,2–5 mm sobre piezas de 3–8 mm de espesor, 1–3 voxeles por sección) es **gruesa**: se debe leer como orden de magnitud (confianza baja a media para tensiones locales en brazos, NNR y plato; media para desplazamientos). Los 3 modelos son un único cuerpo conexo.

## 4. Verificación

**Equilibrio** (nivel fino): reacción 285,665 N frente a peso aplicado 285,662 N (**0,0011 %**; igual en los 3 niveles). Reacciones: motriz x=476 → 50,3 N; motriz x=116 → 48,8 N; castor x=461 → 102,0 N; castor x=125 → 84,6 N. **Equilibrio de momentos solo aproximado**: el centro de reacciones queda a 6,9 mm en X y 27,4 mm en Z del centro de cargas (≈ 18 N de reparto delante/atrás; en el análisis 1 eran 11 mm / ≈ 7 N) por las ligaduras de traslación con excentricidad (guías de 31 mm, voxeles, rescate de piezas). El reparto por rueda no es fiable a ±15 N; la suma sí.

**Comprobación a mano (pieza nueva)**: repisa `placasoporte` del dispensador (MDF 4 mm, 282 × 142 mm, 1 kg + 0,11 kg propio = 10,8 N uniformes). Navier apoyada: flecha 0,065 mm, σ 0,21 MPa; empotrada: 0,0087 mm. FEM (flecha relativa centro–borde): **0,002 mm; σ máx 0,11 MPa (p99 0,07)**. El FEM es 4 veces más rígido que la cota empotrada: **no concuerda con los límites simples** porque la repisa está ligada por área a las cremalleras y a la placa de empuje (se apoya y se rigidiza); sirve para confirmar el orden de magnitud (pocas centésimas de mm, ≪ 1 MPa) y que no hay una flexión grande, **no** para validar el valor absoluto. No hice una comprobación a mano del plato ni de los brazos (carga demasiado dependiente de cómo se apoyan).

**Control de conectividad**: el análisis de componentes por nodos descartó en el nivel fino 1 sub-cuerpo suelto (7 voxeles) y reveló 1 pieza (placa de empuje) por rescatar. Un primer intento con un solo chequeo por pieza no vio el sub-cuerpo suelto y devolvió desplazamientos absurdos (5·10⁹ mm); si se rehace, mantener el chequeo por nodo.

## 5. Resultados (nivel fino 2; entre paréntesis rango de los 3 niveles)

| Magnitud | Análisis 2 | Análisis 1 | Dónde |
|---|---|---|---|
| **Desplazamiento máximo global** | **0,325 mm** (0,31–0,33) | 8,39 mm | A2: inclinador del gabinete 2 (x=313, y=702, z=830). A1: anillo de la placa superior |
| Desplazamiento máx. placa superior | **0,084 mm** (0,080–0,084) | 8,39 mm | borde del agujero central, hacia la ranura |
| Desplazamiento máx. SPM / plato | 0,137 mm (0,12–0,14); inclinación del plato 0,017° | anillo del SPM 0,9–8,4 mm, 1,1–1,35° | borde del `platov3` |
| Desplazamiento máx. dispensador / cámara | 0,093 mm / 0,112 mm | — | tope del dispensador / soporte de cámara |
| σvM máx. `Base superior` | **1,06 MPa** (0,78–1,06; singular, esquina de ranura x=123, z=695) | 8,09 MPa (7,1–10,8) | misma esquina entrante |
| σvM de referencia `Base superior` (p99) | **0,39 MPa** (0,33–0,40) | 4,1 MPa (4,1–4,6) | |
| σvM máx. todo el MDF (p99 / p99,9 por área) | 1,06 MPa (0,21 / 0,32) | 8,1 (1,4 / 3,1) | |
| σvM PETG (máx. / p99 por área / p99,9) | 0,48 / 0,14 / 0,30 MPa | no evaluado | máx. `placaempujeresorte` (voxeles de contacto) |
| σvM en el plato del SPM | 0,23 MPa (p99 0,11) | no evaluado | borde de los apoyos platosupv |
| σvM en los brazos (Brazov / Brasop) | 0,23–0,26 / 0,22–0,34 MPa (p99 0,2) | no evaluado | uniones con rodamientos y NNR |
| σvM en soporte del motor y soporte lateral del dispensador | 0,09–0,17 / 0,08–0,13 MPa | no evaluado | |
| σvM máx. aluminio | 4,6 MPa (4,4–7,7); p99,9 3,9 | 13,7 (6,7–18,6) | tubo de la bandeja inferior (x=403, y=270, z=507–513), junto al anclaje rígido de la rueda: **no válido** |

Figuras (en `figuras/`; **regeneradas con datos de CalculiX, no capturas de ventana**; escala de deformación 100×, escala de color 2 MPa en las vistas completas): `carrito2_n2_vm_completo_frente_izq.png`, `carrito2_n2_vm_completo_trasera_der.png`, `carrito2_n2_vm_completo_interior.png` (carcasa semitransparente), `carrito2_n2_desp_completo_frente_izq.png`, `carrito2_n2_detalle_spm.png` (con y sin plato; escala 0,4 MPa), `carrito2_n2_detalle_dispensador.png` (con y sin paredes; escala 0,5 MPa), `carrito2_n2_base_superior.png` (planta, vM y desplazamiento). Revisadas visualmente: SPM y dispensador aparecen en su sitio sobre la placa y con mapa de color propio (sin cajas punteadas); los voxeles se notan como escalones y los NNR se ven como anillos ásperos; el dispensador sale casi uniforme (≤ 0,1 MPa) salvo una esquina inferior (artefacto de ligadura); la cámara queda tapada por el dispensador en la vista frente-izquierda. Copias en `~/Documents/Obsidian Vault/Adjuntos/fem2_*.png`.

## 6. Comparación con el análisis 1: qué cambia al incluir el SPM y el dispensador reales

1. **La placa superior se rigidiza muchísimo**: 8,4 → 0,084 mm (×100) y σ de referencia 4,1 → 0,39 MPa (×10). En el análisis 1 el peso del SPM y del dispensador entraba por fuerzas sobre un anillo de MDF de 4 mm sin rigidez adicional. Ahora la base v3 (PETG, 5–8 mil voxeles de 4–5 mm), `st11`, el plato y las paredes del dispensador están pegados a la placa y a su anillo, y el conjunto actúa como un bloque que reparte la carga sobre una zona mucho mayor y sobre el marco.
2. **Reparto de carga**: las reacciones de rueda cambian poco (suma igual; castores 187 N frente a 176 N; el efecto del peso en el SPM/dispensador está en el eje trasero–delantero, pero el artefacto de momentos de 27 mm no deja concluir). No extraje la fuerza transmitida por cada unión: la distribución de la carga entre la placa y el marco **no se midió directamente**.
3. **Esfuerzos en piezas nuevas** (todas ≤ 0,5 MPa PETG; FS ≫ 50 frente a 45 MPa, pero ver confianza en la sección 3 y 8): plato 0,23, brazos 0,2–0,34, soportes del dispensador 0,1–0,17.
4. **Lo que ya no cambia**: el desplazamiento global (0,3 mm) lo manda el inclinador del gabinete 2, igual que en el análisis 1 (0,29 mm); paredes, tubos y cajones no cambian de orden.
5. **Cuidado al interpretar**: el análisis 2 fija rígidamente SPM, dispensador, placa y marco; el análisis 1 los trata como pesos sobre un anillo flexible. El caso real (tornillos, apoyos puntuales, holguras) está entre ambos: **no se debe quedarse con 0,08 mm como flecha real**.

## 7. Factor de seguridad aproximado por material (rangos, por confirmar)

| Material | Resistencia supuesta | σ usada (referencia – pico) | FS |
|---|---|---|---|
| MDF (placa superior, nivel 2) | 20 MPa flexión | p99 0,4 – pico singular 1,1 | ≈ 18 – 50 |
| MDF, resto | 20 | ≤ 1,1 | > 18 |
| PETG (SPM, soportes, plato, dispensador) | 45 MPa (σy supuesta; el real depende de la dirección de impresión y de la adhesión de capas, probablemente menor) | p99 0,14 – pico 0,48 | ≈ 90 – 300 (confianza baja: malla voxel gruesa, sin concentración en dientes ni agujeros) |
| Aluminio 6063-T5 | 145 MPa (típico; mín. ≈ 110) | p99,9 3,9 – pico 7,7 | ≈ 19 – 37 |

Sin factor dinámico (arranque, frenado, bordillos), sin fatiga, sin tornillos, sin pandeo.

## 8. Lo que NO verifiqué / limitaciones

1. **Uniones reales**: todo pegado. Es la suposición dominante (sección 6). No hay tensiones en tornillos, pasadores, snap-fit ni aplastamiento de MDF/PETG bajo ellos.
2. **Mecanismo SPM**: la pose home se trata como un solo cuerpo rígido soldado. No se evaluaron las cargas del mecanismo en movimiento ni la inclinación hasta ≈ 28° (cambia el centro de masa y los esfuerzos).
3. **Geometría simplificada**: voxeles gruesos; dientes y rasgos pequeños perdidos; sólo 1–3 voxeles de espesor en piezas finas (platosupv, Brasop, placa empuje-resorte) → las tensiones locales son orden de magnitud, no valores de dimensionado. Malla más fina imposible por el límite de spooles (≈ 1 M ecuaciones).
4. **Materiales**: PETG isótropo y con relleno 100 %; MDF del dispensador supuesto; Al 6063 y 145 MPa de memoria. Nada verificado con datos del proveedor.
5. **Convergencia**: solo 3 mallas, no estrictamente anidadas (el nivel fino cambia también el tamaño de las cáscaras). Desplazamientos estables ±5 %; las σ máximas locales no convergen (picos en ligaduras, esquinas de ranura y anclaje de ruedas); se usa p99 como referencia. Las tensiones del anclaje rígido de las ruedas y de las uniones de tubos **no son válidas**.
6. **Comprobación a mano** solo de la repisa del dispensador y con resultado más rígido que ambas cotas; sin comprobación a mano del plato, brazos ni soporte del motor.
7. **Distribución de carga** entre elementos y fuerzas de unión: no extraídas. El equilibrio de momentos solo aproximado (27 mm de desfase en Z).
8. Mantiene las limitaciones del análisis 1 (guías de cajones supuestas, ubicación de batería, agujero central R 118,6 mm frente a R100 de `entorno_plato.md`, pandeo, cargas dinámicas, fatiga, humedad/creep, sin contraste con SolidWorks).
9. Las masas del solapamiento entre piezas se cuentan dos veces; el SPM a densidad de PETG sólido (sobrestima).

## 9. Archivos y cómo reproducir (`~/Documents/FEM/carrito_smartpharma/v2_cuerpo_unico/`)

- `scripts/01_exportar_solidos.py` (FreeCADCmd; BREP globales), `02_teselar_solidos.py` (teselación 0,25 mm), `03_extraer_placas_extra.py` (superficies medias de las placas del dispensador y del plato), `voxel.py`, `fem_v2.py` (construcción: cáscaras, voxeles, ligaduras, cargas, `.inp`; `NIVEL` 1–3), `posproceso_v2.py`, `comparar_mano_v2.py`, `figuras_v2.py`, `correr_v2.sh`; usa `scripts/fem_lib.py`, `verif_placa_lib.py` y las geometrías del análisis 1 (`../geom`).
- Ejecutar: `FreeCADCmd scripts/01_exportar_solidos.py; FreeCADCmd scripts/02_teselar_solidos.py; FreeCADCmd scripts/03_extraer_placas_extra.py; scripts/correr_v2.sh 2; python scripts/posproceso_v2.py carrito2_n2; python scripts/figuras_v2.py carrito2_n2`.
- `corridas/`: `.inp`, `*_info.json` (masas, cargas, ligaduras, ties por pieza), `*_res.json`, `*_campo.npz`; los `.dat/.frd/.12d` están comprimidos (gunzip antes de reprocesar). Notas: una corrida a la vez; no pasar de ≈ 0,9 M ecuaciones.

## 10. Más ángulos y script de vistas (solo visualización)

`scripts/figuras_vista.py` dibuja cualquier vista desde `corridas/carrito2_n2_campo.npz` (no recalcula nada). Las 44 figuras estándar están en `figuras/vistas/` (copias en el vault, `Adjuntos/fem2v_*.png`): von Mises y desplazamiento en 9 vistas (frontal, trasera, lateral izquierda y derecha, superior, isométricas frente-izquierda, frente-derecha, trasera-izquierda e inferior-trasera), interior con carcasa semitransparente (2 lados), detalles del SPM y del dispensador (frontal, lateral, superior, isométrica; con y sin plato / sin paredes de MDF), placa superior en planta y con zoom al agujero, y dos hojas resumen (`carrito2_hoja_resumen_vm.png`, `carrito2_hoja_resumen_desp.png`).

Uso:
- Una vista: `python scripts/figuras_vista.py carrito2_n2 --az 35 --el 25 --campo vm --salida foo.png`
- Detalle con zoom: `python scripts/figuras_vista.py carrito2_n2 --az 90 --el 10 --campo desp --grupo SPM --ocultar plato --zoom 2.5 --centro 311,1180,703 --escala-color 0.3 --salida spm.png`
- Todas las estándar: `--lote` (unos 8 min); solo las hojas resumen: `--hojas`.
- Cámara: `--az 0` mira la cara de los cajones (+Z, +X a la derecha), `--az 90` el lado -X, `--az 180` la trasera, `--az -90` el lado +X; `--el` > 0 desde arriba, < 0 desde abajo, 90 = planta (x a la derecha, z hacia abajo).
- Opciones: `--campo vm|desp`; `--grupo todo|carro|SPM|dispensador|camara|"Base superior"`; `--ocultar plato,paredes,carcasa,tubos,dispensador_mdf` (o una etiqueta de pieza); `--transp` (carcasa semitransparente); `--escala-color MAX`; `--esc-def N` (deformación, por defecto 100); `--flechas auto|todo|apoyos|no`; `--zoom Z --centro x,y,z` (mm del modelo).

Escalas de color (iguales en todas las vistas del mismo campo para poder compararlas): vistas completas von Mises 0-2 MPa y desplazamiento 0-0,35 mm; detalles del SPM 0-0,4 MPa; dispensador y placa superior 0-0,5 MPa; desplazamiento de la placa superior 0-0,09 mm. Flechas de carga: solo en las isométricas frontales (`auto`); en las vistas ortogonales frontal/trasera/laterales/superior no se dibujan (taparían piezas y señalarían puntos ocultos) y en la vista desde abajo solo los apoyos; en la isométrica frente-derecha se omite la flecha del plato porque queda detrás del dispensador. Las figuras son regeneradas con datos de CalculiX, no capturas de ventana.
