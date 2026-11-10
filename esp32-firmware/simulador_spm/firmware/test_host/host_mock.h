// Mínimo para compilar spm_esp32s3.ino en el PC (-DHOST_TEST): sin Arduino, FreeRTOS, PCNT ni LEDC. Los ganchos de hardware los define test_firmware.cpp.
#pragma once
#include <stdint.h>
#include <stddef.h>
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x)  ((void)(x))
#define IRAM_ATTR
