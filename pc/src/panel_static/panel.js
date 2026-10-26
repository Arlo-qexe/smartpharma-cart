"use strict";

const INTERVALO_MS = 2000;
const ERROR_REVISION = "ERROR_REVISION_MANUAL";
const MOTIVOS = {
  fallo_captura: "Lote vacío (captura fallida o reintento tras perder la conexión)",
  sin_consenso_ocr: "Sin consenso en el reconocimiento",
  framing_invalido: "Lote inválido (framing)",
};

let estado = null;
let vista = "inicio";
let loteSeleccionado = null;
let ultimoLoteId = null;      // el lote más reciente que ya vio la página
let claveFotos = null;

const $ = (id) => document.getElementById(id);

function crear(tag, clase, texto) {
  const e = document.createElement(tag);
  if (clase) e.className = clase;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

const fmtHora = (ts) => new Date(ts * 1000).toLocaleString("es");
const motivoHumano = (m) => MOTIVOS[m] || m;
const resultadoHumano = (r) => (r === ERROR_REVISION ? "Revisión manual" : r);

// Severidad de una alarma: roja si la Orange Pi la está esperando (bloquea el
// ciclo), amarilla si sigue activa pero ya nadie la espera (queda por cerrar),
// gris (sin clase) si está resuelta.
const claseAlarma = (a) => (a.activa ? (a.espera_decision ? "rojo" : "amarillo") : "");

function vaciar(nodo) {
  while (nodo.firstChild) nodo.removeChild(nodo.firstChild);
}

function filaVacia(cuerpo, columnas, texto) {
  const tr = crear("tr");
  const td = crear("td", "vacio", texto);
  td.colSpan = columnas;
  tr.appendChild(td);
  cuerpo.appendChild(tr);
}

// ---------------------------------------------------------------------------
function renderBanner() {
  const activas = estado.alarmas.filter((a) => a.activa);
  const banner = $("banner");
  banner.hidden = activas.length === 0;
  vaciar(banner);
  if (activas.length) {
    const esperando = activas.some((a) => a.espera_decision);
    banner.classList.toggle("amarillo", !esperando);
    const principal = activas.find((a) => a.espera_decision) || activas[0];
    banner.appendChild(crear("span", "",
      (esperando
        ? "ALARMA ACTIVA — " + motivoHumano(principal.motivo) + ". La Orange Pi espera tu decisión."
        : "ALARMA ANTIGUA SIN CERRAR — " + motivoHumano(principal.motivo) + ". La Orange Pi ya no la espera.") +
      (activas.length > 1 ? ` (${activas.length} alarmas activas)` : "")));
    if (vista !== "alertas") {
      const boton = crear("button", "btn-chico", "Resolver");
      boton.addEventListener("click", () => cambiarVista("alertas"));
      banner.appendChild(boton);
    }
  }
  const insignia = $("insignia");
  insignia.hidden = activas.length === 0;
  insignia.textContent = activas.length;
}

function loteMostrado() {
  if (!estado.lotes.length) return null;
  return estado.lotes.find((l) => l.id === loteSeleccionado) || estado.lotes[0];
}

function renderInicio() {
  renderRecorrido();
  const lote = loteMostrado();
  const fotos = $("fotos");

  if (!lote) {
    $("lote-titulo").textContent = "Esperando el primer lote de la Orange Pi…";
    $("d-clasif").textContent = "—";
    $("d-clasif").className = "valor sin-dato";
    $("d-lote").textContent = "";
    $("fotos-aviso").textContent = "";
    vaciar(fotos);
    claveFotos = null;
  } else {
    $("lote-titulo").textContent =
      `Lote #${lote.id} · ${fmtHora(lote.ts)} · desde ${lote.origen}`;
    const clasif = $("d-clasif");
    clasif.textContent = resultadoHumano(lote.resultado);
    clasif.className = "valor" + (lote.resultado === ERROR_REVISION ? " error" : "");
    $("d-lote").textContent = `${lote.n_imagenes} imágenes recibidas`;

    // Las fotos se reconstruyen solo si cambió el lote, para no parpadear.
    const clave = `${lote.id}:${lote.fotos_disponibles}`;
    if (clave !== claveFotos) {
      claveFotos = clave;
      vaciar(fotos);
      if (lote.fotos_disponibles) {
        for (let i = 0; i < lote.n_imagenes; i++) {
          const fig = crear("figure");
          const enlace = crear("a");
          enlace.href = `/foto/${lote.id}/${i}`;
          enlace.target = "_blank";
          enlace.rel = "noopener";
          // Clic normal: visor ampliado. Clic central / Ctrl+clic siguen
          // abriendo la foto en otra pestaña gracias al href.
          enlace.addEventListener("click", (ev) => {
            if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0) return;
            ev.preventDefault();
            abrirVisor(lote.id, i, lote.n_imagenes);
          });
          const img = crear("img");
          img.src = `/foto/${lote.id}/${i}`;
          img.alt = `Cara ${i + 1}`;
          enlace.appendChild(img);
          fig.appendChild(enlace);
          fig.appendChild(crear("figcaption", "", `Cara ${i + 1}`));
          fotos.appendChild(fig);
        }
      }
    }
    $("fotos-aviso").textContent =
      lote.n_imagenes === 0
        ? "Lote vacío: la captura falló, no hay fotos."
        : lote.fotos_disponibles
          ? ""
          : `Fotos no disponibles: solo se conservan en memoria los últimos ${estado.max_lotes_con_fotos} lotes.`;
  }

  const lista = $("alertas-recientes");
  vaciar(lista);
  if (!estado.alarmas.length) {
    lista.appendChild(crear("li", "vacio", "Sin alertas"));
  }
  for (const a of estado.alarmas.slice(0, 4)) {
    const li = crear("li");
    li.appendChild(crear("span", "punto " + claseAlarma(a)));
    const lote = a.lote_id ? ` · lote #${a.lote_id}` : "";
    li.appendChild(crear("span", "", motivoHumano(a.motivo) + lote));
    lista.appendChild(li);
  }
}

