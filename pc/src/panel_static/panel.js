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

// Botón "Iniciar recorrido": ordena a la Orange Pi iniciar el ciclo de auditoría
// (informe 4.7). La orden es de un solo uso y vence si nadie la recoge.
function renderRecorrido() {
  const o = estado.orden_ciclo;
  const boton = $("boton-recorrido");
  const texto = $("recorrido-estado");
  if (o.estado === "iniciada") {
    boton.disabled = true;
    $("boton-texto").textContent = "RECORRIDO INICIADO";
    texto.textContent = "La Orange Pi recogió la orden a las " + fmtHora(o.ts_entregada) + ".";
  } else if (o.estado === "ordenada") {
    boton.disabled = true;
    $("boton-texto").textContent = "ORDEN ENVIADA";
    texto.textContent = `Esperando que la Orange Pi la recoja (vence en ${o.restante_s} s).`;
  } else {
    boton.disabled = false;
    $("boton-texto").textContent = "INICIAR RECORRIDO";
    const hace = o.ts_ultima_consulta ? estado.ts_servidor - o.ts_ultima_consulta : null;
    texto.textContent = hace !== null && hace < 10
      ? "La Orange Pi está esperando la orden."
      : "La Orange Pi no ha consultado hace poco (puede estar apagada o ya en marcha).";
  }
}

$("boton-recorrido").addEventListener("click", async () => {
  $("boton-recorrido").disabled = true;       // evita doble clic mientras responde
  try {
    await fetch("/api/ciclo/iniciar", { method: "POST" });
  } finally {
    refrescar();
  }
});

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
    caja.append(campo, confirmar, error);
  } else {
    const cerrar = crear("button", "btn-chico", "Cerrar");
    cerrar.addEventListener("click", () => resolver(a.id, "", crear("span")));
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
  const firma = JSON.stringify([estado.alarmas, estado.destinos_sugeridos]);
  if (firma === firmaAlertas) return;
  firmaAlertas = firma;

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
  config: renderConfig,
};

function render() {
  if (!estado) return;
  renderBanner();
  RENDERS[vista]();
}

function cambiarVista(nueva) {
  vista = nueva;
  for (const b of document.querySelectorAll("#pestanas button")) {
    b.classList.toggle("activa", b.dataset.vista === nueva);
  }
  for (const v of ["inicio", "inventario", "alertas", "config"]) {
    $("vista-" + v).hidden = v !== nueva;
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
    $("conexion").hidden = true;
    render();
  } catch (e) {
    $("conexion").hidden = false;
  }
}

for (const b of document.querySelectorAll("#pestanas button")) {
  b.addEventListener("click", () => cambiarVista(b.dataset.vista));
}
refrescar();
setInterval(refrescar, INTERVALO_MS);
