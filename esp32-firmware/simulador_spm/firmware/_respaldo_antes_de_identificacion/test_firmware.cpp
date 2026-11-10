// Banco de pruebas en el PC del firmware spm_esp32s3.ino: el mismo código de control, con una planta de motor DC simulada.
//   g++ -std=c++17 -Wall -Wextra -DHOST_TEST -I. test_firmware.cpp -o test_firmware && ./test_firmware
#define HOST_TEST 1
#include "host_mock.h"
#include <stdio.h>
#include <random>
#include <vector>
#include <string>
#include "../spm_esp32s3/spm_esp32s3.ino"

// ---------------------------------------------------------------- planta simulada (por motor) y ganchos de hardware
struct Plant {
    double theta = 0, omega = 0;             // ° y °/s del eje de salida
    double vfree = 900, tau = 0.06, u0 = 0.08;   // velocidad libre a duty 1, constante de tiempo, banda de fricción estática (duty)
    bool locked = false;                     // eje bloqueado (atasco)
    bool dead_encoder = false;               // encoder desconectado (no cuenta)
    int hw_sign = 1;                         // el sentido real del encoder respecto al PWM positivo
    long counts = 0;                         // cuentas reales del encoder (X4)
    int32_t cnt16 = 0;                       // contador de 16 bits del PCNT (con reinicio a 0 en el límite)
    int wrap_mode = 0;                       // 0: reinicia a 0 en el límite · 1: salta al límite opuesto
    int16_t duty = 0;
    double ppr = 1000;
    void step(double dt) {
        double u = duty / (double)PWM_MAX;
        double ue = fabs(u) > u0 ? u - (u > 0 ? u0 : -u0) : 0.0;
        if (locked) { omega = 0; }
        else omega += dt * (vfree * ue - omega) / tau;
        double dth = omega * dt; theta += dth;
        long c = lround(theta * hw_sign / 360.0 * ppr);
        long d = c - counts; counts = c;
        if (dead_encoder) return;
        for (long k = 0; k < labs(d); k++) {
            cnt16 += d > 0 ? 1 : -1;
            if (wrap_mode == 0) { if (cnt16 >= PCNT_LIM) cnt16 = 0; else if (cnt16 <= -PCNT_LIM) cnt16 = 0; }
            else { if (cnt16 > PCNT_LIM) cnt16 = -PCNT_LIM; else if (cnt16 < -PCNT_LIM) cnt16 = PCNT_LIM; }
        }
    }
};
static Plant P[N_MOT];
static std::vector<std::string> g_out;
static int64_t g_now_us = 1000000;
int32_t hwReadRaw(int i) { return P[i].cnt16; }
void hwWritePwm(int i, int16_t duty) { P[i].duty = duty; }
void hwSend(const char *s, int n) { g_out.emplace_back(s, n); }
int64_t hwNowUs() { return g_now_us; }

static int g_fail = 0, g_n = 0;
#define CHECK(cond, ...) do { g_n++; if (!(cond)) { g_fail++; printf("  FALLA: "); printf(__VA_ARGS__); printf("  [%s:%d]\n", __FILE__, __LINE__); } } while (0)

static void resetAll() {
    firmwareInit();
    for (int i = 0; i < N_MOT; i++) P[i] = Plant();
    g_out.clear(); g_now_us = 1000000;
    for (int i = 0; i < N_MOT; i++) { P[i].ppr = g_cfg.m[i].ppr; }
}
// avanza 'secs' segundos de simulación con el lazo a 1 kHz (dt real opcionalmente con jitter)
static std::mt19937 rng(1);
static void run(double secs, double jitter = 0.0, void (*each)(double) = nullptr) {
    double t = 0;
    while (t < secs) {
        double dt = 1e-3 * (1.0 + (jitter > 0 ? std::uniform_real_distribution<double>(-jitter, jitter)(rng) : 0.0));
        for (int i = 0; i < N_MOT; i++) P[i].step(dt);
        g_now_us += (int64_t)(dt * 1e6);
        controlCycle((float)dt, g_now_us);
        t += dt;
        if (each) each(t);
    }
}
static void send(const char *json) { handleLine(json, g_now_us); }
static double posDeg(int i) { return g_rt[i].pos * g_rt[i].dpc; }
static bool anyErr(const char *what) { for (auto &s : g_out) if (s.find("\"err\"") != std::string::npos && s.find(what) != std::string::npos) return true; return false; }

