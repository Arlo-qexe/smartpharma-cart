#!/usr/bin/env bash
# Compila y corre el banco de pruebas del firmware en el PC.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
g++ -std=c++17 -Wall -Wextra -DHOST_TEST -I. test_firmware.cpp -o test_firmware
./test_firmware
rm -f test_firmware telemetria_muestra.jsonl