// Botón "Iniciar recorrido" + casilla de cantidad + botón "Detener": ordenan a la Orange Pi
// iniciar el ciclo de auditoría (informe 4.7) y detenerlo entre objetos (informe 4.8). La
// orden de inicio es de un solo uso y vence si nadie la recoge; la cantidad la cuenta la PC.
function renderRecorrido() {
  const o = estado.orden_ciclo;
  const c = estado.ciclo;
  const iniciar = $("boton-recorrido");
  const detener = $("boton-detener");
  const cantidad = $("cantidad-cajas");
  const texto = $("recorrido-estado");
  const caja = c.cantidad ? `${c.iniciadas} de ${c.cantidad}` : `${c.iniciadas} (sin límite)`;
  let editable = false;
  if (c.estado === "activo") {
    iniciar.disabled = true;
    detener.disabled = false;
    $("boton-texto").textContent = "RECORRIDO EN CURSO";
    texto.textContent = `Cajas iniciadas: ${caja}. Detener actúa antes de la siguiente caja.`;
  } else if (c.estado === "deteniendo") {
    iniciar.disabled = true;
    detener.disabled = true;
    $("boton-texto").textContent = "DETENIENDO…";
    texto.textContent = `La Orange Pi termina la caja en curso y se detiene antes de la siguiente (cajas iniciadas: ${caja}).`;
  } else if (o.estado === "ordenada") {
    iniciar.disabled = true;
    detener.disabled = false;               // cancela la orden si aún no se recoge
    $("boton-texto").textContent = "ORDEN ENVIADA";
    texto.textContent = `Esperando que la Orange Pi la recoja (vence en ${o.restante_s} s).`;
  } else {
    iniciar.disabled = false;
    detener.disabled = true;
    editable = true;
    $("boton-texto").textContent = "INICIAR RECORRIDO";
    const hace = o.ts_ultima_consulta ? estado.ts_servidor - o.ts_ultima_consulta : null;
    const espera = hace !== null && hace < 10
      ? "La Orange Pi está esperando la orden."
      : "La Orange Pi no ha consultado hace poco (puede estar apagada o ya en marcha).";
    const fin = c.motivo_fin === "cantidad_alcanzada"
      ? `Recorrido completo: ${c.iniciadas} cajas (${fmtHora(c.ts_fin)}). `
      : c.motivo_fin === "detenido_por_regente"
        ? `Recorrido detenido tras ${c.iniciadas} cajas (${fmtHora(c.ts_fin)}). `
        : "";
    texto.textContent = fin + espera;
  }
  cantidad.disabled = !editable;
  if (!puedeOperar()) {                       // sin turno se mira, no se opera
    iniciar.disabled = true;
    detener.disabled = true;
    cantidad.disabled = true;
    const a = acceso();
    texto.textContent = (a && !a.admin_configurado
      ? "No hay administrador: créalo en la PC con «bash pc/scripts/usuarios.sh crear-admin». "
      : "Inicia tu turno para operar el recorrido. ") + texto.textContent;
  }
}

// Indicador de conexión con la Orange Pi: se basa en el último mensaje recibido de ella
// (consulta o lote), no en un ping; la PC no puede iniciar conexiones hacia ella.
function renderOrangePi() {
  const op = estado.orangepi;
  const nodo = $("orangepi");
  nodo.classList.remove("conectada", "perdida", "sin-dato");
  if (!op.ts_ultimo_contacto) {
    nodo.classList.add("sin-dato");
    $("orangepi-texto").textContent = "Orange Pi: sin contacto aún";
  } else if (op.conectada) {
    nodo.classList.add("conectada");
    $("orangepi-texto").textContent = `Orange Pi conectada (${op.ip})`;
  } else {
    nodo.classList.add("perdida");
    const seg = Math.round(estado.ts_servidor - op.ts_ultimo_contacto);
    $("orangepi-texto").textContent = `Orange Pi sin contacto hace ${seg} s`;
  }
}

async function postCiclo(ruta, cuerpo) {
  try {
    const r = await fetch(ruta, {
      method: "POST",
      headers: cuerpo ? { "Content-Type": "application/json" } : {},
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    });
    if (r.status === 401 || r.status === 403) {
      $("recorrido-estado").textContent = "Tu turno no está abierto o venció: inicia turno para operar.";
    }
    return r.ok;
  } catch (e) {
    return false;
  } finally {
    refrescar();
  }
}

$("boton-recorrido").addEventListener("click", async () => {
  const crudo = $("cantidad-cajas").value.trim();
  const cantidad = crudo === "" ? null : Number(crudo);
  if (cantidad !== null && (!Number.isInteger(cantidad) || cantidad < 1 || cantidad > 9999)) {
    $("recorrido-estado").textContent = "La cantidad debe ser un entero entre 1 y 9999 (o déjala vacía para no limitar).";
    return;
  }
  $("boton-recorrido").disabled = true;       // evita doble clic mientras responde
  await postCiclo("/api/ciclo/iniciar", cantidad === null ? null : { cantidad });
});

$("boton-detener").addEventListener("click", async () => {
  $("boton-detener").disabled = true;
  await postCiclo("/api/ciclo/detener", null);
});

// ---------------------------------------------------------------------------
// Acceso: turno, inicio de sesión y administración de usuarios (usuarios.py).
// Mirar el panel es libre; operar (iniciar/detener, resolver alarmas) exige un turno abierto.
let sesionPrevia = false;          // ¿había sesión en el sondeo anterior? (para avisar si se perdió sola)
let cierreVoluntario = false;
let avisoAcceso = "";
let ultimaActividad = 0;
let usuarios = [];
let usuariosCargados = false;
let codigoNuevo = null;            // {nombre, codigo}: solo vive aquí hasta que se descarta

// `acceso` es null si el servidor no controla accesos (solo pruebas): entonces todo está permitido.
const acceso = () => (estado ? estado.acceso : null);
const puedeOperar = () => {
  const a = acceso();
  return !a || !!(a.sesion && a.sesion.permisos.includes("operar"));
};
const esAdmin = () => {
  const a = acceso();
  return !!(a && a.sesion && a.sesion.rol === "administrador");
};