// ---------------------------------------------------------------- pruebas
static void testJson() {
    printf("JSON y comandos\n");
    JMsg m;
    CHECK(jsonParse("{\"m1\":12.5,\"m2\":-3,\"m3\":0}", m) && m.n == 3 && fabsf(jget(m, "m1")->num - 12.5f) < 1e-6, "setpoints m1..m3");
    CHECK(jsonParse("{\"motor\":2,\"pos\":-45.25}\r", m) && jget(m, "motor")->num == 2, "motor/pos con \\r");
    CHECK(jsonParse("{\"cmd\":\"reset\"}", m) && !strcmp(jget(m, "cmd")->str, "reset"), "cmd");
    CHECK(jsonParse("{\"cfg\":\"ppr\",\"val\":[1000,1000,1344]}", m) && jget(m, "val")->n_arr == 3 && jget(m, "val")->arr[2] == 1344, "arreglo");
    CHECK(jsonParse("{ \"zero\" : \"all\" }", m) && jget(m, "zero")->type == 2, "espacios");
    CHECK(jsonParse("{\"a\":true,\"b\":null}", m) && jget(m, "a")->num == 1, "booleanos");
    CHECK(!jsonParse("{\"m1\":12.5", m), "sin llave de cierre");
    CHECK(!jsonParse("m1:12", m), "no es objeto");
    CHECK(!jsonParse("{\"m1\":abc}", m), "número inválido");
    CHECK(!jsonParse("{\"m1\":nan}", m), "nan rechazado");
    CHECK(!jsonParse("{\"a\":1,\"b\":2,\"c\":3,\"d\":4,\"e\":5,\"f\":6,\"g\":7,\"h\":8,\"i\":9,\"j\":10,\"k\":11}", m), "demasiados campos");
    CHECK(!jsonParse("{\"clave_muy_larga_xx\":1}", m), "clave demasiado larga");
    resetAll(); send("{\"m1\":30}"); run(0.01);
    CHECK(fabsf(g_cmd[0].target_deg - 30) < 1e-4 || g_rt[0].target != 0, "consigna recibida");
    resetAll(); send("{\"basura\":1}"); CHECK(anyErr("unknown"), "comando desconocido → error");
    resetAll(); send("{\"cfg\":\"kp\",\"motor\":2,\"val\":0.05}"); CHECK(fabsf(g_cfg.m[1].kp - 0.05f) < 1e-6 && fabsf(g_cfg.m[0].kp - CFG_DEFECTO.kp) < 1e-6, "cfg de un solo motor");
    resetAll(); send("{\"cfg\":\"ppr\",\"val\":[1000,2000,3000]}"); CHECK(g_cfg.m[1].ppr == 2000 && fabsf(g_rt[2].dpc - 0.12f) < 1e-6, "ppr por motor y dpc recalculado");
    resetAll(); send("{\"cfg\":\"ctrl\",\"val\":\"casc\"}"); CHECK(g_cfg.m[0].ctrl == CT_CASC && g_cfg.m[2].ctrl == CT_CASC, "ctrl casc");
    resetAll(); send("{\"cfg\":\"pwm_max\",\"val\":5}"); CHECK(g_cfg.m[0].pwm_max == 1.0f, "pwm_max saturado a 1");
    resetAll(); send("{\"cfg\":\"nada\",\"val\":1}"); CHECK(anyErr("cfg"), "cfg desconocida → error");
}

