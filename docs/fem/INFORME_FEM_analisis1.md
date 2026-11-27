# FEM estático del carrito SmartPharma (CalculiX + Gmsh) — cargas estáticas

Fecha: 2026-11-17. Unidades mm / N / MPa / tonne. Solver: CalculiX 2.23 (estático lineal), mallador Gmsh (Python de `~/.local/share/cessna-fem-env`). No se usó SimScale. No se modificó ningún archivo de `DiseñoGeneral_SPMv2`: se trabajó con una copia (`geom/copia.FCStd`). No se usó el banco FEM de FreeCAD (como en el Cessna: FreeCAD solo exporta geometría; no hay `.FCStd` con el análisis).

**Lectura rápida.** El carrito, con los supuestos de abajo, es muy rígido y poco esforzado salvo **un punto**: la placa superior de MDF de 4 mm (`Base superior`), que sostiene el SPM, el dispensador y el soporte de cámara y tiene un anillo estrecho entre el agujero central y el recorte del dispensador. Ahí el FEM da **8,4 mm de flecha local** (≈1,1–1,35° de inclinación equivalente del anillo donde apoya el SPM) y σvM de referencia ≈ 4 MPa (picos singulares 8–11 MPa). Todo lo demás: σvM ≲ 3 MPa en MDF (toboganes; el resto ≤ 0,8) y ≲ 14 MPa en tubos de aluminio (picos en uniones), desplazamientos < 0,3 mm. **Esto no demuestra que el diseño "pase"**: depende de materiales, uniones y apoyos supuestos (sección 8).

## 1. Modelo y sistema de coordenadas

- Ensamblaje: `~/Documents/DiseñoGeneral_SPMv2/DiseñoGeneral_SPMv2.FCStd` (142 sólidos; 133 del DG sin EspumaV4 + 29 del SPM v2 en pose home). Las posiciones se leen **globales** (con el `Placement` de los `App::Part` padres, como corrige `LEEME.md` del ensamblaje; las cajas de `Revision_DiseñoGeneral/piezas.csv` están en coordenadas locales y no sirven).
- **Eje vertical = +Y** (peso en −Y). Piso Y = 206 mm (base de las castor). Carro: X 43–544 (500 mm), Z 418–1018 (600 mm), Y 206–1115 (cuerpo); SPM, dispensador y soporte de cámara sobre la placa superior (Y 1111–1115, hasta Y 1511). Ruedas motrices en Z ≈ 510 (x = 116 y 476), castor en Z ≈ 954 (x = 125 y 461).
- Revisando el CAD con coordenadas globales: los 3 gabinetes son cajones (Y 484–604, 668–788, 850–970; separados 62 mm; cada uno 437×310 mm con paredes de 120 mm y una placa inclinador de 4 mm) dentro del carro; no encontré solapes entre ellos. El dispensador (rotado ≈ 21° en planta) ocupa el recorte rectangular de la placa superior y sube hasta Y 1511. Las piezas del carro **no se tocan** entre sí en general (holguras de 2–4 mm entre paredes y bases; los cajones quedan a 27 mm de las paredes del carro, sin guías en el CAD salvo un `Riel de extensión` en el gabinete 1): las uniones reales no están en el CAD.

## 2. Simplificaciones (todas documentadas) y qué se modeló

