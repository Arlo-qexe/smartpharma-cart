// ═══════════════════════════════════════════════════════════════════════════════
//  SPM — firmware ESP32-S3: 3 motores DC con encoder (manipulador esférico paralelo)
//  Responde a la interfaz del simulador (simulador_spm/interfaz, pestaña «Serial / ESP32»).
//
//  Hardware       PWM por hardware (LEDC, 20 kHz) · encoders en cuadratura X4 por hardware (PCNT) · puente H DRV8833 (ver sección 1)
//  Tiempo real    Timer de hardware (1 kHz) → notificación a la tarea de control (núcleo 1, máxima prioridad) que muestrea los encoders,
//                 genera la referencia, corre el lazo discreto y escribe el PWM. Comandos y telemetría en tareas aparte (núcleo 0).
//                 El periodo real de muestreo se mide en cada ciclo (esp_timer) y se usa para estimar la velocidad; los ciclos perdidos se cuentan.
//  Control        Por motor, seleccionable en vivo: PID de posición + avance de velocidad · cascada P(posición)→PI(velocidad) · lazo abierto.
//  Referencia     Perfil de velocidad/aceleración limitadas (movimientos sueltos) o rampa entre consignas (modo «streaming» de la interfaz).
//  Seguridad      Detección de motor desbocado/atascado (polaridad invertida, encoder desconectado) → todos los motores a 0 hasta «reset».
//
//  SmartPharma (secciones 6c y 7b): trayectorias de cámara y clasificación ejecutadas a bordo desde la tabla generada `spm_trayectorias.h`, y canal UART de la
//  Orange Pi (checksum XOR de shared/protocol_constants.h) además del USB. Ver firmware/README_firmware.md y ../PROPUESTA_trayectorias_spm.md.
//    USB  {"cmd":"cam","cara":N} | {"cmd":"clas","destino":"TIPO_A|TIPO_B|TIPO_C|DESCARTE|DESCARTE_1|DESCARTE_2"} | {"cmd":"home"} | {"cfg":"descarte","val":1|2}
//    UART {"accion":"girar_posicion","cara":N}|XX → {"evento":"en_posicion","cara":N}|XX      {"accion":"clasificar","destino":"..."}|XX
//
//  Requiere arduino-esp32 3.x (API de LEDC/timer de IDF 5 y driver/pulse_cnt.h).  Placa: ESP32S3 Dev Module, 115200 baud.
//
//  Protocolo (una línea JSON por mensaje, ángulos de MOTOR en grados; el mismo del brazo 3DOF + extensiones):
//    PC → ESP32  {"m1":a,"m2":b,"m3":c}  (cualquier subconjunto) | {"motor":N,"pos":deg} | {"cmd":"reset"|"stop"|"enable"|"save"|"load"|"polarity"|"status"}
//                {"zero":N|"all"} | {"duty":N,"val":-1..1} (lazo abierto) | {"cfg":"kp","motor":N(opcional),"val":x}  (ver README_firmware.md)
//    ESP32 → PC  {"t":ms,"st":"run","m1":{"posRef":pulsos,"pos":pulsos,"ok":true,"u":‰,"e":deg},"m2":{...},"m3":{...},"jit":µs,"miss":n}  (20 Hz)
//                {"ack":"...","val":...} | {"err":"..."} | {"info":"..."}
// ═══════════════════════════════════════════════════════════════════════════════
#ifdef HOST_TEST
  #include "host_mock.h"              // banco de pruebas en PC: sustituye Arduino/FreeRTOS/PCNT/LEDC (ver test_host/)
#else
  #include <Arduino.h>
  #include <Preferences.h>
  #include "driver/pulse_cnt.h"
  #include "esp_timer.h"
  #include "freertos/FreeRTOS.h"
  #include "freertos/task.h"
  #include "freertos/semphr.h"
#endif
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <strings.h>
#include "spm_trayectorias.h"                       // GENERADO: tramos de cámara y clasificación (herramientas/generar_trayectorias_smartpharma.py)
#include "../../../../shared/protocol_constants.h"  // contrato del equipo: nombres de acciones/eventos y checksum XOR (no repetir a mano)

// Tipos usados como parámetros de funciones: deben ir ANTES de la primera función (el IDE de Arduino genera los prototipos al principio del archivo).
struct JVal { char key[12]; char str[16]; float num; float arr[4]; uint8_t n_arr; uint8_t type; };   // type: 1 número, 2 texto, 3 arreglo, 4 booleano/null
struct JMsg { JVal v[10]; uint8_t n; };

// ═══════════════════════════════════════════════════════════════════════════════
//  1. DISTRIBUCIÓN DE PINES (ESP32-S3 N16R8, mapa de la placa) Y DRIVER DRV8833
//     Motores 4 y 5 (38,21,40,39 / 41,42,1,2), servo (8), UART2 (13,14), I2C (47,48), UART0 (43,44), USB (19,20), flash/PSRAM (26-37) y
//     pines de arranque (0,3,45,46) NO se usan aquí.
//
//     DRV8833 (módulo de 2 canales; 3 motores → 2 módulos). Cada canal tiene SOLO dos entradas, xIN1 y xIN2, y las dos reciben PWM:
//       sin pines PWMx ni STBY (eso era del TB6612FNG). Tabla de verdad por canal:  (0,0) libre · (1,0) avance · (0,1) retroceso · (1,1) freno.
//       · nSLEEP: debe estar a nivel alto o el chip no maneja nada. Lo más simple: cablearlo a 3V3 (PIN_NSLEEP = PIN_NONE). Si se quiere controlar
//         desde el firmware, poner su GPIO en PIN_NSLEEP (el firmware lo sube a alto al arrancar).
//       · nFAULT (salida, drenador abierto): opcional; no se lee en este firmware (la detección de atasco es por encoder).
//       · Corriente: 1.5 A continuos por puente (2 A pico), con VM de 2.7 a 10.8 V. OJO: VM máximo 10.8 V, NO conectar 12 V al DRV8833.
//         Con los motores libres y 9.6 V (como se identificaron) está dentro de rango. Los módulos baratos suelen traer ISEN a GND (sin límite de corriente).
//       · Cables: motor + / − a xOUT1 / xOUT2; xIN1 ← pwmF, xIN2 ← pwmR. Si el eje gira al revés, intercambiar OUT1/OUT2 o poner pwm_sign = -1.
//     Dos módulos: módulo A canal A = motor 1, canal B = motor 2; módulo B canal A = motor 3.  GND común con la fuente y la ESP32.
// ═══════════════════════════════════════════════════════════════════════════════
#define PIN_M1_ENC_A   11
#define PIN_M1_ENC_B   12
#define PIN_M1_IN1      9    // pwmF
#define PIN_M1_IN2     10    // pwmR
#define PIN_M2_ENC_A   15
#define PIN_M2_ENC_B   16
#define PIN_M2_IN1     17
#define PIN_M2_IN2     18
#define PIN_M3_ENC_A    4
#define PIN_M3_ENC_B    5
#define PIN_M3_IN1      6
#define PIN_M3_IN2      7

static const int N_MOT = 3;
static const uint8_t PIN_NONE = 255;
struct MotorPins { uint8_t encA, encB, pwmF, pwmR, en; };         // pwmF = xIN1 = giro «positivo», pwmR = xIN2; en = pin de habilitación (PIN_NONE: el DRV8833 no lo tiene)
static const MotorPins PINS[N_MOT] = {
    { PIN_M1_ENC_A, PIN_M1_ENC_B, PIN_M1_IN1, PIN_M1_IN2, PIN_NONE },     // Motor 1 (NNR1)
    { PIN_M2_ENC_A, PIN_M2_ENC_B, PIN_M2_IN1, PIN_M2_IN2, PIN_NONE },     // Motor 2 (NNR2)
    { PIN_M3_ENC_A, PIN_M3_ENC_B, PIN_M3_IN1, PIN_M3_IN2, PIN_NONE },     // Motor 3 (NNR3)
};
// Forma de aplicar el PWM por las dos entradas:
//   true  = DECAIMIENTO LENTO (el que se usó al identificar los motores): giro + → IN1 fijo en alto e IN2 en bajo durante la fracción `duty` y en alto el resto
//           ((1,0) mueve, (1,1) frena en cortocircuito). La tensión media en el motor es duty·VM (lineal): la zona muerta es solo la fricción.
//   false = decaimiento rápido (PWM en una sola entrada, la otra en 0: fuera del pulso el puente queda libre); la tensión media es (2·duty − 1)·VM
//           y por debajo de duty = 0.5 casi no hay par. Cada modo da un modelo distinto: controlar con el mismo con el que se identificó.
static const bool DRIVER_DECAIMIENTO_LENTO = true;
// Enlace UART con la Orange Pi (UART2 del mapa: GPIO 13/14). ¡RX/TX POR CONFIRMAR con el cableado! Orange Pi TX (pin 8) → PIN_OPI_RX; Orange Pi RX (pin 10) ← PIN_OPI_TX.
#define PIN_OPI_TX      13
#define PIN_OPI_RX      14
#define SPM_UART_OPI_ACTIVA        1       // 0 = sin canal UART (solo USB)
#define SPM_UART_CONFIRMA_CLASIFICAR 0     // 1 = envía un evento al terminar `clasificar`. NO existe en shared/: la Orange Pi actual no lo espera (ver PROPUESTA_trayectorias_spm.md)
#define SPM_UART_ERRORES           0       // 1 = también por UART los rechazos (hoy solo por USB: el protocolo no define evento de error)
#ifndef EVENTO_CLASIFICADO
#define EVENTO_CLASIFICADO "clasificado"   // PROPUESTA (no existe en shared/protocol_constants.*): campo "destino"
#endif
static const uint8_t PIN_NSLEEP = PIN_NONE;                       // nSLEEP del DRV8833 si lo controla el firmware (PIN_NONE = cableado a 3.3 V)

// ═══════════════════════════════════════════════════════════════════════════════
//  2. PARÁMETROS DE TIEMPO REAL Y PERIFÉRICOS
// ═══════════════════════════════════════════════════════════════════════════════
static const uint32_t CONTROL_HZ   = 1000;                      // lazo de control (timer de hardware)
static const float    TS_NOM       = 1.0f / CONTROL_HZ;
static const uint32_t PWM_FREQ_HZ  = 20000;
static const uint8_t  PWM_BITS     = 10;
static const int16_t  PWM_MAX      = (1 << PWM_BITS) - 1;
static const int      PCNT_LIM     = 30000;                     // límite del contador de 16 bits; el desbordamiento se resuelve por muestreo
static const uint32_t ENC_GLITCH_NS = 1000;                     // filtro antirrebote del PCNT (ns)
static const bool     ENC_PULLUP   = true;                      // encoders Hall de colector abierto → pull-up interno
static const uint32_t BAUD         = 115200;
static const float    VEL_ALPHA    = 0.15f;                     // filtro exponencial de la velocidad medida (≈ 25 Hz a 1 kHz)