static void testUnwrap() {
    printf("Desbordamiento del contador de 16 bits\n");
    CHECK(unwrapDelta(5) == 5 && unwrapDelta(-7) == -7, "sin desbordar");
    CHECK(unwrapDelta(-29998) == 2, "reinicio a 0 hacia arriba");
    CHECK(unwrapDelta(29998) == -2, "reinicio a 0 hacia abajo");
    CHECK(unwrapDelta(-59998) == 2 && unwrapDelta(59998) == -2, "salto al límite opuesto");
    for (int mode = 0; mode < 2; mode++) {                        // contador que da varias vueltas de 30000 cuentas en ambos sentidos
        resetAll(); P[0].wrap_mode = mode; P[0].omega = 0;
        send("{\"cfg\":\"limit\",\"motor\":1,\"val\":100000}"); send("{\"cfg\":\"vmax\",\"motor\":1,\"val\":3000}"); send("{\"cfg\":\"amax\",\"motor\":1,\"val\":60000}");
        send("{\"cfg\":\"pwm_max\",\"motor\":1,\"val\":1}"); send("{\"cfg\":\"kp\",\"motor\":1,\"val\":0.05}");
        send("{\"m1\":9000}"); run(12.0);                         // 9000° = 25 vueltas = 25000 cuentas... y luego de vuelta
        send("{\"m1\":-9000}"); run(25.0);
        double err = fabs(posDeg(0) - P[0].theta);
        CHECK(err < 0.5, "modo %d: posición acumulada %.1f° vs real %.1f° (err %.2f°)", mode, posDeg(0), P[0].theta, err);
    }
}

static void testReferenceProfile() {
    printf("Generador de referencia (perfil)\n");
    resetAll();
    MotorRT &m = g_rt[0]; const MotorCfg &c = g_cfg.m[0];
    m.target = 90.0f / m.dpc; double vmax_seen = 0, amax_seen = 0, rvp = 0, rmax = 0;
    for (int k = 0; k < 4000; k++) {
        referenceStep(0, 1e-3f);
        double v = m.rv * m.dpc; double a = (v - rvp) / 1e-3; rvp = v;
        vmax_seen = fmax(vmax_seen, fabs(v)); amax_seen = fmax(amax_seen, fabs(a)); rmax = fmax(rmax, m.r * m.dpc);
    }
    CHECK(vmax_seen <= c.vmax * 1.001, "velocidad máx %.1f ≤ %.1f", vmax_seen, c.vmax);
    CHECK(amax_seen <= c.amax * 1.02, "aceleración máx %.0f ≤ %.0f", amax_seen, c.amax);
    CHECK(rmax <= 90.0001, "sin sobreimpulso (máx %.4f°)", rmax);
    CHECK(fabsf(m.r * m.dpc - 90.0f) < 1e-3f && m.rv == 0, "termina exactamente en el objetivo");
    // un escalón pequeño y uno negativo
    m.target = (float)(m.r - 0.7 / m.dpc); for (int k = 0; k < 2000; k++) referenceStep(0, 1e-3f);
    CHECK(fabsf(m.r - m.target) < 1e-3f, "escalón pequeño negativo llega");
}

static void closedLoopStep(const char *ctrl, double target, double *t_settle, double *overshoot_pct) {
    resetAll();
    char b[96]; snprintf(b, sizeof(b), "{\"cfg\":\"ctrl\",\"val\":\"%s\"}", ctrl); send(b);
    snprintf(b, sizeof(b), "{\"m1\":%.3f}", target); send(b);
    double peak = 0, t = 0; *t_settle = -1;
    while (t < 4.0) {
        run(0.001); t += 0.001; double p = P[0].theta;
        if (fabs(p) > fabs(peak)) peak = p;
        if (*t_settle < 0 && fabs(p - target) < 1.0) *t_settle = t;
        if (fabs(p - target) > 1.0) *t_settle = -1;
    }
    *overshoot_pct = target != 0 ? 100.0 * (fabs(peak) - fabs(target)) / fabs(target) : 0;
}
static void testClosedLoop() {
    printf("Lazo cerrado con planta simulada (900 °/s a duty 1, τ=60 ms, fricción estática 8 %%)\n");
    for (const char *ctrl : { "pid", "casc" }) {
        for (double tgt : { 90.0, -45.0, 5.0 }) {
            double ts, os; closedLoopStep(ctrl, tgt, &ts, &os);
            double e = fabs(P[0].theta - tgt);
            printf("   %s  %+6.1f°: asienta (±1°) en %.2f s, sobreimpulso %.1f %%, error final %.3f°\n", ctrl, tgt, ts, os, e);
            CHECK(ts > 0 && ts < 2.5, "%s %+.0f° asienta", ctrl, tgt);
            CHECK(os * fabs(tgt) / 100.0 < fmax(0.08 * fabs(tgt), 1.0), "%s %+.0f° sobreimpulso %.1f%%", ctrl, tgt, os);
            CHECK(e < 1.0f, "%s %+.0f° error final %.3f°", ctrl, tgt, e);
            CHECK(g_state == ST_RUN, "sin falsas fallas");
        }
    }
}