function renderAcceso() {
  const a = acceso();
  $("turno").hidden = !a;
  if (!a) return;
  if (sesionPrevia && !a.sesion && !cierreVoluntario) {
    avisoAcceso = "Tu turno terminó (venció por inactividad o un administrador tomó el control).";
  }
  if (a.sesion) avisoAcceso = "";
  sesionPrevia = !!a.sesion;
  cierreVoluntario = false;

  const texto = $("turno-texto");
  texto.classList.toggle("activo", !!a.sesion);
  if (a.sesion) {
    texto.textContent = `${a.sesion.nombre} · ${a.sesion.rol}`;
  } else if (a.turno) {
    texto.textContent = `Turno de ${a.turno.nombre}`;
  } else {
    texto.textContent = "Sin turno abierto";
  }
  $("turno-boton").textContent = a.sesion ? "Cerrar turno" : "Iniciar turno";
  $("turno-aviso").hidden = !avisoAcceso;
  $("turno-aviso").textContent = avisoAcceso;

  const admin = esAdmin();
  $("pestana-usuarios").hidden = !admin;
  if (!admin && vista === "usuarios") cambiarVista("inicio");
}

function abrirLogin() {
  $("login-error").textContent = "";
  $("login-sin-admin").hidden = !acceso() || acceso().admin_configurado;
  modoLogin("aux");
  $("login").showModal();
  $("login-codigo").focus();
}
function modoLogin(modo) {
  $("login-aux").hidden = modo !== "aux";
  $("login-admin").hidden = modo !== "admin";
  $("login-tab-aux").classList.toggle("activa", modo === "aux");
  $("login-tab-admin").classList.toggle("activa", modo === "admin");
  $(modo === "aux" ? "login-codigo" : "login-nombre").focus();
}
const ERRORES_ACCESO = {
  credenciales_invalidas: "Código o clave incorrectos.",
  sesion_requerida: "Tu turno no está abierto o venció: inicia turno de nuevo.",
  sin_permiso: "No tienes permiso para hacer esto.",
  error_interno: "Error interno del servidor (revisa el log de la PC).",
};
function textoErrorAcceso(d) {
  if (d.error === "bloqueado") return `Demasiados intentos fallidos. Espera ${d.espera_s} s.`;
  if (d.error === "turno_en_curso") {
    return `${d.nombre} tiene el turno abierto. Debe cerrarlo (un administrador puede tomar el control).`;
  }
  return ERRORES_ACCESO[d.error] || ERRORES_USUARIOS[d.error] || "No se pudo completar la acción.";
}

$("turno-boton").addEventListener("click", async () => {
  const a = acceso();
  if (a && a.sesion) {
    cierreVoluntario = true;
    await fetch("/api/sesion/cerrar", { method: "POST" }).catch(() => {});
    codigoNuevo = null;
    refrescar();
  } else {
    abrirLogin();
  }
});
$("login-tab-aux").addEventListener("click", () => modoLogin("aux"));
$("login-tab-admin").addEventListener("click", () => modoLogin("admin"));
$("login-cancelar").addEventListener("click", () => $("login").close());
$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const aux = !$("login-aux").hidden;
  const cuerpo = aux
    ? { codigo: $("login-codigo").value.trim() }
    : { nombre: $("login-nombre").value.trim(), clave: $("login-clave").value };
  $("login-error").textContent = "";
  try {
    const r = await fetch("/api/sesion/iniciar", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $("login-error").textContent = textoErrorAcceso(d); return; }
    avisoAcceso = "";
    $("login").close();
    refrescar();
  } catch (e) {
    $("login-error").textContent = "No se pudo contactar al servidor.";
  } finally {
    for (const id of ["login-codigo", "login-nombre", "login-clave"]) $(id).value = "";   // nunca se deja un secreto en el formulario
  }
});

// Si hay turno, cualquier clic o tecla lo mantiene vivo (como máximo un aviso por minuto).
function actividadUsuario() {
  const a = acceso();
  if (!a || !a.sesion || Date.now() - ultimaActividad < 60000) return;
  ultimaActividad = Date.now();
  fetch("/api/sesion/actividad", { method: "POST" }).catch(() => {});
}
for (const tipo of ["pointerdown", "keydown"]) document.addEventListener(tipo, actividadUsuario, { passive: true });

// ---- Pestaña USUARIOS (solo administrador) ------------------------------------------
const ERRORES_USUARIOS = {
  nombre_duplicado: "Ya existe un usuario con ese nombre (los nombres no se reutilizan).",
  nombre_invalido: "El nombre debe tener entre 2 y 60 caracteres.",
  clave_invalida: "La clave debe tener al menos 10 caracteres, sin espacios al principio ni al final.",
  rol_invalido: "Rol inválido.",
  ultimo_administrador: "No se puede desactivar ni eliminar al único administrador.",
  usuario_inexistente: "Ese usuario ya no existe.",
  solo_auxiliares: "Solo los auxiliares tienen código.",
  sin_codigos_libres: "No quedan códigos libres.",
};

