// El firmware spm_esp32s3.ino ejecutándose en el PC contra una planta simulada, expuesto como puerto serie virtual (pty): la interfaz del simulador
// puede conectarse como si fuera la placa. Mismo código de control, JSON y telemetría que el firmware real; solo cambian los ganchos de hardware.
//   g++ -std=c++17 -O2 -DHOST_TEST -I. esp32_host.cpp -o esp32_host && ./esp32_host [--ppr 1000,1000,1000]
#define HOST_TEST 1
#include "host_mock.h"
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <termios.h>
#include <time.h>
#include <string>
#include <vector>
#include "../spm_esp32s3/spm_esp32s3.ino"

struct Plant {
    double theta = 0, omega = 0, vfree = 900, tau = 0.06, u0 = 0.08, ppr = 1000; long counts = 0; int32_t cnt16 = 0; int16_t duty = 0;
    void step(double dt) {
        double u = duty / (double)PWM_MAX, ue = fabs(u) > u0 ? u - (u > 0 ? u0 : -u0) : 0.0;
        omega += dt * (vfree * ue - omega) / tau; theta += omega * dt;
        long c = lround(theta / 360.0 * ppr), d = c - counts; counts = c;
        for (long k = 0; k < labs(d); k++) { cnt16 += d > 0 ? 1 : -1; if (cnt16 >= PCNT_LIM || cnt16 <= -PCNT_LIM) cnt16 = 0; }
    }
};
static Plant P[N_MOT];
static int g_fd = -1;
static int64_t g_t0_us = 0;
static int64_t nowUs() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000; }
int32_t hwReadRaw(int i) { return P[i].cnt16; }
void hwWritePwm(int i, int16_t duty) { P[i].duty = duty; }
void hwSend(const char *s, int n) { if (g_fd >= 0) { ssize_t r = write(g_fd, s, n); (void)r; } }
int64_t hwNowUs() { return nowUs() - g_t0_us; }

int main(int argc, char **argv) {
    firmwareInit();
    for (int a = 1; a + 1 < argc; a++) if (!strcmp(argv[a], "--ppr")) { float p[3]; if (sscanf(argv[a + 1], "%f,%f,%f", &p[0], &p[1], &p[2]) == 3) for (int i = 0; i < 3; i++) g_cfg.m[i].ppr = p[i]; }
    applyConfigDerived();
    for (int i = 0; i < N_MOT; i++) P[i].ppr = g_cfg.m[i].ppr;
    g_fd = posix_openpt(O_RDWR | O_NOCTTY); grantpt(g_fd); unlockpt(g_fd);
    int sl = open(ptsname(g_fd), O_RDWR | O_NOCTTY); termios tio; tcgetattr(sl, &tio); cfmakeraw(&tio); tcsetattr(sl, TCSANOW, &tio);
    printf("PUERTO: %s\n", ptsname(g_fd)); fflush(stdout);
    fcntl(g_fd, F_SETFL, fcntl(g_fd, F_GETFL) | O_NONBLOCK);
    g_t0_us = nowUs();
    sendf("{\"info\":\"SPM ESP32-S3 (host) listo\",\"control_hz\":%u}", (unsigned)CONTROL_HZ);
    std::string buf; int64_t next = nowUs(), t_prev = next, t_tel = next;
    for (;;) {
        char tmp[256]; ssize_t n = read(g_fd, tmp, sizeof(tmp));
        if (n > 0) for (ssize_t k = 0; k < n; k++) { if (tmp[k] == '\n') { if (!buf.empty()) handleLine(buf.c_str(), hwNowUs()); buf.clear(); } else if (buf.size() < 239) buf += tmp[k]; }
        int64_t t = nowUs();
        if (t >= next) {
            double dt = (t - t_prev) * 1e-6; t_prev = t; next += 1000; if (next < t) next = t + 1000;
            for (int i = 0; i < N_MOT; i++) P[i].step(dt);
            controlCycle((float)dt, hwNowUs());
            if (t - t_tel >= 1000000 / g_cfg.telem_hz) { t_tel = t; sendTelemetry((uint32_t)(hwNowUs() / 1000)); }
        } else usleep(100);
    }
}
