#!/usr/bin/env bash
# Compila el firmware para ESP32-S3 (requiere arduino-cli y el núcleo esp32:esp32 3.x:  arduino-cli core install esp32:esp32).
# Subir:  arduino-cli upload -p /dev/ttyUSB0 --fqbn esp32:esp32:esp32s3 spm_esp32s3
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
arduino-cli compile --fqbn esp32:esp32:esp32s3 --warnings all spm_esp32s3