| Elemento | Tratamiento | Por qué / efecto |
|---|---|---|
| Paredes, bases, tapas, placa superior, cajones, toboganes (MDF 4 mm) | **Cáscaras S6** en la superficie media (espesor leído del CAD: pares de caras planas paralelas, 4,00 mm en todas) | Es la idealización estándar de placas delgadas. Se eliminaron los agujeros < 15 mm (tornillos) y las pestañas de unión (dentado < 5 mm) para poder mallar. Cobertura de masa 0,98–1,04 de la del CAD; en los 3 toboganes (cara plana 75–81 % del volumen) se ajustó la densidad para conservar la masa del CAD |
| 27 perfiles/columnas (Al 20×20 y "Columna ..." 27×18) | **Tubos de pared delgada de 4 cáscaras** con la caja envolvente del CAD y el espesor que iguala el área real de la sección (perfil 20×20: 163,3 mm², t = 2,31 mm; columnas: 49–52 mm², t = 0,56–0,60 mm; 2 piezas 68–77 mm², t 0,74–0,83) | Las vigas B31/B32 con ligaduras solo de traslación dejan el giro axial libre (singular); los tubos de cáscara no. La sección real de las columnas no es la caja (sección en C/L de 0,6 mm): la aproximación conserva el área, no la inercia exacta |
| Uniones entre piezas | **Ligaduras tipo "pegado"** (`*EQUATION`, solo traslaciones, nodo → triángulo vecino con pesos baricéntricos) cuando la distancia entre superficies medias ≤ semiespesores + 2 mm. 12–21 mil ligaduras según la malla | **Las uniones reales (tornillos, escuadras, pegamento) son POR CONFIRMAR; el pegado rigidiza la estructura** y no permite deslizamiento ni separación. Se unen también tubos con placas y placas entre sí |
| Guías de los cajones | Borde inferior (30 mm) de las paredes laterales de cada gabinete ligado a la pared del carro (ligadura con tolerancia 45 mm, tira continua) | **Supuesto**: el CAD no tiene guías (salvo el riel del gabinete 1). Comportamiento de rieles real por confirmar |
| Ruedas (4) | **Araña de vigas rígidas** (B31, E = 1000×Al) del punto de contacto con el piso (Y = 206) a los nodos de las placas bajo la chumacera KP001 (motrices) o la placa de montaje (castor). Las masas de las ruedas Rin/castor van directo al piso (no se aplican) | Las motrices (Rin, fondo Y = 213) se supusieron con llanta de 7 mm hasta Y = 206 (por confirmar). El anclaje rígido es amplio (≈130–750 nodos): las tensiones ahí **no son válidas** |
| Apoyos | Contacto de las 4 ruedas: UY = 0 en las 4; UX en una motriz; UZ en las 2 motrices | Mínimos para evitar movimiento de cuerpo rígido; las reacciones horizontales son ≈ 0,19 N |
| Dispensador (MDF/PETG, 18+ piezas), SPM (29 sólidos PETG/acero), `st11`, soporte de cámara | **No mallados**: masa de cada pieza por volumen CAD × densidad (MDF 700, PETG 1270 sólido, acero 7850, Al 2700 kg/m³) + masas dadas; peso aplicado como **fuerzas nodales repartidas por área** sobre la placa superior en la huella de cada cuerpo (casco convexo en planta + 15 mm) | Cajas punteadas en las figuras. **Supuesto crítico**: que la placa superior recibe todo ese peso en la huella; el CAD no define cómo se apoyan |
| Motores 2101/2102, acoples, KP001, riel, manijas, rótulos | Masas concentradas (fuerzas nodales) sobre los nodos de las placas/tubos bajo su caja (+4–10 mm) | Motor 0,5 kg c/u (suposición; el CAD da 0,55 kg si fuese acero). El riel de extensión se pesó como acero macizo (1,17 kg: sobrestima) |
| Servo, electrónica, batería, SPM+JGA25, cámara | Servo 0,055 kg; batería/electrónica 1 kg sobre los 4 perfiles del tablero inferior (z > 700; ubicación supuesta); SPM completo = 29 sólidos del CAD (2,01 kg a 100 % de relleno) + `st11` (0,48 kg) + 0,6 kg (motores JGA25 que no están en el CAD) + caja de 0,15 kg; cámara/Orange Pi/ESP32 0,2 kg + soporte PETG (0,34 kg) | Masas de PETG a densidad sólida: sobrestima si hay relleno parcial |
| Tornillería, rodamientos pequeños, engranes | Solo como masa (dispensador/SPM); no hay tornillos en el CAD | |

## 3. Materiales (todos supuestos por Arlo; **por confirmar**)

