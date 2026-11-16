// GENERADO por herramientas/generar_trayectorias_smartpharma.py (huella de la configuración 707f809a9797). NO editar a mano.
// Tramos de las trayectorias SmartPharma en GRADOS DE MOTOR (eje de salida, 0 = home) con perfil quíntico. Valores iniciales; calibrar con el mecanismo real.
#pragma once
struct SpmLeg { float q0[3]; float q1[3]; float t_mov; float t_hold; };   // t_mov = 0: solo espera t_hold en q0 (= q1)
struct SpmPlan { const SpmLeg *legs; unsigned char n; };
#define SPM_N_CARAS 4
#define SPM_N_TIPOS 3
#define SPM_PPR_GENERADO 4532
#define SPM_DESCARTE_VARIANTE_DEFECTO 1
#define SPM_DESCARTE2_ESPERA_S 60.0f
#define SPM_TOL_POSE_DEG 4.0f          // tolerancia para reconocer en qué pose está el plato (home, cara 1..N)
static const char *const SPM_TIPOS[SPM_N_TIPOS] = { "TIPO_A", "TIPO_B", "TIPO_C" };   // de izquierda a derecha viendo desde el dispensador

static const float SPM_POSE_Q[SPM_N_CARAS + 1][3] = {   // 0 = home, k = cara k
    {0.000f, 0.000f, 0.000f},
    {15.279f, 15.279f, -15.279f},
    {211.161f, 211.161f, -211.161f},
    {407.044f, 407.044f, -407.044f},
    {602.926f, 602.926f, -602.926f},
};

// CAM_1: Cámara: cara 1 (home -> cara1) (3.00 s)
static const SpmLeg SPM_CAM_1[] = {
    { {0.000f, 0.000f, 0.000f}, {15.279f, 15.279f, -15.279f}, 2.50f, 0.50f },   // home->cara1
};
// CAM_2: Cámara: cara 2 (cara1 -> cara2) (3.00 s)
static const SpmLeg SPM_CAM_2[] = {
    { {15.279f, 15.279f, -15.279f}, {211.161f, 211.161f, -211.161f}, 2.50f, 0.50f },   // cara1->cara2
};
// CAM_3: Cámara: cara 3 (cara2 -> cara3) (3.00 s)
static const SpmLeg SPM_CAM_3[] = {
    { {211.161f, 211.161f, -211.161f}, {407.044f, 407.044f, -407.044f}, 2.50f, 0.50f },   // cara2->cara3
};
// CAM_4: Cámara: cara 4 (cara3 -> cara4) (3.00 s)
static const SpmLeg SPM_CAM_4[] = {
    { {407.044f, 407.044f, -407.044f}, {602.926f, 602.926f, -602.926f}, 2.50f, 0.50f },   // cara3->cara4
};
static const SpmPlan SPM_CAM[SPM_N_CARAS] = { { SPM_CAM_1, 1 }, { SPM_CAM_2, 1 }, { SPM_CAM_3, 1 }, { SPM_CAM_4, 1 } };