async function cargarUsuarios() {
  try {
    const r = await fetch("/api/usuarios", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    usuarios = (await r.json()).usuarios;
    usuariosCargados = true;
  } catch (e) {
    $("usuarios-error").textContent = "No se pudo leer la lista de usuarios.";
  }
  renderUsuarios();
}

async function accionUsuarios(ruta, cuerpo) {
  $("usuarios-error").textContent = "";
  try {
    const r = await fetch(ruta, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo || {}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $("usuarios-error").textContent = textoErrorAcceso(d); return null; }
    return d;
  } catch (e) {
    $("usuarios-error").textContent = "No se pudo contactar al servidor.";
    return null;
  } finally {
    cargarUsuarios();
  }
}

function mostrarCodigo(nombre, codigo) {
  codigoNuevo = codigo ? { nombre, codigo } : null;
  renderUsuarios();
}

let firmaUsuarios = null;
function renderUsuarios() {
  if (vista !== "usuarios") return;
  // Se reconstruye solo si cambió algo: así un clic no se pierde porque la tabla se rehízo a mitad.
  const firma = JSON.stringify([usuarios, codigoNuevo, usuariosCargados]);
  if (firma === firmaUsuarios) return;
  firmaUsuarios = firma;
  $("codigo-nuevo").hidden = !codigoNuevo;
  if (codigoNuevo) {
    $("codigo-nuevo-texto").textContent = `Código de ${codigoNuevo.nombre}:`;
    $("codigo-nuevo-valor").textContent = codigoNuevo.codigo;
  }
  const cuerpo = $("tabla-usuarios").tBodies[0];
  vaciar(cuerpo);
  if (!usuarios.length) filaVacia(cuerpo, 5, usuariosCargados ? "No hay usuarios." : "Cargando…");
  for (const u of usuarios) {
    const tr = crear("tr", u.eliminado || !u.activo ? "baja" : "");
    tr.appendChild(crear("td", "responsable", u.nombre));
    tr.appendChild(crear("td", "", u.rol));
    tr.appendChild(crear("td", "", u.eliminado ? "Eliminado" : u.activo ? "Activo" : "Desactivado"));
    tr.appendChild(crear("td", "", `${fmtHora(u.creado_ms / 1000)} · ${u.creado_por}`));
    const td = crear("td");
    const caja = crear("div", "acciones");
    if (!u.eliminado) {
      if (u.rol === "auxiliar") {
        const nuevo = crear("button", "btn-chico", "Nuevo código");
        nuevo.addEventListener("click", async () => {
          if (!confirm(`Se generará un código nuevo para ${u.nombre} y el anterior dejará de servir. ¿Continuar?`)) return;
          const d = await accionUsuarios(`/api/usuarios/${u.id}/codigo`);
          if (d) mostrarCodigo(u.nombre, d.codigo);
        });
        caja.appendChild(nuevo);
      }
      const alternar = crear("button", "btn-chico", u.activo ? "Desactivar" : "Activar");
      alternar.addEventListener("click", () => accionUsuarios(`/api/usuarios/${u.id}/${u.activo ? "desactivar" : "activar"}`));
      const eliminar = crear("button", "btn-chico", "Eliminar");
      eliminar.addEventListener("click", () => {
        if (confirm(`¿Eliminar a ${u.nombre}? Ya no podrá entrar; sus acciones pasadas siguen a su nombre en el registro y el nombre no se podrá reutilizar.`)) {
          accionUsuarios(`/api/usuarios/${u.id}/eliminar`);
        }
      });
      caja.append(alternar, eliminar);
    }
    td.appendChild(caja);
    tr.appendChild(td);
    cuerpo.appendChild(tr);
  }
}

$("usuario-rol").addEventListener("change", () => {
  $("usuario-clave-envoltura").hidden = $("usuario-rol").value !== "administrador";
});
$("usuario-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const rol = $("usuario-rol").value;
  const nombre = $("usuario-nombre").value.trim();
  const cuerpo = { nombre, rol };
  if (rol === "administrador") cuerpo.clave = $("usuario-clave").value;
  const d = await accionUsuarios("/api/usuarios", cuerpo);
  $("usuario-clave").value = "";
  if (d) {
    $("usuario-nombre").value = "";
    mostrarCodigo(d.usuario.nombre, d.codigo);
  }
});
$("codigo-nuevo-cerrar").addEventListener("click", () => mostrarCodigo(null, null));
$("clave-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("clave-mensaje").textContent = "";
  const d = await accionUsuarios("/api/sesion/clave", { actual: $("clave-actual").value, nueva: $("clave-nueva").value });
  $("clave-actual").value = "";
  $("clave-nueva").value = "";
  if (d) $("clave-mensaje").textContent = "Clave cambiada.";
});

// ---------------------------------------------------------------------------
// Registro de eventos (solo lectura). Los datos vienen de /api/log (auditoria.py).
const PAGINA_REGISTRO = 100;
const registro = { eventos: [], hayMas: false, tipos: [], total: 0, cargado: false,
                   cargando: false, noDisponible: false, error: false };
let integridad = null;
let ultimaIntegridad = 0;
let firmaRegistro = null;
let firmaTipos = null;

const MOTIVOS_TURNO = {
  voluntario: "lo cerró la propia persona",
  expirado: "venció por inactividad",
  tomado_por_administrador: "un administrador tomó el control",
  reemplazado_por_nuevo_inicio: "la misma persona volvió a entrar",
  codigo_regenerado: "se regeneró su código",
  usuario_desactivado: "se desactivó al usuario",
  usuario_eliminado: "se eliminó al usuario",
  clave_restablecida: "se restableció su clave",
};
const ACCIONES = {
  iniciar_ciclo: "iniciar el recorrido", detener_ciclo: "detener el recorrido", resolver_alarma: "resolver una alarma",
  asistente_confirmar: "confirmar una acción del asistente", listar_usuarios: "ver los usuarios",
  administrar_usuarios: "administrar usuarios",
};
const MOTIVOS_FIN = {
  detenido_por_regente: "lo detuvo el regente",
  cantidad_alcanzada: "se alcanzó la cantidad de cajas fijada",
};
const v = (x) => (x === null || x === undefined || x === "" ? "—" : String(x));
const cajas = (c) => (c ? `${c} ${c === 1 ? "caja" : "cajas"}` : "sin límite de cajas");
const iniciadas = (n) => `${v(n)} ${n === 1 ? "caja iniciada" : "cajas iniciadas"}`;