| Material | E [MPa] | ν | ρ | Resistencia supuesta | Nota |
|---|---|---|---|---|---|
| MDF | 3 000 | 0,25 | 700 kg/m³ | flexión ≈ 20 MPa (orden de magnitud; marca por confirmar) | Se asumió todo el MDF igual. En las columnas (tubos de 0,6 mm) y perfiles se asumió aluminio, no confirmado |
| Aluminio 6063 | 69 000 | 0,33 | 2 700 | σy ≈ 145 MPa (valor típico de hojas de datos de 6063-T5, citado de memoria, **no verificado en línea**; el mínimo garantizado es menor, ≈ 110 MPa) | Perfiles 20×20 y columnas |
| PETG impreso | 2 000 | 0,38 | 1 270 | σy ≈ 45 MPa, anisótropo | **Solo como masa; no hay tensiones del PETG** |
| Acero | — | — | 7 850 | — | Solo masas (KP001, riel, rodamientos) |

## 4. Caso de carga

Peso propio (g = 9,81 m/s², −Y) + medicamentos. Masa total aplicada **29,26 kg = 287,0 N**:

| Concepto | Masa [kg] | Dónde se aplica |
|---|---|---|
| Estructura mallada (MDF 9,69 + Al 2,52) | 12,21 (CAD de esas piezas: 12,15) | gravedad sobre las cáscaras |
| Medicamentos en cajones | 3 × 2,0 = 6,0 | presión equivalente (fuerza nodal por área tributaria, −Y) sobre la placa inclinador de cada gabinete (supuesto: la caja descansa sobre el inclinador, no sobre la base) |
| Dispensador (CAD + servo) + 1 kg en tolva | 2,63 + 1,0 | huella en la placa superior |
| SPM + st11 + motores JGA25 (0,6) + caja plato (0,15) | 3,09 + 0,15 | huella en la placa superior |
| Soporte de cámara + electrónica de visión | 0,54 | huella en la placa superior |
| Motores 2101/2102, acoples, KP001, riel | 1,0 + 0,18 + 0,18 + 1,17 | bajo cada pieza |
| Batería + electrónica | 1,0 | 4 perfiles del tablero inferior |
| Manijas, rótulos | 0,10 | frentes de cajón |

Apoyos: ver sección 2. Todas las figuras muestran flechas de carga y triángulos de apoyo.

## 5. Mallas y ejecución

| Nivel | Tamaño de elemento (pared / placa sup. / resto de placas / tubos) | Nodos | Elementos S6 | Ligaduras | CalculiX | Memoria |
|---|---|---|---|---|---|---|
| 1 | 30 / 18 / 20 / 14 mm | 66 520 | 31 655 | 12 183 | 36 s | — |
| 2 | 30 / **9** / 20 / 14 | 74 032 | 35 255 | 12 527 | 26 s | 3,9 GB |
| 3 | 30 / **5** / 20 / 14 | 96 280 | 46 115 | 14 104 | 52 s | 5,5 GB |
| 4 (referencia) | 20 / 9 / 13 / 10 | 134 609 | 64 916 | 21 101 | 110 s | 8,0 GB |

Los 4 modelos son **una sola componente conexa** (un cuerpo; comprobado en el grafo de elementos+ligaduras). Una corrida a la vez, bajo el límite de 16 GB. CalculiX expande las cáscaras (≈ 2,5× nodos). El nivel 4 es el que se grafica. Tensión: von Mises máximo de los puntos de integración del elemento (3 capas × 3 puntos), no extrapolada a los nodos.

## 6. Verificación

### 6.1 Equilibrio (nivel 4; igual en los demás)
Reacción vertical total 287,0136 N frente a peso aplicado 287,0103 N: **error 0,0011 %**; Fx, Fz ≈ 1e-6 N. Reacciones por rueda: motriz x=476 → 55,3 N; motriz x=116 → 55,5 N; castor x=461 → 85,3 N; castor x=125 → 90,9 N. **Equilibrio de momentos solo aproximado**: el centro de reacciones queda a 6,5 mm en X y 11,2 mm en Z del centro de cargas (≈ 7 N de reparto delante/atrás, 2,5 % del peso) porque las ligaduras de traslación con excentricidad (guías de 31 mm, holguras) no conservan el momento. El equilibrio de fuerzas es exacto.

