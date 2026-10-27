# Espera a que termine el entrenamiento (best.onnx de yolo_v4) y mide las cajas de val_341_360 con y sin corte temprano.
# Rutas fijas: edítalas para otro modelo. Uso: bash entrenamiento/medir_v4.sh
cd "C:/Users/User/Documents/Universidad/Universidad/pruebas/ia"
M=dataset_ia/yolo_v4/runs/detector/weights/best.onnx
while [ ! -f "$M" ]; do sleep 20; done
sleep 60   # que termine de escribir y liberar la CPU
for f in "" "--corte-temprano"; do
  echo "=== v4 $f"
  PYTHONUTF8=1 python entrenamiento/probar.py --modelo $M --fotos dataset_ia/arducam/val_341_360 --referencia dataset_ia/arducam/val_341_360/referencia.csv --csv dataset_ia/arducam/val_341_360/res_v4.csv $f 2>&1 | grep -E "^caja3|Tiempo|Exactitud"
done