// Cada evento: [etiqueta corta, función que redacta el detalle a partir de su contenido].
const EVENTOS = {
  sistema_iniciado: ["Sistema iniciado", (d) => `Servidor de la PC iniciado (registro en ${v(d.base)}).`],
  sistema_detenido: ["Sistema detenido", () => "Servidor de la PC detenido de forma ordenada."],
  servicio_anunciado: ["Servicio anunciado", (d) => `Anunciado por mDNS como ${v(d.nombre)} en ${v(d.ip)}:${v(d.puerto)}.`],
  lote_recibido: ["Lote recibido", (d) =>
    `Lote #${v(d.lote_id)} de ${v(d.origen)}: ${v(d.n_imagenes)} imágenes → ${resultadoHumano(d.resultado)}.`],
  alarma_abierta: ["Alarma abierta", (d) =>
    `Alarma #${v(d.alarma_id)}: ${motivoHumano(d.motivo)}${d.lote_id ? ` (lote #${d.lote_id})` : ""}.`],
  alarma_resuelta: ["Alarma resuelta", (d) =>
    `Alarma #${v(d.alarma_id)} (${motivoHumano(d.motivo)}${d.lote_id ? `, lote #${d.lote_id}` : ""}) resuelta con el destino ${v(d.destino)}.`],
  alarma_cerrada: ["Alarma cerrada", (d) =>
    `Alarma #${v(d.alarma_id)} (${motivoHumano(d.motivo)}) cerrada sin decisión: la Orange Pi ya no la esperaba.`],
  decision_entregada: ["Decisión entregada", (d) =>
    `Destino ${v(d.destino)} de la alarma #${v(d.alarma_id)} entregado a la Orange Pi (${v(d.ip_orangepi)}).`],
  ciclo_inicio_ordenado: ["Inicio ordenado", (d) => `Orden de iniciar el recorrido (${cajas(d.cantidad)}).`],
  ciclo_iniciado: ["Recorrido iniciado", (d) => `La Orange Pi recogió la orden (${cajas(d.cantidad)}).`],
  ciclo_detencion_ordenada: ["Detención ordenada", (d) =>
    `Orden de detener el recorrido (${iniciadas(d.cajas_iniciadas)}).`],
  ciclo_detenido: ["Recorrido detenido", (d) =>
    `Recorrido detenido: ${MOTIVOS_FIN[d.motivo] || v(d.motivo)} (${iniciadas(d.cajas_iniciadas)}; lo inició ${v(d.iniciado_por)}).`],
  ciclo_orden_cancelada: ["Orden cancelada", () => "Se canceló la orden de inicio antes de que la Orange Pi la recogiera."],
  ciclo_orden_vencida: ["Orden vencida", (d) => `La orden de inicio de ${v(d.ordenada_por)} venció sin que la Orange Pi la recogiera.`],
  usuario_creado: ["Usuario creado", (d) => `Se creó al ${v(d.rol)} ${v(d.nombre)}.`],
  usuario_codigo_regenerado: ["Código regenerado", (d) => `Nuevo código para ${v(d.nombre)}; el anterior dejó de servir.`],
  usuario_activado: ["Usuario activado", (d) => `${v(d.nombre)} vuelve a poder entrar.`],
  usuario_desactivado: ["Usuario desactivado", (d) => `${v(d.nombre)} ya no puede entrar.`],
  usuario_eliminado: ["Usuario eliminado", (d) => `${v(d.nombre)} eliminado (su nombre no se reutiliza).`],
  usuario_clave_cambiada: ["Clave cambiada", (d) => `${v(d.nombre)} cambió su propia clave.`],
  usuario_clave_restablecida: ["Clave restablecida", (d) => `Clave de ${v(d.nombre)} restablecida desde la consola de la PC.`],
  turno_iniciado: ["Turno iniciado", (d) => `Abrió su turno (${v(d.rol)}).`],
  turno_cerrado: ["Turno cerrado", (d) =>
    `Turno de ${v(d.nombre)} cerrado: ${MOTIVOS_TURNO[d.motivo] || v(d.motivo)} (duró ${v(d.duracion_s)} s).`],
  login_fallido: ["Intento fallido", (d) =>
    `Intento fallido con ${d.via === "codigo" ? "un código" : `nombre y clave${d.nombre_intentado ? ` (${d.nombre_intentado})` : ""}`}; ${v(d.fallos_seguidos)} seguidos.`],
  login_bloqueado: ["Acceso bloqueado", (d) => `Demasiados intentos fallidos: inicio de sesión bloqueado ${v(d.espera_s)} s.`],
  accion_denegada: ["Acción denegada", (d) =>
    `Se negó «${ACCIONES[d.accion] || v(d.accion)}»: ${d.motivo === "sin_permiso" ? "la persona no tiene permiso" : "no había turno abierto"}.`],
  ciclo_interrumpido: ["Recorrido interrumpido", (d) =>
    `${v(d.motivo)} (${iniciadas(d.cajas_iniciadas)}; lo inició ${v(d.iniciado_por)}).`],
};
const etiquetaEvento = (tipo) => (EVENTOS[tipo] ? EVENTOS[tipo][0] : tipo);
const claseEvento = (tipo) =>
  (tipo === "alarma_abierta" || tipo === "login_bloqueado") ? "rojo"
    : ["ciclo_orden_vencida", "ciclo_interrumpido", "login_fallido", "accion_denegada"].includes(tipo) ? "amarillo" : "";
function describirEvento(e) {
  const f = EVENTOS[e.tipo];
  return f ? f[1](e.detalle || {}) : JSON.stringify(e.detalle);
}

function filtrosRegistro() {
  const f = {};
  const tipo = $("reg-tipo").value;
  if (tipo) f.tipo = tipo;
  const q = $("reg-texto").value.trim();
  if (q) f.q = q;
  const desde = $("reg-desde").value;
  if (desde) f.desde = String(new Date(desde + "T00:00:00").getTime());
  const hasta = $("reg-hasta").value;
  if (hasta) f.hasta = String(new Date(hasta + "T23:59:59.999").getTime());
  return f;
}

// modo: "todo" (primera página con los filtros), "mas" (anteriores) o "nuevos" (posteriores al más reciente)
async function cargarRegistro(modo = "todo") {
  if (registro.cargando) return;
  registro.cargando = true;
  try {
    const q = new URLSearchParams(filtrosRegistro());
    q.set("limite", String(PAGINA_REGISTRO));
    if (modo === "mas" && registro.eventos.length) q.set("antes", String(registro.eventos[registro.eventos.length - 1].id));
    if (modo === "nuevos" && registro.eventos.length) q.set("despues", String(registro.eventos[0].id));
    const r = await fetch("/api/log?" + q, { cache: "no-store" });
    if (r.status === 503) { registro.noDisponible = true; registro.cargado = true; return; }
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    registro.noDisponible = false;
    registro.error = false;
    if (modo === "mas") {
      registro.eventos = registro.eventos.concat(d.eventos);
      registro.hayMas = d.hay_mas;
    } else if (modo === "nuevos" && !d.hay_mas) {
      registro.eventos = d.eventos.concat(registro.eventos);
    } else if (modo === "nuevos") {
      registro.cargado = false;          // llegaron más de una página: se vuelve a leer todo
    } else {
      registro.eventos = d.eventos;
      registro.hayMas = d.hay_mas;
    }
    if (modo !== "nuevos" || registro.cargado !== false) registro.cargado = true;
    registro.total = d.total;
    registro.tipos = d.tipos;
  } catch (e) {
    registro.error = true;
  } finally {
    registro.cargando = false;
    renderRegistro();
  }
}

async function verificarRegistro() {
  ultimaIntegridad = Date.now();
  try {
    const r = await fetch("/api/log/verificar", { cache: "no-store" });
    integridad = r.ok ? await r.json() : null;
  } catch (e) {
    integridad = null;
  }
  renderRegistro();
}

// Se llama en cada sondeo mientras se mira la pestaña: trae lo nuevo y re-verifica cada 30 s.
function tickRegistro() {
  if (registro.cargando) return;
  if (!registro.cargado) cargarRegistro("todo");
  else cargarRegistro("nuevos");
  if (Date.now() - ultimaIntegridad > 30000) verificarRegistro();
}

function renderRegistro() {
  if (vista !== "registro") return;
  const caja = $("registro-integridad");
  caja.classList.remove("buena", "mala");
  vaciar(caja);
  if (registro.noDisponible) {
    caja.textContent = "El registro de eventos no está disponible en este servidor.";
  } else if (!integridad) {
    caja.textContent = "Verificando el registro…";
  } else if (integridad.ok) {
    caja.classList.add("buena");
    caja.appendChild(document.createTextNode(`✔ Registro íntegro: ${integridad.total} eventos verificados. `));
    caja.appendChild(crear("small", "", integridad.total ? `Último sello: ${integridad.ultimo_hash}` : ""));
  } else {
    caja.classList.add("mala");
    const MOTIVOS_INTEGRIDAD = {
      falta_un_evento: "falta un evento (hueco en la numeración)",
      cadena_rota: "la cadena no encaja con el evento anterior",
      evento_modificado: "el contenido del evento no coincide con su sello",
    };
    caja.textContent = `⚠ El registro fue alterado: la cadena se rompe en el evento #${integridad.primer_invalido} ` +
      `(${MOTIVOS_INTEGRIDAD[integridad.motivo] || integridad.motivo}). Avisa al responsable del sistema.`;
  }

  const firma = `${registro.eventos.length}:${registro.eventos[0] ? registro.eventos[0].id : 0}:${registro.hayMas}:${registro.error}`;
  const firmaT = registro.tipos.join("|");
  if (firmaT !== firmaTipos) {            // el selector de eventos se reconstruye solo si cambió
    firmaTipos = firmaT;
    const sel = $("reg-tipo");
    const actual = sel.value;
    vaciar(sel);
    const todos = crear("option", "", "Todos");
    todos.value = "";
    sel.appendChild(todos);
    for (const t of registro.tipos) {
      const o = crear("option", "", etiquetaEvento(t));
      o.value = t;
      sel.appendChild(o);
    }
    sel.value = actual;
  }
  if (firma === firmaRegistro) return;
  firmaRegistro = firma;

  const cuerpo = $("tabla-registro").tBodies[0];
  vaciar(cuerpo);
  if (registro.error && !registro.eventos.length) {
    filaVacia(cuerpo, 5, "No se pudo leer el registro.");
  } else if (!registro.eventos.length) {
    filaVacia(cuerpo, 5, registro.cargado ? "No hay eventos que coincidan." : "Cargando…");
  }
  for (const e of registro.eventos) {
    const tr = crear("tr");
    tr.appendChild(crear("td", "", String(e.id)));
    tr.appendChild(crear("td", "", fmtHora(e.ts_ms / 1000)));
    const tdTipo = crear("td");
    const punto = crear("span", "punto " + claseEvento(e.tipo));
    tdTipo.appendChild(punto);
    tdTipo.appendChild(document.createTextNode(" " + etiquetaEvento(e.tipo)));
    tr.appendChild(tdTipo);
    tr.appendChild(crear("td", "responsable", e.responsable));
    const tdDetalle = crear("td", "detalle", describirEvento(e));
    if (e.detalle && e.detalle.arranque) tdDetalle.title = `Ejecución del sistema #${e.detalle.arranque} · sello ${e.hash}`;
    tr.appendChild(tdDetalle);
    cuerpo.appendChild(tr);
  }
  $("registro-pie").textContent =
    `Mostrando ${registro.eventos.length} de ${registro.total} eventos en total` +
    (registro.hayMas ? ". Hay más antiguos." : ".");
  $("reg-mas").hidden = !registro.hayMas;
}

function aplicarFiltrosRegistro() {
  registro.cargado = false;
  firmaRegistro = null;
  registro.eventos = [];
  cargarRegistro("todo");
}
$("reg-aplicar").addEventListener("click", aplicarFiltrosRegistro);
$("reg-texto").addEventListener("keydown", (ev) => { if (ev.key === "Enter") aplicarFiltrosRegistro(); });
$("reg-limpiar").addEventListener("click", () => {
  for (const id of ["reg-tipo", "reg-texto", "reg-desde", "reg-hasta"]) $(id).value = "";
  aplicarFiltrosRegistro();
});
$("reg-mas").addEventListener("click", () => cargarRegistro("mas"));
$("reg-verificar").addEventListener("click", verificarRegistro);

function renderInventario() {
  $("inv-max").textContent = estado.max_lotes_con_fotos;
  const cuerpo = $("tabla-lotes").tBodies[0];
  vaciar(cuerpo);
  if (!estado.lotes.length) filaVacia(cuerpo, 5, "Aún no se ha recibido ningún lote.");
  for (const l of estado.lotes) {
    const tr = crear("tr", "clic");
    tr.appendChild(crear("td", "", "#" + l.id));
    tr.appendChild(crear("td", "", fmtHora(l.ts)));
    tr.appendChild(crear("td", "", l.origen));
    tr.appendChild(crear("td", "", String(l.n_imagenes)));
    tr.appendChild(crear("td", "", resultadoHumano(l.resultado)));
    tr.addEventListener("click", () => {
      loteSeleccionado = l.id;
      cambiarVista("inicio");
    });
    cuerpo.appendChild(tr);
  }
}

const borradores = new Map(); // alarma id -> destino a medio escribir
let firmaAlertas = null;

async function resolver(id, destino, nodoError) {
  nodoError.textContent = "";
  try {
    const resp = await fetch(`/api/alarmas/${id}/resolver`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destino: destino.trim().toUpperCase() }),
    });
    if (resp.status === 400) {
      nodoError.textContent = "Destino inválido: solo A-Z, 0-9 y _ (máx. 32).";
      return;
    }
    if (resp.status === 401 || resp.status === 403) {
      nodoError.textContent = "Inicia tu turno para resolver alarmas.";      // se conserva lo que escribió
      return;
    }
    borradores.delete(id);
  } finally {
    firmaAlertas = null;
    refrescar();
  }
}