### 6.2 Cálculo a mano de orden de magnitud
(a) **Ensayo de placa aislada** (`scripts/verif_placa.py`): placa MDF 429×300×4 mm, simplemente apoyada, 19,62 N uniformes, mismo flujo (S6 + pesos nodales). Flecha central: FEM 0,5267 mm (h = 30 mm) y 0,5304 mm (h = 15 mm) frente a Navier 0,5269 mm: **−0,04 % / +0,7 %**. σ: Navier 0,390 MPa en la fibra extrema; el FEM (von Mises de punto de integración) da 0,32 / 0,345 MPa: **la tensión reportada subestima la fibra extrema ≈ 12–17 %** en estas mallas (sesgo que aplica a todas las tensiones de este informe).

(b) **Placa inclinador de cada gabinete en el modelo completo** (`scripts/comparar_mano.py`; carga = 2 kg + 0,36 kg de la placa):

| Gabinete | Flecha relativa FEM (centro − borde) | Navier apoyada (flexible) | Empotrada (rígido) | σ FEM p99 / máx | σ Navier (apoyada) |
|---|---|---|---|---|---|
| 1 | 0,214 mm | 0,623 mm | 0,237 mm | 0,35 / 0,68 MPa | 0,46 MPa |
| 2 | 0,232 mm | 0,623 | 0,237 | 0,41 / 0,77 | 0,46 |
| 3 | 0,095 mm | 0,623 | 0,237 | 0,23 / 0,29 | 0,46 |

El FEM cae en el extremo rígido (≈ empotrada o más rígido): coherente con el pegado de los 4 bordes y con la unión a la base del cajón, y **confirma que las uniones supuestas rigidizan** (con bordes solo apoyados serían ≈ 0,6 mm). Las σ p99 están en el orden esperado (el máximo del FEM es un pico en ligaduras).

## 7. Resultados (nivel 4, referencia)

Figuras (regeneradas con los datos de CalculiX; **no son capturas de la ventana de un solver**; renderizado propio con matplotlib, escala de deformación 10×, título con modelo/estudio/resultado, leyenda en MPa, flechas de carga y apoyos): `figuras/`
- `carrito_n4_vm_frente_izq.png`, `carrito_n4_vm_trasera_der.png`: von Mises, ensamblaje completo (escala saturada a 10 MPa = p99,9; máximo real 13,7 MPa).
- `carrito_n4_vm_completo_frente_izq.png`, `carrito_n4_vm_completo_trasera_der.png` y `carrito_n4_vm_completo_frente_izq_interior.png` (carcasa semitransparente): igual que las anteriores pero con la geometría real teselada (`scripts/03_teselar_cuerpos.py`, `scripts/figuras_completo.py`) de los cuerpos no mallados (dispensador, SPM, cámara, motores, ruedas) en colores claros **sin von Mises** (masa concentrada); esos cuerpos se trasladan como sólido rígido con el desplazamiento medio de su apoyo ×10 (motores y ruedas sin desplazar). No se recalculó nada; el riel de extensión no se dibuja (artefacto del orden de dibujo).
- `carrito_n4_desp_frente_izq.png`, `carrito_n4_desp_trasera_der.png`: desplazamiento resultante (URES, mm).
- `carrito_n4_vm_interior.png`: sin paredes laterales, trasera ni tapas (se ven marco, tubos, cajones, bandeja).
- `carrito_n4_zona1_base_superior.png` (σvM y desplazamiento de la placa superior, planta), `carrito_n4_zonas_2_3.png` (bandeja inferior/anclaje de ruedas y marco superior), `carrito_n4_zona4_gabinetes.png`.
(Revisé las figuras visualmente: las zonas coinciden con los datos; el color de las paredes muy poco esforzadas queda en el extremo oscuro de la escala.)

### 7.1 Tabla de resultados

