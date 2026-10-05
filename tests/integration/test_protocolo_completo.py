"""
Prueba de extremo a extremo: requiere la ESP32-S3 y la cámara conectadas a
la Orange Pi, y el servidor de la PC corriendo en la misma red.

Esto NO es una prueba unitaria automática — es una guía de pasos manuales
para validar el protocolo completo una vez que ambos lados tengan su parte
lista. Ver docs/arquitectura_comunicacion.md para el detalle de cada paso.

Cómo usar:
    1. En la PC: python3 pc/src/server.py
    2. En la Orange Pi: python3 orange-pi/src/main.py
    3. Verificar en los logs de ambos lados que:
       - La Orange Pi descubre la PC por mDNS sin configuración manual.
       - El ciclo completo de un objeto se ejecuta sin errores
         (introducir_objeto -> 5 caras -> envío TCP -> clasificación -> clasificar).
       - Al desconectar la PC a mitad de una transacción, la Orange Pi
         reintenta y, tras 30 s, activa la alarma local en la ESP32-S3.
       - Al enviar un lote vacío (simulando fallo de captura), la PC
         responde ERROR_REVISION_MANUAL sin intentar OCR.
"""

# TODO: si se quiere automatizar esta prueba sin hardware real, combinar
# orange-pi/tests/mock_pc_server.py (para el lado que no se está probando)
# con una versión de orange-pi/src/main.py que no dependa de pyserial/OpenCV
# reales (inyectando un EnlaceUART y una cámara falsos).

if __name__ == "__main__":
    print(__doc__)