function celdaAccion(a) {
  const td = crear("td");
  if (!a.activa) return td;
  const caja = crear("div", "accion");
  const bloqueado = !puedeOperar();
  if (bloqueado) caja.title = "Inicia tu turno para resolver alarmas";
  if (a.espera_decision) {
    const campo = crear("input");
    campo.setAttribute("list", "destinos");
    campo.placeholder = "TIPO_X o DESCARTE";
    campo.maxLength = 32;
    campo.value = borradores.get(a.id) || "";
    campo.addEventListener("input", () => borradores.set(a.id, campo.value));
    const error = crear("span", "error-chico");
    const confirmar = crear("button", "btn-chico", "Confirmar");
    confirmar.addEventListener("click", () => resolver(a.id, campo.value, error));
    campo.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") resolver(a.id, campo.value, error);
    });
    campo.disabled = bloqueado;
    confirmar.disabled = bloqueado;
    caja.append(campo, confirmar, error);
  } else {
    const cerrar = crear("button", "btn-chico", "Cerrar");
    cerrar.addEventListener("click", () => resolver(a.id, "", crear("span")));
    cerrar.disabled = bloqueado;
    caja.appendChild(cerrar);
  }
  td.appendChild(caja);
  return td;
}

function textoEstadoAlarma(a) {
  if (a.activa) return a.espera_decision ? "Esperando decisión del regente" : "Activa (antigua)";
  return a.destino ? `Resuelta → ${a.destino}` : "Cerrada";
}