// CLAS_TIPO_A_E0: Clasificar TIPO_A desde home (10.00 s)
static const SpmLeg SPM_CLAS_0_E0[] = {
    { {0.000f, 0.000f, 0.000f}, {-26.352f, -32.421f, 7.157f}, 2.50f, 0.50f },   // aproximacion
    { {-26.352f, -32.421f, 7.157f}, {-64.057f, -78.710f, -9.129f}, 2.50f, 0.50f },   // volcado
    { {-64.057f, -78.710f, -9.129f}, {-64.057f, -78.710f, -9.129f}, 0.00f, 1.00f },   // mantener
    { {-64.057f, -78.710f, -9.129f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_A_E1: Clasificar TIPO_A desde cara1 (10.00 s)
static const SpmLeg SPM_CLAS_0_E1[] = {
    { {15.279f, 15.279f, -15.279f}, {-17.879f, -25.505f, -0.348f}, 2.50f, 0.50f },   // aproximacion
    { {-17.879f, -25.505f, -0.348f}, {-64.057f, -78.710f, -9.129f}, 2.50f, 0.50f },   // volcado
    { {-64.057f, -78.710f, -9.129f}, {-64.057f, -78.710f, -9.129f}, 0.00f, 1.00f },   // mantener
    { {-64.057f, -78.710f, -9.129f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_A_E2: Clasificar TIPO_A desde cara2 (10.00 s)
static const SpmLeg SPM_CLAS_0_E2[] = {
    { {211.161f, 211.161f, -211.161f}, {90.537f, 68.144f, -91.769f}, 2.50f, 0.50f },   // aproximacion
    { {90.537f, 68.144f, -91.769f}, {-64.057f, -78.710f, -9.129f}, 2.50f, 0.50f },   // volcado
    { {-64.057f, -78.710f, -9.129f}, {-64.057f, -78.710f, -9.129f}, 0.00f, 1.00f },   // mantener
    { {-64.057f, -78.710f, -9.129f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_A_E3: Clasificar TIPO_A desde cara3 (11.32 s)
static const SpmLeg SPM_CLAS_0_E3[] = {
    { {407.044f, 407.044f, -407.044f}, {195.556f, 171.349f, -177.601f}, 3.01f, 0.50f },   // aproximacion
    { {195.556f, 171.349f, -177.601f}, {-64.057f, -78.710f, -9.129f}, 3.31f, 0.50f },   // volcado
    { {-64.057f, -78.710f, -9.129f}, {-64.057f, -78.710f, -9.129f}, 0.00f, 1.00f },   // mantener
    { {-64.057f, -78.710f, -9.129f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_A_E4: Clasificar TIPO_A desde cara4 (13.87 s)
static const SpmLeg SPM_CLAS_0_E4[] = {
    { {602.926f, 602.926f, -602.926f}, {293.007f, 279.548f, -265.738f}, 4.30f, 0.50f },   // aproximacion
    { {293.007f, 279.548f, -265.738f}, {-64.057f, -78.710f, -9.129f}, 4.57f, 0.50f },   // volcado
    { {-64.057f, -78.710f, -9.129f}, {-64.057f, -78.710f, -9.129f}, 0.00f, 1.00f },   // mantener
    { {-64.057f, -78.710f, -9.129f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_B_E0: Clasificar TIPO_B desde home (10.00 s)
static const SpmLeg SPM_CLAS_1_E0[] = {
    { {0.000f, 0.000f, 0.000f}, {40.346f, 56.726f, -65.419f}, 2.50f, 0.50f },   // aproximacion
    { {40.346f, 56.726f, -65.419f}, {76.156f, 89.579f, -155.996f}, 2.50f, 0.50f },   // volcado
    { {76.156f, 89.579f, -155.996f}, {76.156f, 89.579f, -155.996f}, 0.00f, 1.00f },   // mantener
    { {76.156f, 89.579f, -155.996f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_B_E1: Clasificar TIPO_B desde cara1 (10.00 s)
static const SpmLeg SPM_CLAS_1_E1[] = {
    { {15.279f, 15.279f, -15.279f}, {48.437f, 63.389f, -73.605f}, 2.50f, 0.50f },   // aproximacion
    { {48.437f, 63.389f, -73.605f}, {76.156f, 89.579f, -155.996f}, 2.50f, 0.50f },   // volcado
    { {76.156f, 89.579f, -155.996f}, {76.156f, 89.579f, -155.996f}, 0.00f, 1.00f },   // mantener
    { {76.156f, 89.579f, -155.996f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_B_E2: Clasificar TIPO_B desde cara2 (10.00 s)
static const SpmLeg SPM_CLAS_1_E2[] = {
    { {211.161f, 211.161f, -211.161f}, {155.259f, 149.469f, -174.617f}, 2.50f, 0.50f },   // aproximacion
    { {155.259f, 149.469f, -174.617f}, {76.156f, 89.579f, -155.996f}, 2.50f, 0.50f },   // volcado
    { {76.156f, 89.579f, -155.996f}, {76.156f, 89.579f, -155.996f}, 0.00f, 1.00f },   // mantener
    { {76.156f, 89.579f, -155.996f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_B_E3: Clasificar TIPO_B desde cara3 (10.00 s)
static const SpmLeg SPM_CLAS_1_E3[] = {
    { {407.044f, 407.044f, -407.044f}, {263.769f, 242.229f, -266.814f}, 2.50f, 0.50f },   // aproximacion
    { {263.769f, 242.229f, -266.814f}, {76.156f, 89.579f, -155.996f}, 2.50f, 0.50f },   // volcado
    { {76.156f, 89.579f, -155.996f}, {76.156f, 89.579f, -155.996f}, 0.00f, 1.00f },   // mantener
    { {76.156f, 89.579f, -155.996f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_B_E4: Clasificar TIPO_B desde cara4 (12.04 s)
static const SpmLeg SPM_CLAS_1_E4[] = {
    { {602.926f, 602.926f, -602.926f}, {369.283f, 344.681f, -352.866f}, 3.30f, 0.50f },   // aproximacion
    { {369.283f, 344.681f, -352.866f}, {76.156f, 89.579f, -155.996f}, 3.74f, 0.50f },   // volcado
    { {76.156f, 89.579f, -155.996f}, {76.156f, 89.579f, -155.996f}, 0.00f, 1.00f },   // mantener
    { {76.156f, 89.579f, -155.996f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_C_E0: Clasificar TIPO_C desde home (10.00 s)
static const SpmLeg SPM_CLAS_2_E0[] = {
    { {0.000f, 0.000f, 0.000f}, {3.740f, 25.414f, -2.984f}, 2.50f, 0.50f },   // aproximacion
    { {3.740f, 25.414f, -2.984f}, {-13.547f, 74.341f, -1.678f}, 2.50f, 0.50f },   // volcado
    { {-13.547f, 74.341f, -1.678f}, {-13.547f, 74.341f, -1.678f}, 0.00f, 1.00f },   // mantener
    { {-13.547f, 74.341f, -1.678f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_C_E1: Clasificar TIPO_C desde cara1 (10.00 s)
static const SpmLeg SPM_CLAS_2_E1[] = {
    { {15.279f, 15.279f, -15.279f}, {10.494f, 33.164f, -11.404f}, 2.50f, 0.50f },   // aproximacion
    { {10.494f, 33.164f, -11.404f}, {-13.547f, 74.341f, -1.678f}, 2.50f, 0.50f },   // volcado
    { {-13.547f, 74.341f, -1.678f}, {-13.547f, 74.341f, -1.678f}, 0.00f, 1.00f },   // mantener
    { {-13.547f, 74.341f, -1.678f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_C_E2: Clasificar TIPO_C desde cara2 (10.00 s)
static const SpmLeg SPM_CLAS_2_E2[] = {
    { {211.161f, 211.161f, -211.161f}, {101.090f, 127.561f, -119.983f}, 2.50f, 0.50f },   // aproximacion
    { {101.090f, 127.561f, -119.983f}, {-13.547f, 74.341f, -1.678f}, 2.50f, 0.50f },   // volcado
    { {-13.547f, 74.341f, -1.678f}, {-13.547f, 74.341f, -1.678f}, 0.00f, 1.00f },   // mantener
    { {-13.547f, 74.341f, -1.678f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_C_E3: Clasificar TIPO_C desde cara3 (10.50 s)
static const SpmLeg SPM_CLAS_2_E3[] = {
    { {407.044f, 407.044f, -407.044f}, {201.399f, 214.547f, -226.571f}, 2.63f, 0.50f },   // aproximacion
    { {201.399f, 214.547f, -226.571f}, {-13.547f, 74.341f, -1.678f}, 2.87f, 0.50f },   // volcado
    { {-13.547f, 74.341f, -1.678f}, {-13.547f, 74.341f, -1.678f}, 0.00f, 1.00f },   // mantener
    { {-13.547f, 74.341f, -1.678f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// CLAS_TIPO_C_E4: Clasificar TIPO_C desde cara4 (13.00 s)
static const SpmLeg SPM_CLAS_2_E4[] = {
    { {602.926f, 602.926f, -602.926f}, {308.620f, 300.942f, -326.813f}, 3.85f, 0.50f },   // aproximacion
    { {308.620f, 300.942f, -326.813f}, {-13.547f, 74.341f, -1.678f}, 4.15f, 0.50f },   // volcado
    { {-13.547f, 74.341f, -1.678f}, {-13.547f, 74.341f, -1.678f}, 0.00f, 1.00f },   // mantener
    { {-13.547f, 74.341f, -1.678f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// SPM_CLAS[tipo][etapa]: etapa = pose de partida (0 home, 1..N cara). Lo normal es la etapa N (desde la última cara).
static const SpmPlan SPM_CLAS[SPM_N_TIPOS][SPM_N_CARAS + 1] = {
    { { SPM_CLAS_0_E0, 4 }, { SPM_CLAS_0_E1, 4 }, { SPM_CLAS_0_E2, 4 }, { SPM_CLAS_0_E3, 4 }, { SPM_CLAS_0_E4, 4 } },
    { { SPM_CLAS_1_E0, 4 }, { SPM_CLAS_1_E1, 4 }, { SPM_CLAS_1_E2, 4 }, { SPM_CLAS_1_E3, 4 }, { SPM_CLAS_1_E4, 4 } },
    { { SPM_CLAS_2_E0, 4 }, { SPM_CLAS_2_E1, 4 }, { SPM_CLAS_2_E2, 4 }, { SPM_CLAS_2_E3, 4 }, { SPM_CLAS_2_E4, 4 } },
};

// DESC2_E0: DESCARTE_2 desde home: espera 60 s y home (60.00 s)
static const SpmLeg SPM_DESC2_E0[] = {
    { {0.000f, 0.000f, 0.000f}, {0.000f, 0.000f, 0.000f}, 0.00f, 60.00f },   // espera
};
// DESC2_E1: DESCARTE_2 desde cara1: espera 60 s y home (63.00 s)
static const SpmLeg SPM_DESC2_E1[] = {
    { {15.279f, 15.279f, -15.279f}, {15.279f, 15.279f, -15.279f}, 0.00f, 60.00f },   // espera
    { {15.279f, 15.279f, -15.279f}, {0.000f, 0.000f, 0.000f}, 2.50f, 0.50f },   // home
};
// DESC2_E2: DESCARTE_2 desde cara2: espera 60 s y home (63.20 s)
static const SpmLeg SPM_DESC2_E2[] = {
    { {211.161f, 211.161f, -211.161f}, {211.161f, 211.161f, -211.161f}, 0.00f, 60.00f },   // espera
    { {211.161f, 211.161f, -211.161f}, {0.000f, 0.000f, 0.000f}, 2.70f, 0.50f },   // home
};
// DESC2_E3: DESCARTE_2 desde cara3: espera 60 s y home (66.20 s)
static const SpmLeg SPM_DESC2_E3[] = {
    { {407.044f, 407.044f, -407.044f}, {407.044f, 407.044f, -407.044f}, 0.00f, 60.00f },   // espera
    { {407.044f, 407.044f, -407.044f}, {203.522f, 203.522f, -203.522f}, 2.60f, 0.50f },   // home
    { {203.522f, 203.522f, -203.522f}, {0.000f, 0.000f, 0.000f}, 2.60f, 0.50f },   // home
};
// DESC2_E4: DESCARTE_2 desde cara4: espera 60 s y home (68.70 s)
static const SpmLeg SPM_DESC2_E4[] = {
    { {602.926f, 602.926f, -602.926f}, {602.926f, 602.926f, -602.926f}, 0.00f, 60.00f },   // espera
    { {602.926f, 602.926f, -602.926f}, {301.463f, 301.463f, -301.463f}, 3.85f, 0.50f },   // home
    { {301.463f, 301.463f, -301.463f}, {0.000f, 0.000f, 0.000f}, 3.85f, 0.50f },   // home
};
static const SpmPlan SPM_DESC2[SPM_N_CARAS + 1] = { { SPM_DESC2_E0, 1 }, { SPM_DESC2_E1, 2 }, { SPM_DESC2_E2, 2 }, { SPM_DESC2_E3, 3 }, { SPM_DESC2_E4, 3 } };