static void testStream() {
    printf("Streaming de consignas a 25 Hz (trayectoria de la interfaz)\n");
    for (double hz : { 25.0, 10.0 }) {
        resetAll();
        // movimiento sinusoidal ±60° con periodo 4 s, consigna cada 1/hz s (como la interfaz)
        double t = 0, next = 0, maxerr = 0, period = 1.0 / hz;
        while (t < 6.0) {
            if (t >= next) { char b[64]; snprintf(b, sizeof(b), "{\"m1\":%.3f,\"m2\":%.3f,\"m3\":%.3f}", 60 * sin(2 * M_PI * t / 4), -30 * sin(2 * M_PI * t / 4), 20 * sin(2 * M_PI * t / 4)); send(b); next += period; }
            run(0.001); t += 0.001;
            if (t > 1.0) { double ideal = 60 * sin(2 * M_PI * (t - period) / 4); maxerr = fmax(maxerr, fabs(P[0].theta - ideal)); }
        }
        printf("   %.0f Hz: error máximo contra la trayectoria retrasada un periodo: %.2f°\n", hz, maxerr);
        CHECK(maxerr < 3.5, "%.0f Hz: seguimiento %.2f°", hz, maxerr);
        CHECK(g_state == ST_RUN, "sin falsas fallas en streaming");
    }
}

static void testVelocityEstimate() {
    printf("Estimación de velocidad con jitter del periodo de muestreo (±25 %%)\n");
    resetAll(); P[0].omega = 0;
    send("{\"cfg\":\"ctrl\",\"motor\":1,\"val\":\"open\"}"); send("{\"duty\":1,\"val\":0.4}");
    run(1.5, 0.25);
    double vtrue = P[0].omega, vest = g_rt[0].vel * g_rt[0].dpc;
    printf("   velocidad real %.1f °/s, estimada %.1f °/s\n", vtrue, vest);
    CHECK(fabs(vest - vtrue) < 0.06 * vtrue, "estimación de velocidad %.1f vs %.1f", vest, vtrue);
}

static void testFaults() {
    printf("Seguridad: polaridad invertida, atasco, encoder muerto y movimiento legítimo rápido\n");
    // 1) polaridad invertida: el encoder cuenta al revés → realimentación positiva
    resetAll(); P[0].hw_sign = -1;
    send("{\"m1\":90}"); double t = 0; double tf = -1;
    while (t < 3.0) { run(0.001); t += 0.001; if (tf < 0 && g_state == ST_FAULT) tf = t; }
    printf("   polaridad invertida: falla detectada a los %.2f s, desvío máximo previo; PWM tras la falla = %d\n", tf, P[0].duty);
    CHECK(g_state == ST_FAULT && tf > 0 && tf < 1.6, "polaridad invertida detectada (%.2f s)", tf);
    CHECK(P[0].duty == 0 && P[1].duty == 0 && P[2].duty == 0, "PWM a 0 en falla");
    CHECK(anyErr("fault"), "mensaje de error enviado");
    // 2) reset con el signo corregido
    send("{\"cfg\":\"enc_sign\",\"motor\":1,\"val\":-1}"); send("{\"cmd\":\"reset\"}"); run(0.05); P[0].theta = 0; 
    CHECK(g_state == ST_RUN, "reset sale de la falla");
    // 3) atasco
    resetAll(); P[1].locked = true; send("{\"m2\":60}"); run(2.0);
    CHECK(g_state == ST_FAULT && (g_fault_mask & 2), "eje bloqueado detectado");
    // 4) encoder desconectado (el motor gira pero no hay cuentas)
    resetAll(); P[2].dead_encoder = true; send("{\"m3\":80}"); run(2.0);
    CHECK(g_state == ST_FAULT && (g_fault_mask & 4), "encoder muerto detectado");
    // 5) movimientos legítimos grandes y rápidos no disparan la falla
    resetAll(); send("{\"m1\":300,\"m2\":-300,\"m3\":250}"); run(3.0); send("{\"m1\":-300,\"m2\":300,\"m3\":-250}"); run(4.0);
    CHECK(g_state == ST_RUN && fabs(P[0].theta + 300) < 1.5, "movimiento grande legítimo sin falsa falla (pos %.1f°)", P[0].theta);
}

