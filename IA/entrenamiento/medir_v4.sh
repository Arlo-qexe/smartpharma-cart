# Espera a que termine el entrenamiento (best.onnx de yolo_v4) y mide las cajas de val_341_360 con y sin corte temprano.
# Rutas fijas: edítalas para otro modelo. Uso: bash entrenamiento/medir_v4.sh
cd "D:/Universidad/Universidad/Universidad/pruebas/ia"
M=dataset_ia/yolo_v4/runs/detector/weights/best.onnx
while [ ! -f "$M" ]; do sleep 20; done
sleep 60   # que termine de escribir y liberar la CPU
for f in "" "--corte-temprano"; do
  # Salida a un CSV nuevo: no sobrescribe res_v4.csv (la medición original de v4)
  nombre=$([ -z "$f" ] && echo sin_corte || echo con_corte)
  echo "=== v4 $nombre"
  PYTHONUTF8=1 python entrenamiento/probar.py --modelo $M --fotos dataset_ia/arducam/val_341_360 --referencia dataset_ia/arducam/val_341_360/referencia.csv --csv dataset_ia/arducam/val_341_360/res_v4_$nombre.csv $f 2>&1 | grep -E "^caja3|Tiempo|Exactitud"
done