// ═══════════════════════════════════════════════════════════════════════════════
//  3. CONFIGURACIÓN (ajustable en vivo con {"cfg":...} y guardable en memoria no volátil)
//     Las ganancias están en unidades físicas del eje del motor: ángulos en grados, salida en fracción de ciclo de trabajo (-1..1).
//     ¡Son valores iniciales conservadores, NO sintonizados! Ver README_firmware.md (sintonía paso a paso).
// ═══════════════════════════════════════════════════════════════════════════════
enum CtrlType : uint8_t { CT_PID = 0, CT_CASC = 1, CT_OPEN = 2 };
struct MotorCfg {
    float   ppr;          // cuentas (X4) por vuelta del eje de salida del motor: DEBE coincidir con «pulsos/vuelta» de la interfaz
    int8_t  enc_sign;     // +1/-1: signo del encoder para que PWM positivo → cuentas crecientes
    int8_t  pwm_sign;     // +1/-1: intercambia RPWM/LPWM (inversión del sentido sin recablear)
    uint8_t ctrl;         // CtrlType
    float   kp, ki, kd;   // PID:  duty/°, duty/(°·s), duty/(°/s)
    float   kff;          // PID:  avance de velocidad de referencia, duty/(°/s)
    float   kpp;          // cascada: lazo externo de posición, (°/s)/°
    float   kpv, kiv;     // cascada: PI de velocidad, duty/(°/s) y duty/(°)
    float   kffv;         // cascada: avance de la velocidad pedida, duty/(°/s)
    float   vmax, amax;   // perfil: °/s y °/s²
    float   pwm_min;      // fracción mínima que vence la fricción estática (0..0.5)
    float   pwm_max;      // saturación de la salida (0..1)
    float   tol;          // ° — «ok» cuando |objetivo - posición| < tol
    float   band;         // ° — banda muerta de parada (0 = sin banda)
    float   limit;        // ° — límite absoluto de las consignas (|consigna| ≤ limit)
};
static const MotorCfg CFG_DEFECTO = { 1000.0f, 1, 1, CT_PID, 0.020f, 0.006f, 0.0010f, 0.0012f, 8.0f, 0.0020f, 0.0200f, 0.0012f,
                                      600.0f, 3000.0f, 0.10f, 0.60f, 1.0f, 0.2f, 1500.0f };
struct Config {
    uint32_t magic;       // detecta datos válidos en la NVS
    MotorCfg m[N_MOT];
    uint16_t telem_hz;    // telemetría
    uint16_t fault_ms;    // ventana de detección de motor desbocado/atascado
};
static const uint32_t CFG_MAGIC = 0x53504D01;                   // «SPM» v1
static Config g_cfg;

// ═══════════════════════════════════════════════════════════════════════════════
//  4. ESTADO COMPARTIDO (el spinlock protege los campos que escriben varias tareas; secciones críticas muy cortas)
// ═══════════════════════════════════════════════════════════════════════════════
enum State : uint8_t { ST_IDLE = 0, ST_RUN = 1, ST_FAULT = 2, ST_TEST = 3, ST_CAPTURE = 4 };
static const char *STATE_NAME[] = { "idle", "run", "fault", "test", "capture" };

struct Cmd {                      // consignas que llegan por serie (las consume la tarea de control)
    float    target_deg;          // °
    bool     fresh;               // consigna nueva sin procesar
    int64_t  t_arrival_us;
    int64_t  t_prev_us;           // llegada de la consigna anterior (para detectar el streaming)
    float    duty_open;           // lazo abierto, -1..1
};
struct Telemetry {                // instantánea para la telemetría (la escribe la tarea de control)
    int32_t pos, target, ref;     // cuentas
    float   e_deg;                // error de seguimiento al objetivo
    int16_t duty;                 // ‰ con signo
    bool    ok;
};

static portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile State g_state = ST_RUN;
static Cmd g_cmd[N_MOT];
static Telemetry g_tel[N_MOT];
static volatile uint32_t g_miss = 0;                  // ciclos de control perdidos (la tarea llegó tarde)
static volatile uint32_t g_jit_us = 0;                // máximo |periodo real - nominal| en la última ventana (µs)
static volatile uint8_t  g_fault_mask = 0;            // bit i = motor i en falla
static volatile bool g_zero_req[N_MOT];
static volatile bool g_reset_req = false;
static float g_test_duty[N_MOT];                      // ciclo de trabajo impuesto en ST_TEST (prueba de polaridad)

// Captura para identificación de la planta (ver sección 6b): ciclo de trabajo en lazo abierto (escalón, rampa o triángulo) sobre UN motor mientras se
// registran tiempo real, posición y ciclo de trabajo a 1 kHz (o decimado). Los datos quedan en RAM hasta que se piden con {"cmd":"dump"}.
static const int CAP_MAX = 4000;                      // muestras (40 KB de RAM)
enum CapMode : uint8_t { CAP_STEP = 0, CAP_RAMP = 1, CAP_TRI = 2 };
static const char *CAP_MODE_NAME[] = { "step", "ramp", "tri" };
struct CapReq {                                       // petición que llega por serie; la toma la tarea de control
    bool pending, hold;
    uint8_t motor, mode;
    float duty, max_deg;
    uint32_t pre_ms, ms, post_ms;
    uint16_t dec;
};
struct CapRun {                                       // captura en curso o terminada (los datos siguen siendo válidos hasta la siguiente)
    bool active, finished, done_msg, hold;
    uint8_t motor, mode, abort_why;                   // abort_why: 0 completa · 1 superó max_deg · 2 stop/reset · 3 falla
    float duty, max_deg, t, t_pre, t_dur, t_post;
    uint32_t pre_ms, ms, post_ms;
    uint16_t dec, dec_cnt, n;
    int32_t pos0;
};
static CapReq g_cap_req;
static CapRun g_cap;
static uint32_t g_cap_t_us[CAP_MAX];
static int32_t  g_cap_pos[CAP_MAX];
static int16_t  g_cap_duty[CAP_MAX];                  // ‰ con signo
static volatile bool g_dumping = false;               // mientras se envían los datos, la telemetría calla

// ═══════════════════════════════════════════════════════════════════════════════
//  5. GANCHOS DE HARDWARE (en el PC los define el banco de pruebas)
// ═══════════════════════════════════════════════════════════════════════════════
#ifdef HOST_TEST
int32_t hwReadRaw(int i);                             // cuenta cruda del contador de 16 bits (con el desbordamiento que se quiera simular)
void    hwWritePwm(int i, int16_t duty);              // ciclo de trabajo con signo (-PWM_MAX..PWM_MAX)
void    hwSend(const char *s, int n);                 // línea hacia la interfaz
int64_t hwNowUs();
void    hwSendUart(const char *s, int n);             // línea hacia la Orange Pi (canal UART)
#else
static pcnt_unit_handle_t g_pcnt[N_MOT];
static hw_timer_t *g_timer = NULL;
static TaskHandle_t g_ctrlTask = NULL;
static SemaphoreHandle_t g_txMutex;
static Preferences g_prefs;