static void testCommands() {
    printf("Comandos: zero, reset, stop/enable, límites, lazo abierto\n");
    resetAll(); send("{\"m1\":50}"); run(1.5);
    send("{\"zero\":1}"); run(0.02);
    CHECK(fabs(posDeg(0)) < 0.5 && fabs(g_rt[0].target) < 1, "zero pone la posición a 0 (pos %.2f°)", posDeg(0));
    send("{\"m1\":20}"); run(1.0); double p0 = P[0].theta;
    send("{\"cmd\":\"stop\"}"); run(0.5);
    CHECK(g_state == ST_IDLE && P[0].duty == 0, "stop deja los motores libres");
    send("{\"cmd\":\"enable\"}"); run(0.1);
    CHECK(g_state == ST_RUN, "enable reactiva");
    send("{\"m1\":99999}"); run(0.2);
    CHECK(fabs(g_rt[0].target * g_rt[0].dpc) <= g_cfg.m[0].limit + 1e-3, "consigna limitada a ±limit (%.0f°)", g_rt[0].target * g_rt[0].dpc);
    send("{\"cmd\":\"reset\"}"); run(0.002);
    CHECK(fabs(g_rt[0].target - g_rt[0].pos) < 8 && g_state == ST_RUN, "reset: objetivo = posición actual (dif %.1f cuentas)", g_rt[0].target - g_rt[0].pos);
    resetAll(); send("{\"cfg\":\"ctrl\",\"motor\":2,\"val\":\"open\"}"); send("{\"duty\":2,\"val\":0.5}"); run(0.1);
    CHECK(P[1].duty > 0 && P[1].duty <= PWM_MAX * g_cfg.m[1].pwm_max + 1, "lazo abierto limitado por pwm_max (duty %d)", P[1].duty);
    (void)p0;
    // pwm_sign invierte los dos canales
    resetAll(); send("{\"cfg\":\"ctrl\",\"motor\":1,\"val\":\"open\"}"); send("{\"cfg\":\"pwm_sign\",\"motor\":1,\"val\":-1}"); send("{\"duty\":1,\"val\":0.3}"); run(0.02);
    CHECK(P[0].duty < 0, "pwm_sign=-1 invierte el sentido (duty %d)", P[0].duty);
}

static void testTelemetry() {
    printf("Telemetría en el formato que lee la interfaz\n");
    resetAll(); send("{\"m1\":40,\"m2\":-20,\"m3\":10}"); run(1.0);
    g_out.clear(); sendTelemetry(12345);
    FILE *f = fopen("telemetria_muestra.jsonl", "w");
    for (auto &s : g_out) fputs(s.c_str(), f);
    fclose(f);
    CHECK(g_out.size() == 1 && g_out[0].size() < 330, "una línea de %zu bytes", g_out.empty() ? 0 : g_out[0].size());
    CHECK(g_out[0].find("\"m1\":{\"posRef\":") != std::string::npos, "formato m1");
}

int main() {
    testJson(); testUnwrap(); testReferenceProfile(); testClosedLoop(); testStream(); testVelocityEstimate(); testFaults(); testCommands(); testTelemetry();
    printf("\n%d comprobaciones, %d fallas\n", g_n, g_fail);
    return g_fail ? 1 : 0;
}