| Magnitud | Valor | Dónde |
|---|---|---|
| **σvM máximo global** | 13,7 MPa (nivel 4; 6,7–18,6 según malla) | `Columna frontal001` (larguero superior delantero, Al), x=214, y=1109, z=1000: **pico de ligadura/introducción de carga** (no representativo) |
| σvM máx. en MDF | 8,1 MPa (nivel 4; 7,1–10,8) | `Base superior`, esquina entrante de una ranura, x=144, z=705: **singular** |
| σvM de referencia, `Base superior` (p99 por elementos) | 4,1 MPa (4,1–4,6 en 4 mallas) | placa entre agujero, ranuras y recorte (anillos/radios) |
| σvM de referencia, todo el MDF (p99 / p99,9 ponderados por área) | 1,4 / 3,1 MPa | |
| σvM de referencia, tubos Al (p99 / p99,9 por área) | 6,5 / 10,9 MPa (nivel 4); p99,9 5,8–11,5 en las 4 mallas | uniones tubo–placa y tubo–tubo |
| σvM, paredes laterales/trasera, bases, cajones | ≤ 0,8 MPa (máx.), p99 ≤ 0,4 | — |
| σvM, toboganes (MDF) | máx. 2,7 MPa (`Tobogan gabinete 3`; probablemente pico de ligadura) | — |
| **Desplazamiento máximo** | **8,39 mm** (8,33 en niveles 2–3; 7,92 en nivel 1) | `Base superior`, x=335, y=1113,5, z=821: anillo entre el agujero del SPM y el recorte del dispensador |
| Descenso del anillo del SPM | Uy −0,9 a −8,4 mm; plano ajustado inclinado **1,35°** (1,07–1,35° según malla) | borde del agujero central R ≈ 118,6 mm |
| Otros desplazamientos | cajones 0,29 mm (inclinador), toboganes 0,13 mm, tubos ≤ 0,06 mm, paredes ≤ 0,03 mm | |

### 7.2 Sensibilidad de malla (zona crítica: placa superior)

| Nivel (tamaño placa sup.) | σvM máx. `Base superior` | σvM p99 `Base superior` | U máx. | σvM máx. Al (p99,9 Al) |
|---|---|---|---|---|
| 1 (18 mm) | 7,12 MPa | 4,61 | 7,92 mm | 18,6 (11,5) |
| 2 (9 mm) | 8,19 | 4,08 | 8,33 | 8,8 (7,8) |
| 3 (5 mm) | 10,82 | 4,18 | 8,33 | 6,7 (5,8) |
| 4 (9 mm + resto ×0,65) | 8,09 | 4,11 | 8,39 | 13,7 (10,9) |

- **Desplazamiento convergido**: nivel 1→2 +5,3 %; 2→3 −0,04 %; 2→4 +0,7 %.
- **σ máx. de la placa NO converge** (7,1 → 8,2 → 10,8 → 8,1 MPa: variación hasta +32 % entre 9 y 5 mm): es una esquina entrante (singularidad); no usarla para dimensionar. El **p99 es estable** (±6 %) y es el valor de referencia.
- **Picos de tubos varían ×2–3** con la malla (ligaduras y puntos de carga): singulares; se citan rangos, no un valor.
- No se corrió una malla global más fina que el nivel 4 (≈ 135 mil nodos): el solver directo llega a 8 GB.

### 7.3 Sensibilidad al supuesto de apoyo del SPM/dispensador (nivel 2)
Si el peso se reparte con margen de 40 mm en vez de 15 mm alrededor de las huellas: U máx. **6,73 mm** (en vez de 8,33; −19 %), σvM máx. `Base superior` 6,3 MPa (p99 3,6), inclinación del anillo 1,14°. **El resultado de la placa superior depende del supuesto de cómo se apoyan SPM y dispensador** (el CAD no lo define; la base v3 del SPM es una pieza rígida que en la realidad puede puentear el anillo).

### 7.4 Factor de seguridad aproximado (rangos, por confirmar)

| Material | Resistencia supuesta | σ usada | FS |
|---|---|---|---|
| MDF, `Base superior` | 20 MPa (flexión) | referencia p99 ≈ 4,1 – pico singular 8,1–10,8 | **≈ 2 – 5** |
| MDF, toboganes | 20 MPa | ≤ 2,7 MPa (pico) | > 7 |
| MDF, resto (paredes, bases, cajones) | 20 MPa | ≤ 0,8 MPa | > 25 |
| Aluminio 6063-T5 | 145 MPa (típico; mín. ≈ 110) | 5,8 (p99,9) – 18,6 (pico) | **≈ 8 – 25** (≈ 6 – 19 con 110 MPa) |
| PETG, acero | — | **no evaluados** (solo masa) | — |