static void setupEncoder(int i) {
    if (ENC_PULLUP) { pinMode(PINS[i].encA, INPUT_PULLUP); pinMode(PINS[i].encB, INPUT_PULLUP); }
    pcnt_unit_config_t uc = {};
    uc.low_limit = -PCNT_LIM; uc.high_limit = PCNT_LIM;
    pcnt_new_unit(&uc, &g_pcnt[i]);
    pcnt_glitch_filter_config_t fc = {};
    fc.max_glitch_ns = ENC_GLITCH_NS;
    pcnt_unit_set_glitch_filter(g_pcnt[i], &fc);
    pcnt_chan_config_t ca = {}; ca.edge_gpio_num = PINS[i].encA; ca.level_gpio_num = PINS[i].encB;
    pcnt_chan_config_t cb = {}; cb.edge_gpio_num = PINS[i].encB; cb.level_gpio_num = PINS[i].encA;
    pcnt_channel_handle_t chA, chB;
    pcnt_new_channel(g_pcnt[i], &ca, &chA);
    pcnt_new_channel(g_pcnt[i], &cb, &chB);
    // decodificación en cuadratura X4: cada flanco de A y de B suma o resta según el nivel del otro canal
    pcnt_channel_set_edge_action(chA, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
    pcnt_channel_set_level_action(chA, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_channel_set_edge_action(chB, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(chB, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_unit_enable(g_pcnt[i]);
    pcnt_unit_clear_count(g_pcnt[i]);
    pcnt_unit_start(g_pcnt[i]);
}
int32_t hwReadRaw(int i) { int c = 0; pcnt_unit_get_count(g_pcnt[i], &c); return c; }

static void setupPwm(int i) {
    ledcAttach(PINS[i].pwmF, PWM_FREQ_HZ, PWM_BITS);
    ledcAttach(PINS[i].pwmR, PWM_FREQ_HZ, PWM_BITS);
    ledcWrite(PINS[i].pwmF, 0); ledcWrite(PINS[i].pwmR, 0);
    if (PINS[i].en != PIN_NONE) { pinMode(PINS[i].en, OUTPUT); digitalWrite(PINS[i].en, HIGH); }   // habilitación del driver (el DRV8833 no la usa)
}
void hwWritePwm(int i, int16_t duty) {                // DRV8833: ambas entradas a 0 = rueda libre
    if (duty > PWM_MAX) duty = PWM_MAX;
    if (duty < -PWM_MAX) duty = -PWM_MAX;
    if (DRIVER_DECAIMIENTO_LENTO && duty != 0) {      // DRV8833 en decaimiento lento (ver DRIVER_DECAIMIENTO_LENTO); duty == 0 sigue siendo «libre»
        const int a = abs(duty);
        ledcWrite(duty > 0 ? PINS[i].pwmF : PINS[i].pwmR, PWM_MAX);        // entrada de dirección fija en alto
        ledcWrite(duty > 0 ? PINS[i].pwmR : PINS[i].pwmF, PWM_MAX - a);    // la otra: en alto = frenar, en bajo = mover (fracción `duty`)
        return;
    }
    ledcWrite(PINS[i].pwmF, duty > 0 ? duty : 0);
    ledcWrite(PINS[i].pwmR, duty < 0 ? -duty : 0);
}
int64_t hwNowUs() { return esp_timer_get_time(); }
void hwSend(const char *s, int n) {
    xSemaphoreTake(g_txMutex, portMAX_DELAY);
    Serial.write((const uint8_t *)s, n);
    xSemaphoreGive(g_txMutex);
}
void hwSendUart(const char *s, int n) {
#if SPM_UART_OPI_ACTIVA
    xSemaphoreTake(g_txMutex, portMAX_DELAY);
    Serial2.write((const uint8_t *)s, n);
    xSemaphoreGive(g_txMutex);
#else
    (void)s; (void)n;
#endif
}
#endif

static void sendf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void sendf(const char *fmt, ...) {
    char buf[420];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n';
    hwSend(buf, n);
}

// ═══════════════════════════════════════════════════════════════════════════════
//  6. NÚCLEO DE CONTROL (sin dependencias de hardware: es lo que prueba el banco de pruebas del PC)
// ═══════════════════════════════════════════════════════════════════════════════
struct MotorRT {
    // medición
    int32_t raw_prev;  bool raw_init;
    int32_t pos;                    // cuentas acumuladas (con enc_sign)
    int32_t pos_offset;             // para «zero»
    float   vel;                    // cuentas/s (filtrada)
    // referencia
    float   r, rv;                  // cuentas, cuentas/s
    float   target;                 // cuentas
    bool    stream;  float seg_left;   // rampa entre consignas: tiempo restante del tramo (s)
    // control
    float   integ, integ_v;         // acumuladores (duty)
    float   u;                      // salida -1..1
    float   dpc;                    // grados por cuenta
    // vigilancia
    uint32_t win_ms, win_hi;  float win_vs;           // ventana de vigilancia: tiempo, tiempo con esfuerzo alto, suma de (velocidad · signo de la salida)
};
static MotorRT g_rt[N_MOT];

static inline float clampf(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }
static inline float signf(float x) { return x < 0 ? -1.0f : 1.0f; }

// Resuelve el desbordamiento del contador de 16 bits por muestreo: entre dos lecturas separadas 1 ms el avance real es minúsculo, así que
// de {d, d±30000, d±60000} (reinicio a 0 en el límite, o salto al límite opuesto) se elige el de menor módulo.
int32_t unwrapDelta(int32_t d) {
    int32_t best = d;
    for (int k = -2; k <= 2; k++) {
        int32_t c = d + k * PCNT_LIM;
        if (abs(c) < abs(best)) best = c;
    }
    return best;
}

void applyConfigDerived() {
    for (int i = 0; i < N_MOT; i++) g_rt[i].dpc = 360.0f / g_cfg.m[i].ppr;
}

void resetMotor(int i, bool keep_position = true) {
    MotorRT &m = g_rt[i];
    m.integ = m.integ_v = 0; m.u = 0; m.vel = 0; m.rv = 0; m.stream = false; m.seg_left = 0;
    if (keep_position) { m.r = (float)m.pos; m.target = (float)m.pos; }
    m.win_ms = m.win_hi = 0; m.win_vs = 0;
    portENTER_CRITICAL(&g_mux);
    g_cmd[i].fresh = false; g_cmd[i].target_deg = m.pos * m.dpc; g_cmd[i].duty_open = 0;
    portEXIT_CRITICAL(&g_mux);
}

// Genera la referencia (r, rv) para el motor i. Dos modos:
//  · streaming: una consigna que llega < 250 ms después de la anterior se alcanza con una rampa lineal en ese mismo intervalo (la trayectoria
//    planificada se reproduce exacta con un retraso de un periodo de la interfaz, sin escalones);
//  · perfil: movimiento suelto con velocidad y aceleración limitadas y frenado por raíz cuadrada (sin sobreimpulso).
void referenceStep(int i, float dt) {
    MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
    const float cpd = 1.0f / m.dpc;
    float vmaxc = c.vmax * cpd, amaxc = c.amax * cpd;
    if (m.stream) {
        m.r += m.rv * dt; m.seg_left -= dt;
        if (m.seg_left <= 0) { m.r = m.target; m.rv = 0; m.stream = false; }
        return;
    }
    float e = m.target - m.r, ae = fabsf(e);
    const float K = 25.0f;                                // zona lineal cerca del objetivo (1/s); empalma con la parábola de frenado sin exigir más que amax
    float lim = amaxc / (K * K);
    float vdes = (ae > lim) ? sqrtf(2.0f * amaxc * (ae - 0.5f * lim)) : K * ae;
    vdes = fminf(vdes, vmaxc) * signf(e);
    float dv = clampf(vdes - m.rv, -amaxc * dt, amaxc * dt);
    m.rv += dv; m.r += m.rv * dt;
    if ((m.target - m.r) * e < 0) { m.r = m.target; m.rv = 0; }                 // cruzó el objetivo en este paso
    if (fabsf(m.target - m.r) < 0.02f * cpd && fabsf(m.rv) < 0.5f * cpd) { m.r = m.target; m.rv = 0; }
}

// Una consigna nueva (ya en cuentas) → decide streaming o perfil.
void applyNewTarget(int i, float target_counts, int64_t t_now_us, int64_t t_prev_us) {
    MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
    m.target = target_counts;
    float gap = (t_prev_us > 0) ? (t_now_us - t_prev_us) * 1e-6f : 1e9f;
    if (gap > 0.005f && gap < 0.25f) {
        float T = clampf(gap, 0.010f, 0.25f);
        float rv = (m.target - m.r) / T;
        if (fabsf(rv * m.dpc) <= 1.5f * c.vmax) { m.rv = rv; m.seg_left = T; m.stream = true; return; }   // demasiado rápida → perfil con límites
    }
    m.stream = false;
}

// Lazo discreto de un motor: devuelve la salida en [-pwm_max, pwm_max]. Unidades físicas (°, °/s).
float controlStep(int i, float dt) {
    MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
    float e = (m.r - m.pos) * m.dpc;                    // error de seguimiento (°)
    float v = m.vel * m.dpc;                            // velocidad medida (°/s)
    float rv = m.rv * m.dpc;                            // velocidad de referencia (°/s)
    float u = 0;

    if (c.ctrl == CT_OPEN) {
        portENTER_CRITICAL(&g_mux); u = g_cmd[i].duty_open; portEXIT_CRITICAL(&g_mux);
        return clampf(u, -c.pwm_max, c.pwm_max);
    }
    float u_raw;
    if (c.ctrl == CT_PID) {
        u_raw = c.kp * e + m.integ + c.kd * (rv - v) + c.kff * rv;        // derivada sobre el error de velocidad
        float us = clampf(u_raw, -c.pwm_max, c.pwm_max);
        bool wind = (us != u_raw) && (signf(e) == signf(u_raw));         // anti-windup: no integrar si satura empujando más
        if (!wind && fabsf(e) < 10.0f) m.integ = clampf(m.integ + c.ki * e * dt, -c.pwm_max, c.pwm_max);   // zona de integración: no acumula en movimientos grandes
    } else {                                              // cascada: P de posición → velocidad pedida → PI de velocidad
        float vcmd = c.kpp * e + rv;
        float ev = vcmd - v;
        u_raw = c.kpv * ev + m.integ_v + c.kffv * vcmd;
        float us = clampf(u_raw, -c.pwm_max, c.pwm_max);
        bool wind = (us != u_raw) && (signf(ev) == signf(u_raw));
        if (!wind) m.integ_v = clampf(m.integ_v + c.kiv * ev * dt, -c.pwm_max, c.pwm_max);
    }
    u = clampf(u_raw, -c.pwm_max, c.pwm_max);
    if (c.band > 0 && fabsf(e) < c.band && fabsf(rv) < 1.0f && fabsf(v) < 5.0f) {   // banda de parada: sin cazar el último pulso
        u = 0; m.integ *= 0.99f; m.integ_v *= 0.99f;
    } else if (fabsf(u) > 0.002f && c.pwm_min > 0) {      // compensación de fricción estática: salto mínimo
        u = signf(u) * (c.pwm_min + (1.0f - c.pwm_min) * fabsf(u));
        u = clampf(u, -c.pwm_max, c.pwm_max);
    }
    return u;
}

// Vigilancia de motor desbocado/atascado en ventanas de fault_ms. Solo cuenta el tiempo con esfuerzo alto (|u| > 50 % del máximo) y mira si el motor
// se mueve en el sentido que se le ordena:
//  · desbocado: casi todo el tiempo con esfuerzo alto y velocidad media CONTRARIA a la orden (< -2 °/s) → polaridad del encoder/PWM invertida
//    (el lazo cerrado es realimentación positiva) → se corta antes de que el mecanismo golpee;
//  · atascado: esfuerzo alto casi todo el tiempo, error > 3° y sin movimiento (|v| medio < 1 °/s) → motor sin energía, encoder desconectado o bloqueo.
bool faultCheck(int i, float dt) {
    MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
    if (c.ctrl == CT_OPEN) return false;
    uint32_t ms = (uint32_t)(dt * 1000.0f + 0.5f);
    m.win_ms += ms;
    if (fabsf(m.u) > 0.5f * c.pwm_max) { m.win_hi += ms; m.win_vs += (m.vel * m.dpc) * signf(m.u) * ms; }
    if (m.win_ms < g_cfg.fault_ms) return false;
    bool fault = false;
    if (m.win_hi >= (uint32_t)(0.8f * m.win_ms)) {
        float vmean = m.win_vs / (float)m.win_hi;                        // °/s en el sentido de la orden
        float e = fabsf((m.r - m.pos) * m.dpc);
        fault = (vmean < -2.0f) || (e > 3.0f && fabsf(vmean) < 1.0f);
    }
    m.win_ms = m.win_hi = 0; m.win_vs = 0;
    return fault;
}

// ═══════════════════════════════════════════════════════════════════════════════
//  6b. CAPTURA PARA IDENTIFICACIÓN DE LA PLANTA
//  Aplica un ciclo de trabajo en lazo abierto a UN motor (sin compensación de fricción ni lazo: se identifica el motor + driver + carga tal cual) y
//  registra (tiempo real, posición, ciclo de trabajo). Perfiles: escalón · rampa 0→duty · triángulo 0→duty→0 (la rampa da la zona muerta y la fricción).
//  Los demás motores quedan libres (o sosteniendo su posición con el lazo normal si hold=1). Termina sola, por max_deg, por stop/reset o por falla.
// ═══════════════════════════════════════════════════════════════════════════════
static void captureBegin(State &st) {
    CapReq r;
    portENTER_CRITICAL(&g_mux); r = g_cap_req; g_cap_req.pending = false; portEXIT_CRITICAL(&g_mux);
    if (st != ST_RUN && st != ST_IDLE) return;                       // falla o prueba de polaridad en curso: se ignora la petición
    memset(&g_cap, 0, sizeof(g_cap));
    g_cap.motor = r.motor; g_cap.mode = r.mode; g_cap.hold = r.hold; g_cap.duty = r.duty; g_cap.max_deg = r.max_deg;
    g_cap.pre_ms = r.pre_ms; g_cap.ms = r.ms; g_cap.post_ms = r.post_ms; g_cap.dec = r.dec;
    g_cap.t_pre = r.pre_ms * 1e-3f; g_cap.t_dur = r.ms * 1e-3f; g_cap.t_post = r.post_ms * 1e-3f;
    g_cap.pos0 = g_rt[r.motor].pos;
    for (int i = 0; i < N_MOT; i++) resetMotor(i, true);             // los motores que sostienen parten con objetivo = posición actual
    g_cap.active = true;
    g_state = st = ST_CAPTURE;
}

// Ciclo de trabajo del motor capturado en este instante (-1..1) y registro de la muestra. 'dt' es el periodo real del ciclo.
static float captureStep(int i, float dt) {
    CapRun &c = g_cap; const MotorRT &m = g_rt[i]; const MotorCfg &cfg = g_cfg.m[i];
    float u = 0, t1 = c.t - c.t_pre;
    if (t1 >= 0 && t1 < c.t_dur) {
        float tau = t1 / c.t_dur;
        float f = (c.mode == CAP_STEP) ? 1.0f : (c.mode == CAP_RAMP ? tau : 1.0f - fabsf(2.0f * tau - 1.0f));
        u = c.duty * f;
    }
    u = clampf(u, -cfg.pwm_max, cfg.pwm_max);                        // el tope de seguridad del motor también rige en la captura
    if (c.n < CAP_MAX && ++c.dec_cnt >= c.dec) {
        c.dec_cnt = 0;
        g_cap_t_us[c.n] = (uint32_t)(c.t * 1e6f); g_cap_pos[c.n] = m.pos; g_cap_duty[c.n] = (int16_t)lroundf(u * 1000.0f);
        c.n++;
    }
    c.t += dt;
    if (fabsf((float)(m.pos - c.pos0) * m.dpc) > c.max_deg) { c.abort_why = 1; c.finished = true; }
    else if (c.t >= c.t_pre + c.t_dur + c.t_post) c.finished = true;
    return u;
}

static void captureFinish(uint8_t why) {
    g_cap.active = false; g_cap.finished = true; g_cap.done_msg = true;
    if (why) g_cap.abort_why = why;
    hwWritePwm(g_cap.motor, 0);
    if (g_state == ST_CAPTURE) {                                     // fin normal o por max_deg: con hold se vuelve a sostener; sin hold, motores libres
        if (g_cap.hold) { for (int i = 0; i < N_MOT; i++) resetMotor(i, true); g_state = ST_RUN; }
        else g_state = ST_IDLE;
    }                                                                // si el estado ya cambió (stop → IDLE, falla → FAULT) se respeta
}

// ═══════════════════════════════════════════════════════════════════════════════
//  6c. TRAYECTORIAS SMARTPHARMA (cámara y clasificación)
//  Cada plan es una lista de tramos q0 → q1 (grados de motor, 0 = home) con perfil quíntico y una espera al final (estabilización). La referencia (posición y
//  velocidad) se la entrega al mismo lazo de control de siempre. El plan NO avanza solo entre órdenes: un `girar_posicion` ejecuta UN tramo y confirma.
//  Una trayectoria en curso rechaza cualquier otra orden de movimiento; `stop`/`reset`/falla la abortan (los motores quedan donde están: no se reanuda sola).
// ═══════════════════════════════════════════════════════════════════════════════
enum TrajKind : uint8_t { TK_CAM = 1, TK_CLAS = 2, TK_HOME = 3 };
static const float SPM_CONV_DEG = 1.0f;                // al terminar el último tramo se espera a que todos los motores estén a menos de esto de su destino
static const float SPM_HOME_VMAX_DPS = 100.0f;         // `home` de recuperación: velocidad media máxima de los motores
static volatile bool    g_tr_active = false;
static volatile uint8_t g_tr_event = 0;                // 0 nada · 1 terminó · 2 abortada (lo envía trajService, nunca el lazo de control)
static const SpmLeg *g_tr_legs = NULL;
static uint8_t g_tr_n = 0, g_tr_idx = 0;
static float   g_tr_t = 0;                             // s dentro del tramo actual
static float   g_tr_q[N_MOT], g_tr_qd[N_MOT];          // referencia actual (° y °/s del eje de salida)
static uint8_t g_tr_kind = 0, g_tr_chan = 0;           // chan: 0 USB · 1 UART (a quién se confirma)
static int     g_tr_cara = 0;
static char    g_tr_dest[16] = "";
static SpmLeg  g_home_leg;                             // tramo de recuperación (pose actual → home)
static uint8_t g_descarte = SPM_DESCARTE_VARIANTE_DEFECTO;   // 1 = DESCARTE como TIPO_A · 2 = espera SPM_DESCARTE2_ESPERA_S y home

static void trajEnd(uint8_t ev) { g_tr_active = false; g_tr_event = ev; }

static void trajAdvance(float dt) {
    g_tr_t += dt;
    for (;;) {
        const SpmLeg &L = g_tr_legs[g_tr_idx];
        const float tot = L.t_mov + L.t_hold;
        if (g_tr_t < tot || g_tr_idx >= g_tr_n - 1) break;
        g_tr_t -= tot; g_tr_idx++;
    }
    const SpmLeg &L = g_tr_legs[g_tr_idx];
    float s = 1.0f, ds = 0.0f;
    if (L.t_mov > 0 && g_tr_t < L.t_mov) {
        const float x = g_tr_t / L.t_mov, x2 = x * x;
        s = x2 * x * (10.0f - 15.0f * x + 6.0f * x2);
        ds = 30.0f * x2 * (1.0f - x) * (1.0f - x) / L.t_mov;
    }
    for (int i = 0; i < N_MOT; i++) { g_tr_q[i] = L.q0[i] + (L.q1[i] - L.q0[i]) * s; g_tr_qd[i] = (L.q1[i] - L.q0[i]) * ds; }
    if (g_tr_idx == g_tr_n - 1 && g_tr_t >= L.t_mov + L.t_hold) {      // plan cumplido: se confirma cuando los motores ya están en su sitio
        float e = 0;
        for (int i = 0; i < N_MOT; i++) e = fmaxf(e, fabsf(L.q1[i] - g_rt[i].pos * g_rt[i].dpc));
        if (e < SPM_CONV_DEG) {
            for (int i = 0; i < N_MOT; i++) { g_rt[i].target = L.q1[i] / g_rt[i].dpc; g_rt[i].stream = false; }
            trajEnd(1);
        }
    }
}

static void trajRef(int i) {                           // referencia del motor i en cuentas (la usa controlStep igual que la del perfil normal)
    MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
    const float q = clampf(g_tr_q[i], -c.limit, c.limit);
    m.r = q / m.dpc; m.rv = g_tr_qd[i] / m.dpc; m.target = m.r; m.stream = false;
}

// Pose del plato según los encoders: 0 = home, 1..SPM_N_CARAS = cara k, -1 = ninguna (a menos de SPM_TOL_POSE_DEG de todos los motores).
static int trajPoseEtapa(float *dev_out = NULL) {
    int best = -1; float bd = 1e9f;
    for (int e = 0; e <= SPM_N_CARAS; e++) {
        float d = 0;
        for (int i = 0; i < N_MOT; i++) d = fmaxf(d, fabsf(SPM_POSE_Q[e][i] - g_rt[i].pos * g_rt[i].dpc));
        if (d < bd) { bd = d; best = e; }
    }
    if (dev_out) *dev_out = bd;
    return bd <= SPM_TOL_POSE_DEG ? best : -1;
}

// Rechazos: siempre por USB; por UART solo si SPM_UART_ERRORES (el protocolo no define evento de error: lo normal es que la Orange Pi agote su plazo).
static void trajError(uint8_t chan, const char *why, const char *extra = "") {
    sendf("{\"err\":\"traj\",\"why\":\"%s\"%s}", why, extra);
#if SPM_UART_ERRORES
    if (chan == 1) { char b[96]; int n = snprintf(b, sizeof(b), "{\"evento\":\"error\",\"codigo\":\"%s\"}", why); (void)n; String p(b); String l = empaquetarMensajeUart(p); hwSendUart(l.c_str(), (int)l.length()); }
#else
    (void)chan;
#endif
}

static bool trajStart(uint8_t kind, const SpmLeg *legs, uint8_t n, uint8_t chan, int cara, const char *dest, bool check_start = true) {
    if (g_tr_active)                                   { trajError(chan, "ocupado"); return false; }
    if (g_state == ST_FAULT)                           { trajError(chan, "falla", ",\"hint\":\"usa reset\""); return false; }
    if (g_state != ST_RUN || g_cap.active || g_cap_req.pending || g_dumping) { trajError(chan, "no_listo", ",\"hint\":\"motores libres o captura en curso: enable/reset\""); return false; }
    if (check_start) {
        float d = 0;
        for (int i = 0; i < N_MOT; i++) d = fmaxf(d, fabsf(legs[0].q0[i] - g_rt[i].pos * g_rt[i].dpc));
        if (d > SPM_TOL_POSE_DEG) { char x[96]; snprintf(x, sizeof(x), ",\"dev_deg\":%.1f,\"hint\":\"el plato no esta en la pose de partida (ppr, zero all en home o home)\"", d); trajError(chan, "pose_inicial", x); return false; }
    }
    portENTER_CRITICAL(&g_mux);
    g_tr_legs = legs; g_tr_n = n; g_tr_idx = 0; g_tr_t = 0; g_tr_kind = kind; g_tr_chan = chan; g_tr_cara = cara;
    strncpy(g_tr_dest, dest ? dest : "", sizeof(g_tr_dest) - 1); g_tr_dest[sizeof(g_tr_dest) - 1] = 0;
    for (int i = 0; i < N_MOT; i++) { g_tr_q[i] = legs[0].q0[i]; g_tr_qd[i] = 0; }
    g_tr_event = 0; g_tr_active = true;
    portEXIT_CRITICAL(&g_mux);
    return true;
}

static void sendUartEvent(const char *payload) {       // «<json>|<XOR>\n» con las funciones de shared/protocol_constants.h
    String l = empaquetarMensajeUart(String(payload));
    hwSendUart(l.c_str(), (int)l.length());
}

// Se llama desde la tarea de comandos (núcleo 0) y desde el banco de pruebas: envía lo que el lazo de control dejó pendiente.
void trajService() {
    uint8_t ev;
    portENTER_CRITICAL(&g_mux); ev = g_tr_event; g_tr_event = 0; portEXIT_CRITICAL(&g_mux);
    if (!ev) return;
    if (ev == 2) { sendf("{\"err\":\"traj\",\"why\":\"abortada\",\"hint\":\"stop/reset/falla: el plato queda donde esta; no se reanuda sola\"}"); return; }
    char b[96];
    if (g_tr_kind == TK_CAM) {
        sendf("{\"ack\":\"traj_done\",\"kind\":\"cam\",\"cara\":%d}", g_tr_cara);
        if (g_tr_chan == 1) { snprintf(b, sizeof(b), "{\"evento\":\"%s\",\"cara\":%d}", EVENTO_EN_POSICION, g_tr_cara); sendUartEvent(b); }
    } else if (g_tr_kind == TK_CLAS) {
        sendf("{\"ack\":\"traj_done\",\"kind\":\"clas\",\"destino\":\"%s\"}", g_tr_dest);
#if SPM_UART_CONFIRMA_CLASIFICAR
        if (g_tr_chan == 1) { snprintf(b, sizeof(b), "{\"evento\":\"%s\",\"destino\":\"%s\"}", EVENTO_CLASIFICADO, g_tr_dest); sendUartEvent(b); }
#endif
    } else sendf("{\"ack\":\"traj_done\",\"kind\":\"home\"}");
}

// Órdenes (las mismas para USB y UART). `cara` 1..SPM_N_CARAS; la 5.ª de CARAS_POR_OBJETO no está implementada y se rechaza.
static void doCamara(int cara, uint8_t chan) {
    if (g_tr_active) { trajError(chan, "ocupado"); return; }
    if (cara > SPM_N_CARAS && cara <= CARAS_POR_OBJETO) { trajError(chan, "cara_no_implementada", ",\"hint\":\"el firmware implementa 4 caras: usar --caras 4 en la Orange Pi\""); return; }
    if (cara < 1 || cara > SPM_N_CARAS) { trajError(chan, "cara_invalida"); return; }
    const int e = trajPoseEtapa();
    if (e == cara) {                                   // ya está en esa pose (p. ej. orden repetida): se confirma sin mover
        g_tr_kind = TK_CAM; g_tr_chan = chan; g_tr_cara = cara; g_tr_event = 1; return;
    }
    if (e != cara - 1) { char x[64]; snprintf(x, sizeof(x), ",\"pose_actual\":%d,\"cara\":%d", e, cara); trajError(chan, "fuera_de_secuencia", x); return; }
    trajStart(TK_CAM, SPM_CAM[cara - 1].legs, SPM_CAM[cara - 1].n, chan, cara, "");
}

static void doClasificar(const char *destino, uint8_t chan) {
    if (g_tr_active) { trajError(chan, "ocupado"); return; }
    int tipo = -1; bool desc2 = false;
    if (!strcmp(destino, DESTINO_DESCARTE)) { if (g_descarte == 2) desc2 = true; else tipo = 0; }
    else if (!strcmp(destino, "DESCARTE_1")) tipo = 0;
    else if (!strcmp(destino, "DESCARTE_2")) desc2 = true;
    else for (int k = 0; k < SPM_N_TIPOS; k++) if (!strcmp(destino, SPM_TIPOS[k])) tipo = k;
    if (tipo < 0 && !desc2) { trajError(chan, "destino_invalido"); return; }
    const int e = trajPoseEtapa();
    if (e < 0) { trajError(chan, "pose_desconocida", ",\"hint\":\"el plato no esta en home ni en una cara: usa home\""); return; }
    const SpmPlan &pl = desc2 ? SPM_DESC2[e] : SPM_CLAS[tipo][e];
    trajStart(TK_CLAS, pl.legs, pl.n, chan, 0, destino);
}

static void doHome(uint8_t chan) {                     // SOLO USB: recuperación tras un aborto (línea recta en el espacio de los motores: sin validar contra colisiones)
    if (g_tr_active) { trajError(chan, "ocupado"); return; }
    float dmax = 0;
    for (int i = 0; i < N_MOT; i++) { g_home_leg.q0[i] = g_rt[i].pos * g_rt[i].dpc; g_home_leg.q1[i] = 0; dmax = fmaxf(dmax, fabsf(g_home_leg.q0[i])); }
    g_home_leg.t_mov = fmaxf(3.0f, 1.875f * dmax / SPM_HOME_VMAX_DPS); g_home_leg.t_hold = 0.5f;
    trajStart(TK_HOME, &g_home_leg, 1, chan, 0, "", false);
}

// Un ciclo de control para todos los motores. 'dt' es el periodo REAL medido. Lo llama la tarea de control (o el banco de pruebas).
void controlCycle(float dt, int64_t now_us) {
    State st = g_state;
    for (int i = 0; i < N_MOT; i++) {
        MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
        // 1) muestreo del encoder (PCNT) con desbordamiento resuelto
        int32_t raw = hwReadRaw(i);
        if (!m.raw_init) { m.raw_prev = raw; m.raw_init = true; }
        int32_t d = unwrapDelta(raw - m.raw_prev);
        m.raw_prev = raw;
        if (g_zero_req[i]) {                                   // «zero»: la posición actual pasa a ser 0
            g_zero_req[i] = false; m.pos = 0; m.r = 0; m.target = 0; m.rv = 0; m.stream = false;
            portENTER_CRITICAL(&g_mux); g_cmd[i].fresh = false; g_cmd[i].target_deg = 0; portEXIT_CRITICAL(&g_mux);
        } else {
            m.pos += d * c.enc_sign;
        }
        float vinst = (float)(d * c.enc_sign) / dt;
        m.vel += VEL_ALPHA * (vinst - m.vel);
    }
    if (g_reset_req) {
        g_reset_req = false;
        for (int i = 0; i < N_MOT; i++) { resetMotor(i, true); }
        if (g_tr_active) trajEnd(2);
        g_fault_mask = 0; g_state = st = ST_RUN;
    }
    if (g_cap_req.pending) captureBegin(st);
    bool tr_on = false;                                      // trayectoria SmartPharma en curso: ella genera la referencia
    if (g_tr_active) {
        if (st != ST_RUN || g_fault_mask) trajEnd(2);        // stop, reset o falla: se aborta (los motores quedan donde están)
        else { trajAdvance(dt); tr_on = g_tr_active; }
    }
    for (int i = 0; i < N_MOT; i++) {
        MotorRT &m = g_rt[i]; const MotorCfg &c = g_cfg.m[i];
        const bool cap_motor = (st == ST_CAPTURE && g_cap.active && i == g_cap.motor);
        // 2) consigna nueva
        Cmd cm; bool fresh;
        portENTER_CRITICAL(&g_mux);
        fresh = g_cmd[i].fresh; cm = g_cmd[i]; g_cmd[i].fresh = false;
        portEXIT_CRITICAL(&g_mux);
        if (fresh && st == ST_RUN && !tr_on) {
            float t = clampf(cm.target_deg, -c.limit, c.limit);
            applyNewTarget(i, t / m.dpc, cm.t_arrival_us, cm.t_prev_us);
        }
        // 3) referencia + control
        float u = 0;
        if (st == ST_RUN || (st == ST_CAPTURE && !cap_motor && g_cap.hold)) {
            if (tr_on) trajRef(i); else referenceStep(i, dt);
            m.u = controlStep(i, dt);
            u = m.u;
            if (faultCheck(i, dt)) { g_fault_mask |= (1 << i); }
        } else if (cap_motor) {
            m.u = u = captureStep(i, dt);
        } else if (st == ST_TEST) {
            u = g_test_duty[i]; m.u = u;
        } else {
            m.u = 0; m.r = (float)m.pos; m.target = m.r; m.rv = 0; m.integ = m.integ_v = 0;       // IDLE/FAULT: motores libres, sigue midiendo
        }
        int16_t duty = (int16_t)lroundf(u * c.pwm_sign * PWM_MAX);
        hwWritePwm(i, duty);
        // 4) instantánea para la telemetría
        float et = (m.target - m.pos) * m.dpc;
        portENTER_CRITICAL(&g_mux);
        g_tel[i].pos = m.pos; g_tel[i].target = (int32_t)lroundf(m.target); g_tel[i].ref = (int32_t)lroundf(m.r);
        g_tel[i].e_deg = et; g_tel[i].duty = (int16_t)lroundf(u * 1000.0f);
        g_tel[i].ok = (fabsf(et) < c.tol) && !(g_fault_mask & (1 << i)) && st == ST_RUN;
        portEXIT_CRITICAL(&g_mux);
    }
    if (g_fault_mask && (st == ST_RUN || st == ST_CAPTURE)) {   // falla en cualquier motor → todos a 0 (el mecanismo es una cadena cerrada)
        g_state = ST_FAULT;
        for (int i = 0; i < N_MOT; i++) hwWritePwm(i, 0);
        sendf("{\"err\":\"fault\",\"mask\":%u,\"hint\":\"motor desbocado o atascado: polaridad del encoder/PWM, cableado, nSLEEP/alimentación VM del DRV8833; reset para continuar\"}", (unsigned)g_fault_mask);
    }
    if (g_cap.active) {                                    // cierre de la captura: terminó, la cortó el límite de giro, stop/reset o una falla
        uint8_t why = 0;
        if (g_state == ST_FAULT) why = 3; else if (g_state != ST_CAPTURE) why = 2;
        if (why || g_cap.finished) captureFinish(why);
    }
    (void)now_us;
}

// ═══════════════════════════════════════════════════════════════════════════════
void firmwareInit() {                                    // parte del arranque sin hardware (la usa setup() y el banco de pruebas)
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_cfg.magic = CFG_MAGIC; g_cfg.telem_hz = 20; g_cfg.fault_ms = 500;
    for (int i = 0; i < N_MOT; i++) g_cfg.m[i] = CFG_DEFECTO;
    memset(g_rt, 0, sizeof(g_rt)); memset(g_cmd, 0, sizeof(g_cmd)); memset(g_tel, 0, sizeof(g_tel));
    memset(&g_cap, 0, sizeof(g_cap)); memset(&g_cap_req, 0, sizeof(g_cap_req)); g_dumping = false;
    applyConfigDerived();
    g_state = ST_RUN; g_fault_mask = 0; g_miss = 0; g_jit_us = 0; g_reset_req = false;
    for (int i = 0; i < N_MOT; i++) g_zero_req[i] = false;
    g_tr_active = false; g_tr_event = 0; g_descarte = SPM_DESCARTE_VARIANTE_DEFECTO;
}

// ═══════════════════════════════════════════════════════════════════════════════
//  7. JSON MÍNIMO (objetos planos con números, textos y arreglos numéricos) Y COMANDOS
// ═══════════════════════════════════════════════════════════════════════════════

static const char *skipWs(const char *p) { while (*p == ' ' || *p == '\t' || *p == '\r') p++; return p; }

bool jsonParse(const char *s, JMsg &m) {
    m.n = 0;
    const char *p = skipWs(s);
    if (*p != '{') return false;
    p = skipWs(p + 1);
    if (*p == '}') return true;
    while (*p) {
        if (m.n >= 10) return false;
        JVal &v = m.v[m.n]; memset(&v, 0, sizeof(v));
        if (*p != '"') return false;
        const char *e = strchr(p + 1, '"'); if (!e) return false;
        size_t kl = (size_t)(e - p - 1); if (kl >= sizeof(v.key)) return false;
        memcpy(v.key, p + 1, kl);
        p = skipWs(e + 1); if (*p != ':') return false;
        p = skipWs(p + 1);
        if (*p == '"') {
            e = strchr(p + 1, '"'); if (!e) return false;
            size_t vl = (size_t)(e - p - 1); if (vl >= sizeof(v.str)) vl = sizeof(v.str) - 1;
            memcpy(v.str, p + 1, vl); v.type = 2; p = e + 1;
        } else if (*p == '[') {
            p = skipWs(p + 1); v.type = 3;
            while (*p && *p != ']') {
                char *end; float x = strtof(p, &end); if (end == p) return false;
                if (v.n_arr < 4) v.arr[v.n_arr++] = x;
                p = skipWs(end); if (*p == ',') p = skipWs(p + 1);
            }
            if (*p != ']') return false;
            p++;
        } else if (!strncmp(p, "true", 4))  { v.num = 1; v.type = 4; p += 4; }
        else if (!strncmp(p, "false", 5)) { v.num = 0; v.type = 4; p += 5; }
        else if (!strncmp(p, "null", 4))  { v.num = 0; v.type = 4; p += 4; }
        else {
            char *end; v.num = strtof(p, &end); if (end == p) return false;
            if (isnan(v.num) || isinf(v.num)) return false;
            v.type = 1; p = end;
        }
        m.n++;
        p = skipWs(p);
        if (*p == ',') { p = skipWs(p + 1); continue; }
        return *p == '}';
    }
    return false;
}
static const JVal *jget(const JMsg &m, const char *key) {
    for (int i = 0; i < m.n; i++) if (!strcmp(m.v[i].key, key)) return &m.v[i];
    return NULL;
}

static void setTarget(int i, float deg, int64_t now_us) {
    portENTER_CRITICAL(&g_mux);
    g_cmd[i].t_prev_us = g_cmd[i].t_arrival_us; g_cmd[i].t_arrival_us = now_us;
    g_cmd[i].target_deg = deg; g_cmd[i].fresh = true;
    portEXIT_CRITICAL(&g_mux);
}

static bool cfgSet(const char *name, int motor, const JVal *val) {
    int lo = 0, hi = N_MOT - 1;
    if (motor >= 1 && motor <= N_MOT) lo = hi = motor - 1;
    if (!strcmp(name, "descarte")) {                    // variante de DESCARTE: 1 (como TIPO_A) o 2 (espera y home); acepta 1/2 o "DESCARTE_1"/"DESCARTE_2"
        int v = (val->type == 2) ? (!strcmp(val->str, "DESCARTE_2") ? 2 : (!strcmp(val->str, "DESCARTE_1") ? 1 : 0)) : (int)val->num;
        if (v != 1 && v != 2) return false;
        g_descarte = (uint8_t)v; return true;
    }
    if (!strcmp(name, "telem_hz")) { g_cfg.telem_hz = (uint16_t)clampf(val->num, 1, 100); return true; }
    if (!strcmp(name, "fault_ms")) { g_cfg.fault_ms = (uint16_t)clampf(val->num, 100, 5000); return true; }
    for (int i = lo; i <= hi; i++) {
        MotorCfg &c = g_cfg.m[i];
        float x = (val->type == 3) ? (i < val->n_arr ? val->arr[i] : val->arr[val->n_arr - 1]) : val->num;   // un arreglo da un valor por motor
        if      (!strcmp(name, "ppr"))     { if (x < 10) return false; c.ppr = x; }
        else if (!strcmp(name, "enc_sign")) c.enc_sign = x < 0 ? -1 : 1;
        else if (!strcmp(name, "pwm_sign")) c.pwm_sign = x < 0 ? -1 : 1;
        else if (!strcmp(name, "ctrl"))    { if (val->type == 2) c.ctrl = !strcmp(val->str, "casc") ? CT_CASC : (!strcmp(val->str, "open") ? CT_OPEN : CT_PID); else c.ctrl = (uint8_t)clampf(x, 0, 2); }
        else if (!strcmp(name, "kp"))   c.kp = x;   else if (!strcmp(name, "ki"))   c.ki = x;
        else if (!strcmp(name, "kd"))   c.kd = x;   else if (!strcmp(name, "kff"))  c.kff = x;
        else if (!strcmp(name, "kpp"))  c.kpp = x;  else if (!strcmp(name, "kpv"))  c.kpv = x;
        else if (!strcmp(name, "kiv"))  c.kiv = x;  else if (!strcmp(name, "kffv")) c.kffv = x;
        else if (!strcmp(name, "vmax")) c.vmax = clampf(x, 1, 5000);
        else if (!strcmp(name, "amax")) c.amax = clampf(x, 10, 100000);
        else if (!strcmp(name, "pwm_min")) c.pwm_min = clampf(x, 0, 0.5f);
        else if (!strcmp(name, "pwm_max")) c.pwm_max = clampf(x, 0.05f, 1.0f);
        else if (!strcmp(name, "tol"))  c.tol = clampf(x, 0.05f, 45);
        else if (!strcmp(name, "band")) c.band = clampf(x, 0, 5);
        else if (!strcmp(name, "limit")) c.limit = clampf(x, 1, 100000);
        else return false;
        resetMotor(i, true);                               // al cambiar parámetros se reinician acumuladores
    }
    applyConfigDerived();
    return true;
}

static unsigned long g_uart_ok = 0, g_uart_err = 0;   // mensajes UART válidos / descartados (checksum o formato)

static void sendStatus() {
    sendf("{\"info\":\"status\",\"st\":\"%s\",\"fault\":%u,\"hz\":%u,\"telem_hz\":%u,\"ppr\":[%.0f,%.0f,%.0f],\"ctrl\":[%u,%u,%u],\"pwm_max\":[%.2f,%.2f,%.2f],\"enc_sign\":[%d,%d,%d],\"pose\":%d,\"traj\":%d,\"descarte\":%u,\"uart_ok\":%lu,\"uart_err\":%lu}",
          STATE_NAME[g_state], (unsigned)g_fault_mask, (unsigned)CONTROL_HZ, (unsigned)g_cfg.telem_hz,
          g_cfg.m[0].ppr, g_cfg.m[1].ppr, g_cfg.m[2].ppr, g_cfg.m[0].ctrl, g_cfg.m[1].ctrl, g_cfg.m[2].ctrl,
          g_cfg.m[0].pwm_max, g_cfg.m[1].pwm_max, g_cfg.m[2].pwm_max, g_cfg.m[0].enc_sign, g_cfg.m[1].enc_sign, g_cfg.m[2].enc_sign, trajPoseEtapa(), (int)g_tr_active, (unsigned)g_descarte,
          (unsigned long)g_uart_ok, (unsigned long)g_uart_err);
}

#ifdef HOST_TEST
bool nvsSave() { return true; }
bool nvsLoad() { return false; }
void polarityTest(bool apply) { (void)apply; }
#else
static bool nvsSave() {
    g_cfg.magic = CFG_MAGIC;
    g_prefs.begin("spm", false);
    size_t n = g_prefs.putBytes("cfg", &g_cfg, sizeof(g_cfg));
    g_prefs.putUChar("desc", g_descarte);               // variante de DESCARTE en su propia clave: no cambia el tamaño de Config (si no, se perdería lo ya guardado)
    g_prefs.end();
    return n == sizeof(g_cfg);
}
static bool nvsLoad() {
    Config tmp;
    g_prefs.begin("spm", true);
    size_t n = g_prefs.getBytes("cfg", &tmp, sizeof(tmp));
    uint8_t d = g_prefs.getUChar("desc", SPM_DESCARTE_VARIANTE_DEFECTO);
    g_prefs.end();
    if (d == 1 || d == 2) g_descarte = d;
    if (n != sizeof(tmp) || tmp.magic != CFG_MAGIC) return false;
    g_cfg = tmp; applyConfigDerived();
    return true;
}
// Prueba de polaridad: pulso corto de ciclo de trabajo positivo en cada motor por separado y se mira hacia dónde cuentan los encoders.
// Debe hacerse con el mecanismo libre y los motores en reposo (IDLE). Con apply=true corrige enc_sign si cuenta al revés.
static void polarityTest(bool apply) {
    if (g_state == ST_FAULT) { sendf("{\"err\":\"polarity\",\"hint\":\"primero reset\"}"); return; }
    State prev = g_state; g_state = ST_TEST;
    vTaskDelay(pdMS_TO_TICKS(30));
    for (int i = 0; i < N_MOT; i++) {
        for (int j = 0; j < N_MOT; j++) g_test_duty[j] = 0;
        int32_t p0 = g_rt[i].pos;
        g_test_duty[i] = 0.40f;
        vTaskDelay(pdMS_TO_TICKS(180));
        g_test_duty[i] = 0;
        vTaskDelay(pdMS_TO_TICKS(250));
        int32_t dp = g_rt[i].pos - p0;                         // con enc_sign actual
        const char *res = (abs(dp) < 3) ? "sin_movimiento" : (dp > 0 ? "ok" : "invertir_encoder");
        if (apply && dp < -2) g_cfg.m[i].enc_sign = (int8_t)(-g_cfg.m[i].enc_sign);
        sendf("{\"polarity\":%d,\"counts\":%ld,\"result\":\"%s\"}", i + 1, (long)dp, res);
    }
    g_reset_req = true; g_state = prev;
}
#endif

// {"cmd":"capture","motor":N,"duty":x,"ms":T[,"mode":"step|ramp|tri","pre_ms":P,"post_ms":Q,"dec":k,"max_deg":D,"hold":0|1]}
//   step: duty constante durante ms · ramp: 0→duty en ms · tri: 0→duty→0 en ms · pre_ms/post_ms: reposo antes/después (def. 100/500) ·
//   dec: se guarda 1 de cada k ciclos (def. 1 = 1 kHz) · max_deg: si el eje gira más que esto desde el inicio, se corta (def. 360°) ·
//   hold=1: los otros motores sostienen su posición con el lazo normal (def. 0: libres).
// El ciclo de trabajo se limita a pwm_max del motor. Con los datos listos se piden con {"cmd":"dump"}.
static void captureCommand(const JMsg &m) {
    if (g_tr_active) { trajError(0, "ocupado"); return; }
    if (g_state == ST_FAULT || g_state == ST_TEST) { sendf("{\"err\":\"capture\",\"hint\":\"primero reset\"}"); return; }
    if (g_cap.active || g_cap_req.pending || g_dumping) { sendf("{\"err\":\"capture\",\"hint\":\"ocupado\"}"); return; }
    const JVal *mo = jget(m, "motor"), *du = jget(m, "duty"), *ms = jget(m, "ms"), *md = jget(m, "mode");
    if (!mo || !du || !ms) { sendf("{\"err\":\"capture\",\"hint\":\"faltan motor, duty y ms\"}"); return; }
    int n = (int)mo->num;
    if (n < 1 || n > N_MOT) { sendf("{\"err\":\"capture\",\"hint\":\"motor 1..3\"}"); return; }
    CapReq r; memset(&r, 0, sizeof(r));
    r.motor = (uint8_t)(n - 1); r.duty = clampf(du->num, -1.0f, 1.0f); r.mode = CAP_STEP;
    if (md && md->type == 2) {
        if (!strcmp(md->str, "ramp")) r.mode = CAP_RAMP;
        else if (!strcmp(md->str, "tri")) r.mode = CAP_TRI;
        else if (strcmp(md->str, "step")) { sendf("{\"err\":\"capture\",\"hint\":\"mode: step, ramp o tri\"}"); return; }
    }
    const JVal *v;
    r.ms      = (uint32_t)clampf(ms->num, 50, 20000);
    r.pre_ms  = (v = jget(m, "pre_ms"))  ? (uint32_t)clampf(v->num, 0, 2000)   : 100;
    r.post_ms = (v = jget(m, "post_ms")) ? (uint32_t)clampf(v->num, 0, 3000)   : 500;
    r.dec     = (v = jget(m, "dec"))     ? (uint16_t)clampf(v->num, 1, 100)    : 1;
    r.max_deg = (v = jget(m, "max_deg")) ? clampf(v->num, 1, 3600)             : 360.0f;
    r.hold    = (v = jget(m, "hold"))    ? v->num != 0                         : false;
    uint32_t samples = (r.pre_ms + r.ms + r.post_ms) / r.dec + 2;
    if (samples > (uint32_t)CAP_MAX) { sendf("{\"err\":\"capture\",\"hint\":\"demasiadas muestras (%lu > %d): sube dec o baja ms\"}", (unsigned long)samples, CAP_MAX); return; }
    portENTER_CRITICAL(&g_mux); g_cap_req = r; g_cap_req.pending = true; portEXIT_CRITICAL(&g_mux);
    sendf("{\"ack\":\"capture\",\"motor\":%d,\"mode\":\"%s\",\"samples\":%lu}", n, CAP_MODE_NAME[r.mode], (unsigned long)samples);
}

// Envía los datos de la última captura: una línea de encabezado {"cap":{...}}, líneas {"c":[t_us,pos,duty‰, ...]} con 8 muestras cada una y {"ack":"dump"}.
// pos = cuentas con enc_sign; el ángulo es pos·dpc (° del eje de salida). La telemetría calla mientras dura el envío.
static void captureDump() {
    if (g_cap.active || g_cap_req.pending) { sendf("{\"err\":\"dump\",\"hint\":\"captura en curso\"}"); return; }
    if (g_cap.n == 0) { sendf("{\"err\":\"dump\",\"hint\":\"sin datos: usa capture primero\"}"); return; }
    g_dumping = true;
    const int n = g_cap.n; const MotorCfg &c = g_cfg.m[g_cap.motor];
    sendf("{\"cap\":{\"motor\":%d,\"mode\":\"%s\",\"n\":%d,\"dec\":%u,\"duty\":%.4f,\"pre_ms\":%lu,\"ms\":%lu,\"post_ms\":%lu,\"ppr\":%.1f,\"dpc\":%.6f,\"abort\":%u}}",
          g_cap.motor + 1, CAP_MODE_NAME[g_cap.mode], n, (unsigned)g_cap.dec, g_cap.duty, (unsigned long)g_cap.pre_ms, (unsigned long)g_cap.ms,
          (unsigned long)g_cap.post_ms, c.ppr, 360.0f / c.ppr, (unsigned)g_cap.abort_why);
    char line[300];
    for (int i = 0; i < n; i += 8) {
        int len = snprintf(line, sizeof(line), "{\"c\":[");
        for (int k = i; k < n && k < i + 8; k++)
            len += snprintf(line + len, sizeof(line) - len, "%s%lu,%ld,%d", k > i ? "," : "", (unsigned long)g_cap_t_us[k], (long)g_cap_pos[k], (int)g_cap_duty[k]);
        snprintf(line + len, sizeof(line) - len, "]}");
        sendf("%s", line);
    }
    sendf("{\"ack\":\"dump\",\"n\":%d}", n);
    g_dumping = false;
}

void handleLine(const char *line, int64_t now_us) {
    JMsg m;
    if (!jsonParse(line, m)) { sendf("{\"err\":\"json\"}"); return; }
    const JVal *cmd = jget(m, "cmd");
    if (cmd && cmd->type == 2) {
        const char *c = cmd->str;
        if (!strcmp(c, "reset"))        { g_reset_req = true; sendf("{\"ack\":\"reset\"}"); }
        else if (!strcmp(c, "stop"))    { g_state = ST_IDLE; for (int i = 0; i < N_MOT; i++) hwWritePwm(i, 0); sendf("{\"ack\":\"stop\"}"); }
        else if (!strcmp(c, "enable"))  { if (g_state == ST_FAULT) sendf("{\"err\":\"fault\",\"hint\":\"usa reset\"}"); else { g_reset_req = true; sendf("{\"ack\":\"enable\"}"); } }
        else if (!strcmp(c, "status"))  sendStatus();
        else if (!strcmp(c, "save"))    { if (nvsSave()) sendf("{\"ack\":\"save\"}"); else sendf("{\"err\":\"save\"}"); }
        else if (!strcmp(c, "load"))    { if (nvsLoad()) { g_reset_req = true; sendf("{\"ack\":\"load\"}"); } else sendf("{\"err\":\"load\",\"hint\":\"sin datos guardados\"}"); }
        else if (!strcmp(c, "defaults")) { if (g_tr_active) { trajError(0, "ocupado"); return; } for (int i = 0; i < N_MOT; i++) g_cfg.m[i] = CFG_DEFECTO; g_descarte = SPM_DESCARTE_VARIANTE_DEFECTO; applyConfigDerived(); g_reset_req = true; sendf("{\"ack\":\"defaults\"}"); }
        else if (!strcmp(c, "polarity")) { if (g_tr_active) { trajError(0, "ocupado"); return; } const JVal *a = jget(m, "apply"); polarityTest(a && a->num != 0); }
        else if (!strcmp(c, "capture"))  captureCommand(m);
        else if (!strcmp(c, "dump"))     captureDump();
        else if (!strcmp(c, "cam"))      { const JVal *a = jget(m, "cara"); doCamara(a ? (int)a->num : 0, 0); }
        else if (!strcmp(c, "clas"))     { const JVal *d = jget(m, "destino"); if (d && d->type == 2) doClasificar(d->str, 0); else trajError(0, "destino_invalido"); }
        else if (!strcmp(c, "home"))     doHome(0);
        else sendf("{\"err\":\"cmd\"}");
        return;
    }
    const JVal *cfg = jget(m, "cfg");
    if (cfg && cfg->type == 2) {
        const JVal *v = jget(m, "val"), *mo = jget(m, "motor");
        if (g_tr_active && strcmp(cfg->str, "descarte")) { trajError(0, "ocupado"); return; }
        if (!v) { sendf("{\"err\":\"cfg\",\"hint\":\"falta val\"}"); return; }
        if (cfgSet(cfg->str, mo ? (int)mo->num : 0, v)) sendf("{\"ack\":\"cfg\",\"name\":\"%s\"}", cfg->str); else sendf("{\"err\":\"cfg\",\"name\":\"%s\"}", cfg->str);
        return;
    }
    const JVal *z = jget(m, "zero");
    if (z) {
        if (g_tr_active) { trajError(0, "ocupado"); return; }
        if (z->type == 2) { for (int i = 0; i < N_MOT; i++) g_zero_req[i] = true; }
        else { int n = (int)z->num; if (n >= 1 && n <= N_MOT) g_zero_req[n - 1] = true; else { sendf("{\"err\":\"zero\"}"); return; } }
        sendf("{\"ack\":\"zero\"}"); return;
    }
    const JVal *du = jget(m, "duty");
    if (du) {                                              // lazo abierto
        if (g_tr_active) { trajError(0, "ocupado"); return; }       // lazo abierto para pruebas (identificación): {"duty":N,"val":x}
        const JVal *v = jget(m, "val"); int n = (int)du->num;
        if (!v || n < 1 || n > N_MOT) { sendf("{\"err\":\"duty\"}"); return; }
        portENTER_CRITICAL(&g_mux); g_cmd[n - 1].duty_open = clampf(v->num, -1, 1); portEXIT_CRITICAL(&g_mux);
        sendf("{\"ack\":\"duty\"}"); return;
    }
    const JVal *mo = jget(m, "motor"), *po = jget(m, "pos");
    if ((mo && po) || jget(m, "m1") || jget(m, "m2") || jget(m, "m3")) { if (g_tr_active) { trajError(0, "ocupado", ",\"hint\":\"trayectoria en curso: stop o reset para abortar\""); return; } }
    if (mo && po) {
        int n = (int)mo->num;
        if (n < 1 || n > N_MOT) { sendf("{\"err\":\"motor\"}"); return; }
        setTarget(n - 1, po->num, now_us); return;
    }
    bool any = false;
    static const char *K[N_MOT] = { "m1", "m2", "m3" };
    for (int i = 0; i < N_MOT; i++) { const JVal *v = jget(m, K[i]); if (v && v->type == 1) { setTarget(i, v->num, now_us); any = true; } }
    if (!any) sendf("{\"err\":\"unknown\"}");
}

// ═══════════════════════════════════════════════════════════════════════════════
//  7b. CANAL UART DE LA ORANGE PI  («<json>|<XOR hex>»; ver shared/protocol_constants.h)
//  Línea con checksum inválido o JSON ilegible: se descarta sin actuar (regla del repo). Acciones que este firmware aún no implementa (activar_dispensador,
//  alarma local, solicitar_telemetria: S-08, S-09, S-13) se ignoran. No hay ningún temporizador que reanude nada por cuenta propia.
// ═══════════════════════════════════════════════════════════════════════════════
void handleUartLine(const char *line) {
    const char *bar = strrchr(line, UART_SEPARADOR_CHECKSUM);
    if (!bar) { g_uart_err++; return; }
    char payload[200]; const size_t pl = (size_t)(bar - line);
    if (pl == 0 || pl >= sizeof(payload)) { g_uart_err++; return; }
    memcpy(payload, line, pl); payload[pl] = 0;
    char chk[8]; int k = 0;                                // checksum recibido: 2 dígitos hex (sin espacios ni \r)
    for (const char *q = bar + 1; *q && k < 7; q++) if (*q != ' ' && *q != '\r') chk[k++] = *q;
    chk[k] = 0;
    char esperado[4]; snprintf(esperado, sizeof(esperado), "%02X", calcularChecksumXor(String(payload)));
    if (k != 2 || strcasecmp(chk, esperado) != 0) { g_uart_err++; return; }
    JMsg m;
    if (!jsonParse(payload, m)) { g_uart_err++; return; }
    g_uart_ok++;
    const JVal *acc = jget(m, "accion");
    if (!acc || acc->type != 2) return;
    if (!strcmp(acc->str, ACCION_GIRAR_POSICION)) {
        const JVal *c = jget(m, "cara");
        if (!c || c->type != 1 || c->num != (float)(int)c->num) { trajError(1, "cara_invalida"); return; }
        doCamara((int)c->num, 1);
    } else if (!strcmp(acc->str, ACCION_CLASIFICAR)) {
        const JVal *d = jget(m, "destino");
        if (!d || d->type != 2) { trajError(1, "destino_invalido"); return; }
        doClasificar(d->str, 1);
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  8. TELEMETRÍA
// ═══════════════════════════════════════════════════════════════════════════════
void sendTelemetry(uint32_t t_ms) {
    if (g_cap.done_msg) {                                  // aviso de fin de captura: se envía desde aquí (tarea de telemetría), nunca desde el lazo de control
        g_cap.done_msg = false;
        sendf("{\"ack\":\"capture_done\",\"motor\":%d,\"n\":%u,\"abort\":%u}", g_cap.motor + 1, (unsigned)g_cap.n, (unsigned)g_cap.abort_why);
    }
    if (g_dumping) return;                                 // no mezclar la telemetría con los datos de la captura
    Telemetry t[N_MOT];
    portENTER_CRITICAL(&g_mux); memcpy(t, g_tel, sizeof(t)); portEXIT_CRITICAL(&g_mux);
    sendf("{\"t\":%lu,\"st\":\"%s\","
          "\"m1\":{\"posRef\":%ld,\"pos\":%ld,\"ok\":%s,\"u\":%d,\"e\":%.2f},"
          "\"m2\":{\"posRef\":%ld,\"pos\":%ld,\"ok\":%s,\"u\":%d,\"e\":%.2f},"
          "\"m3\":{\"posRef\":%ld,\"pos\":%ld,\"ok\":%s,\"u\":%d,\"e\":%.2f},\"jit\":%lu,\"miss\":%lu}",
          (unsigned long)t_ms, STATE_NAME[g_state],
          (long)t[0].target, (long)t[0].pos, t[0].ok ? "true" : "false", t[0].duty, t[0].e_deg,
          (long)t[1].target, (long)t[1].pos, t[1].ok ? "true" : "false", t[1].duty, t[1].e_deg,
          (long)t[2].target, (long)t[2].pos, t[2].ok ? "true" : "false", t[2].duty, t[2].e_deg,
          (unsigned long)g_jit_us, (unsigned long)g_miss);
}

// ═══════════════════════════════════════════════════════════════════════════════
//  9. TAREAS FreeRTOS, TIMER DE HARDWARE Y ARRANQUE (solo en la placa)
// ═══════════════════════════════════════════════════════════════════════════════
#ifndef HOST_TEST
void IRAM_ATTR onControlTimer() {                       // ISR del timer de hardware: solo despierta la tarea de control
    BaseType_t woken = pdFALSE;
    vTaskNotifyGiveFromISR(g_ctrlTask, &woken);
    portYIELD_FROM_ISR(woken);
}

static void taskControl(void *) {
    int64_t t_prev = hwNowUs();
    uint32_t jit_max = 0, n = 0;
    for (;;) {
        uint32_t pend = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(50));
        if (pend == 0) {                                // el timer no dispara: seguridad, motores a 0
            for (int i = 0; i < N_MOT; i++) hwWritePwm(i, 0);
            continue;
        }
        if (pend > 1) g_miss += pend - 1;               // ciclos perdidos (la tarea no alcanzó a correr)
        int64_t t = hwNowUs();                          // instante REAL del muestreo
        float dt = (t - t_prev) * 1e-6f; t_prev = t;
        if (dt < 0.2f * TS_NOM || dt > 5.0f * TS_NOM) dt = TS_NOM;
        uint32_t j = (uint32_t)fabsf((dt - TS_NOM) * 1e6f); if (j > jit_max) jit_max = j;
        if (++n >= CONTROL_HZ) { g_jit_us = jit_max; jit_max = 0; n = 0; }
        controlCycle(dt, t);
    }
}

static void taskTelemetry(void *) {
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        uint32_t hz = g_cfg.telem_hz; if (hz < 1) hz = 20;
        vTaskDelayUntil(&last, pdMS_TO_TICKS(1000 / hz));
        sendTelemetry((uint32_t)(hwNowUs() / 1000));
    }
}

static void taskCommands(void *) {
    char buf[240]; int n = 0;
    char ubuf[240]; int un = 0;                          // línea del UART de la Orange Pi
    for (;;) {
        int c = Serial.read();
        int u = SPM_UART_OPI_ACTIVA ? Serial2.read() : -1;
        trajService();                                   // confirmaciones pendientes de la trayectoria (el lazo de control no envía nada)
        if (c < 0 && u < 0) { vTaskDelay(pdMS_TO_TICKS(1)); continue; }
        if (c >= 0) {
            if (c == '\n') {
                buf[n] = 0;
                if (n > 0) handleLine(buf, hwNowUs());
                n = 0;
            } else if (n < (int)sizeof(buf) - 1) buf[n++] = (char)c;
            else n = 0;                                  // línea demasiado larga: se descarta
        }
        if (u >= 0) {
            if (u == '\n') {
                ubuf[un] = 0;
                if (un > 0) handleUartLine(ubuf);
                un = 0;
            } else if (un < (int)sizeof(ubuf) - 1) ubuf[un++] = (char)u;
            else { un = 0; g_uart_err++; }
        }
    }
}

void setup() {
    for (int i = 0; i < N_MOT; i++) { pinMode(PINS[i].pwmF, OUTPUT); digitalWrite(PINS[i].pwmF, LOW); pinMode(PINS[i].pwmR, OUTPUT); digitalWrite(PINS[i].pwmR, LOW); }
    firmwareInit();
    Serial.setRxBufferSize(1024);
    Serial.begin(BAUD);
#if SPM_UART_OPI_ACTIVA
    Serial2.setRxBufferSize(512);
    Serial2.begin(UART_BAUDRATE, SERIAL_8N1, PIN_OPI_RX, PIN_OPI_TX);   // enlace con la Orange Pi (115200, constante de shared/)
#endif
    delay(200);
    g_txMutex = xSemaphoreCreateMutex();
    nvsLoad();                                           // si hay configuración guardada, reemplaza a los valores por defecto
    applyConfigDerived();
    for (int i = 0; i < N_MOT; i++) { setupPwm(i); setupEncoder(i); }
    if (PIN_NSLEEP != PIN_NONE) { pinMode(PIN_NSLEEP, OUTPUT); digitalWrite(PIN_NSLEEP, HIGH); }     // DRV8833: sale de reposo (nSLEEP en alto)
    g_state = ST_RUN;
    xTaskCreatePinnedToCore(taskControl,   "control", 4096, NULL, configMAX_PRIORITIES - 2, &g_ctrlTask, 1);
    xTaskCreatePinnedToCore(taskTelemetry, "telem",   4096, NULL, 2, NULL, 0);
    xTaskCreatePinnedToCore(taskCommands,  "cmds",    8192, NULL, 1, NULL, 0);   // 8 KB: el dump arma líneas de 300 B y vsnprintf gasta bastante pila
    g_timer = timerBegin(1000000);                       // reloj de 1 MHz
    timerAttachInterrupt(g_timer, &onControlTimer);
    timerAlarm(g_timer, 1000000UL / CONTROL_HZ, true, 0);
    sendf("{\"info\":\"SPM ESP32-S3 listo\",\"control_hz\":%u,\"pwm_hz\":%u,\"pwm_bits\":%u}", (unsigned)CONTROL_HZ, (unsigned)PWM_FREQ_HZ, (unsigned)PWM_BITS);
}

void loop() { vTaskDelay(pdMS_TO_TICKS(1000)); }
#endif