function renderAlertas() {
  // Se reconstruye solo si cambió algo, para no borrar lo que el regente está escribiendo.
  const firma = JSON.stringify([estado.alarmas, estado.destinos_sugeridos, puedeOperar()]);
  if (firma === firmaAlertas) return;
  firmaAlertas = firma;

  $("alertas-bloqueo").hidden = puedeOperar();
  const lista = $("destinos");
  vaciar(lista);
  for (const d of estado.destinos_sugeridos) {
    const op = crear("option");
    op.value = d;
    lista.appendChild(op);
  }

  const cuerpo = $("tabla-alertas").tBodies[0];
  vaciar(cuerpo);
  if (!estado.alarmas.length) filaVacia(cuerpo, 6, "Sin alertas.");
  for (const a of estado.alarmas) {
    const tr = crear("tr");
    const tdPunto = crear("td");
    tdPunto.appendChild(crear("span", "punto " + claseAlarma(a)));
    tr.appendChild(tdPunto);
    tr.appendChild(crear("td", "", fmtHora(a.ts)));
    tr.appendChild(crear("td", "", motivoHumano(a.motivo)));
    tr.appendChild(crear("td", "", a.lote_id ? "#" + a.lote_id : "—"));
    tr.appendChild(crear("td", "", textoEstadoAlarma(a)));
    tr.appendChild(celdaAccion(a));
    cuerpo.appendChild(tr);
  }
}

function renderConfig() {
  const cuerpo = $("tabla-config").tBodies[0];
  vaciar(cuerpo);
  for (const c of estado.config) {
    const tr = crear("tr");
    tr.appendChild(crear("td", "", c.nombre));
    tr.appendChild(crear("td", "", c.valor));
    cuerpo.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// Visor de fotos ampliadas
const visor = { loteId: null, indice: 0, total: 0 };

function mostrarVisor() {
  const img = $("visor-img");
  img.src = `/foto/${visor.loteId}/${visor.indice}`;
  img.alt = `Cara ${visor.indice + 1}`;
  $("visor-pie").textContent =
    `Lote #${visor.loteId} · Cara ${visor.indice + 1} de ${visor.total}`;
  $("visor-ant").disabled = visor.indice === 0;
  $("visor-sig").disabled = visor.indice === visor.total - 1;
}

function abrirVisor(loteId, indice, total) {
  visor.loteId = loteId;
  visor.indice = indice;
  visor.total = total;
  mostrarVisor();
  if (!$("visor").open) $("visor").showModal();
}

function moverVisor(delta) {
  const nuevo = visor.indice + delta;
  if (nuevo < 0 || nuevo >= visor.total) return;
  visor.indice = nuevo;
  mostrarVisor();
}

$("visor-ant").addEventListener("click", () => moverVisor(-1));
$("visor-sig").addEventListener("click", () => moverVisor(1));
$("visor-cerrar").addEventListener("click", () => $("visor").close());
// Clic en el fondo oscuro (el propio <dialog>, no su contenido) cierra el visor.
$("visor").addEventListener("click", (ev) => {
  if (ev.target === $("visor")) $("visor").close();
});
document.addEventListener("keydown", (ev) => {
  if (!$("visor").open) return;
  if (ev.key === "ArrowLeft") moverVisor(-1);
  if (ev.key === "ArrowRight") moverVisor(1);
});

// ---------------------------------------------------------------------------
// Asistente: el servidor atiende de a una solicitud y una conversación. Todo el
// texto se pinta con textContent (nada de innerHTML).
const chat = { cargado: false, cargando: false, disponible: false, modo: "", ayuda: "", ocupado: false, tarjeta: null };

const ERRORES_ASISTENTE = {
  asistente_ocupado: "El asistente está ocupado con otra solicitud. Espera un momento.",
  propuesta_inexistente: "Esa propuesta ya no está vigente.",
  texto_invalido: "El mensaje está vacío o es demasiado largo (máx. 1000 caracteres).",
  asistente_no_disponible: "El asistente no está disponible.",
  error_interno: "El asistente tuvo un error interno (revisa el log de la PC).",
  sesion_requerida: "Inicia tu turno para ejecutar acciones del asistente.",
};

async function cargarEstadoAsistente() {
  if (chat.cargando) return;
  chat.cargando = true;
  try {
    const d = await (await fetch("/api/asistente/estado", { cache: "no-store" })).json();
    chat.disponible = d.disponible;
    chat.modo = d.modo;
    chat.ayuda = d.ayuda || "";
  } catch (e) {
    chat.disponible = false;
    chat.modo = "desactivado";
    chat.ayuda = "No se pudo consultar al asistente.";
  }
  chat.cargando = false;
  chat.cargado = true;
  renderAsistente();
}

function renderAsistente() {
  if (!chat.cargado) {
    cargarEstadoAsistente();
    $("asistente-aviso").textContent = "Consultando al asistente…";
    return;
  }
  const aviso = $("asistente-aviso");
  $("chat").hidden = !chat.disponible;
  $("chat-form").hidden = !chat.disponible;
  aviso.classList.toggle("prueba", chat.modo === "prueba");
  if (!chat.disponible) {
    aviso.textContent = "Asistente desactivado. " + chat.ayuda;
  } else if (chat.modo === "prueba") {
    aviso.textContent =
      "MODO DE PRUEBA (sin modelo): responde con reglas fijas a unas pocas preguntas " +
      "(alarmas, estado, últimas clasificaciones). No es un modelo de lenguaje.";
  } else {
    aviso.textContent =
      "Asistente local. Es una ayuda: no decide el destino de ninguna alarma, eso lo " +
      "decides tú en la pestaña Alertas.";
  }
}

function establecerOcupado(ocupado) {
  chat.ocupado = ocupado;
  $("chat-texto").disabled = ocupado;
  $("chat-enviar").disabled = ocupado;
  $("chat-nuevo").disabled = ocupado;
  if (!ocupado) $("chat-texto").focus();
}

function agregarMensaje(rol, texto) {
  const caja = $("chat");
  const m = crear("div", "mensaje " + rol);
  m.appendChild(crear("span", "autor", rol === "operador" ? "Tú" : rol === "error" ? "Error" : "Asistente"));
  m.appendChild(crear("p", "", texto));
  caja.appendChild(m);
  caja.scrollTop = caja.scrollHeight;
  return m;
}

function mostrarPropuesta(d) {
  const tarjeta = crear("div", "mensaje propuesta");
  tarjeta.appendChild(crear("span", "autor", "El asistente propone una acción"));
  tarjeta.appendChild(crear("p", "", d.resumen));
  const botones = crear("div", "accion");
  const ejecutar = crear("button", "btn-chico", "Ejecutar");
  const cancelar = crear("button", "btn-chico", "Cancelar");
  const cerrar = () => { ejecutar.disabled = true; cancelar.disabled = true; chat.tarjeta = null; };
  ejecutar.addEventListener("click", () => { cerrar(); llamarAsistente("confirmar", { id: d.id }); });
  cancelar.addEventListener("click", () => { cerrar(); llamarAsistente("cancelar", { id: d.id }); });
  botones.append(ejecutar, cancelar);
  tarjeta.appendChild(botones);
  $("chat").appendChild(tarjeta);
  $("chat").scrollTop = $("chat").scrollHeight;
  chat.tarjeta = { ejecutar, cancelar };
}

async function llamarAsistente(accion, cuerpo) {
  establecerOcupado(true);
  const pensando = agregarMensaje("asistente", "Pensando…");
  pensando.classList.add("pensando");
  try {
    const resp = await fetch("/api/asistente/" + accion, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo || {}),
    });
    const datos = await resp.json().catch(() => ({}));
    pensando.remove();
    if (!resp.ok) {
      agregarMensaje("error", ERRORES_ASISTENTE[datos.error] || `Error del asistente (${resp.status}).`);
    } else if (datos.tipo === "propuesta") {
      mostrarPropuesta(datos);
    } else if (datos.tipo === "respuesta") {
      agregarMensaje("asistente", datos.texto);
    }
    return resp.ok;
  } catch (e) {
    pensando.remove();
    agregarMensaje("error", "No se pudo contactar al servidor.");
    return false;
  } finally {
    establecerOcupado(false);
  }
}