Los FS son estáticos y sin factor dinámico, sin concentración de tensión en tornillos ni fatiga.

## 8. Lo que NO verifiqué / limitaciones

1. **Uniones reales** (tornillos, escuadras, pegamento, tipo de sujeción de columnas y chumaceras): sustituidas por pegado; rigidiza (ver 6.2b) y no da cargas en fijaciones ni aplastamiento del MDF en tornillos.
2. **Cómo se apoyan el dispensador, el SPM, la cámara** y el recorte/ranuras sobre la placa superior (7.3) y **las guías de los cajones** (supuestas).
3. **PETG**: ninguna pieza impresa (SPM, st11, cremalleras, soporte de cámara, adaptadores) tiene análisis de tensión; anisotropía y relleno no considerados (masa a 100 % de relleno).
4. **Ruedas/ejes/chumaceras/motores**: araña rígida y masas; las tensiones del anclaje de ruedas (zona 2) no son válidas; el reparto de reacciones por rueda tiene ≈ ±7 N de error por las ligaduras (6.1). Llanta de las motrices (7 mm) supuesta.
5. **Cargas no estáticas**: arranque/frenado, bordillos, rampas, impactos, vibraciones, inclinación del SPM (el SPM se inclina hasta ≈ 28°: cambia su centro de masa y la carga sobre la placa), **pandeo** de columnas/tubos (análisis lineal), fatiga, humedad/creep del MDF y PETG.
6. **Materiales**: todos supuestos; el aluminio de las "Columnas" y que todo el MDF sea igual son suposiciones. σy del 6063-T5 de memoria.
7. **Secciones**: columnas modeladas como caja de pared delgada con el área del CAD (no la inercia real); toboganes con 75–81 % de la superficie media (densidad ajustada).
8. **Convergencia**: desplazamiento converge; tensiones locales no (picos singulares); sin malla global más fina que 135 mil nodos.
9. **Discrepancias del CAD**: el agujero central de la placa superior mide R ≈ 118,6 mm (ajuste a la malla), mientras `entorno_plato.md` dice R = 100 mm; no se resolvió. Las holguras de 2–4 mm entre piezas del CAD se cerraron con ligaduras de proximidad (tolerancia 2 mm + semiespesores).
10. **Falta de comparación independiente**: no se contrastó con SolidWorks Simulation ni con un segundo solver; el único contraste a mano es la placa (6.2) y el equilibrio de fuerzas.
11. **Riel de extensión** pesado como acero macizo (sobrestima 1,17 kg), batería/electrónica ubicada por suposición, sin cables ni tapas.

## 9. Archivos y cómo reproducir

Carpeta `~/Documents/FEM/carrito_smartpharma/`:
- `scripts/01_exportar_geom.py` (FreeCADCmd: copia del ensamblaje, posiciones globales), `scripts/02_extraer_fe.py` (FreeCADCmd: superficies medias, tubos, casco de masas; `geom/faces/*.brep`, `geom/fe_piezas.json`), `scripts/fem_carrito.py` (Gmsh + ligaduras + cargas + `.inp`; `NIVEL` 1–4, variable `MARGEN`), `scripts/posproceso.py`, `scripts/figuras.py`, `scripts/comparar_mano.py`, `scripts/verif_placa.py`, `scripts/extra_resultados.py`, `scripts/correr_nivel.sh`, `scripts/fem_lib.py` (copia de la del Cessna).
- Ejecutar: `FreeCADCmd scripts/01_exportar_geom.py; FreeCADCmd scripts/02_extraer_fe.py; scripts/correr_nivel.sh 4` y `~/.local/share/cessna-fem-env/bin/python scripts/figuras.py carrito_n4`. Para otro nivel/variante: `MARGEN=40 scripts/correr_nivel.sh 2 m40`.
- `corridas/`: `carrito_nX.inp`, `*_info.json` (masas, cargas, ligaduras), `*_res.json` (resultados por pieza), `*_campo.npz`, `*_aux.json`; los `.dat/.frd/.12d` están comprimidos (`gunzip` antes de reprocesar).
- `figuras/`: PNG del nivel 4; `figuras/_previas_n1/` = primera pasada (malla gruesa).