$("chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const texto = $("chat-texto").value.trim();
  if (!texto || chat.ocupado) return;
  if (chat.tarjeta) {            // seguir escribiendo cancela la propuesta pendiente (lo hace el servidor)
    chat.tarjeta.ejecutar.disabled = true;
    chat.tarjeta.cancelar.disabled = true;
    chat.tarjeta = null;
  }
  agregarMensaje("operador", texto);
  $("chat-texto").value = "";
  llamarAsistente("mensaje", { texto });
});
$("chat-texto").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    $("chat-form").requestSubmit();
  }
});
$("chat-nuevo").addEventListener("click", async () => {
  if (await llamarAsistente("reiniciar")) {
    vaciar($("chat"));
    chat.tarjeta = null;
    agregarMensaje("asistente", "Conversación nueva.");
  }
});

const RENDERS = {
  inicio: () => { renderInicio(); renderAsistente(); },
  inventario: renderInventario,
  alertas: renderAlertas,
  registro: () => { tickRegistro(); renderRegistro(); },
  usuarios: () => { if (!usuariosCargados) cargarUsuarios(); renderUsuarios(); },
  config: renderConfig,
};

function render() {
  if (!estado) return;
  renderAcceso();
  renderOrangePi();
  renderBanner();
  RENDERS[vista]();
}

function cambiarVista(nueva) {
  vista = nueva;
  for (const b of document.querySelectorAll("#pestanas button")) {
    b.classList.toggle("activa", b.dataset.vista === nueva);
  }
  for (const v of ["inicio", "inventario", "alertas", "registro", "usuarios", "config"]) {
    $("vista-" + v).hidden = v !== nueva;
  }
  if (nueva === "usuarios") usuariosCargados = false;
  else codigoNuevo = null;                // un código recién generado no sobrevive a salir de la pestaña
  if (nueva === "registro") {            // al entrar se vuelve a leer todo y a verificar la cadena
    registro.cargado = false;
    ultimaIntegridad = 0;
  }
  claveFotos = null;
  firmaAlertas = null;
  render();
}

async function refrescar() {
  try {
    const resp = await fetch("/api/estado", { cache: "no-store" });
    if (!resp.ok) throw new Error(resp.status);
    estado = await resp.json();
    // Al llegar un lote nuevo, Inicio pasa a mostrarlo aunque el regente hubiera abierto
    // uno viejo desde Inventario (si no, quedaría fijo en ese lote para siempre).
    const masReciente = estado.lotes.length ? estado.lotes[0].id : null;
    if (masReciente !== ultimoLoteId) {
      ultimoLoteId = masReciente;
      loteSeleccionado = null;
    }
    $("conexion").hidden = true;
    render();
  } catch (e) {
    $("conexion").hidden = false;
  }
}

for (const b of document.querySelectorAll("#pestanas button")) {
  b.addEventListener("click", () => {
    cambiarVista(b.dataset.vista);
    history.replaceState(null, "", "#" + b.dataset.vista);     // la URL recuerda la pestaña (marcador)
  });
}
// http://127.0.0.1:8080/#registro abre directamente esa pestaña.
if (Object.prototype.hasOwnProperty.call(RENDERS, location.hash.slice(1))) cambiarVista(location.hash.slice(1));
refrescar();
setInterval(refrescar, INTERVALO_MS);
