"use strict";

const INTERVALO_MS = 2000;
const ERROR_REVISION = "ERROR_REVISION_MANUAL";
const MOTIVOS = {
  fallo_captura: "Fallo de captura (lote vacío)",
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
    banner.appendChild(crear("span", "",
      "ALARMA ACTIVA — " + motivoHumano(activas[0].motivo) +
      (esperando ? ". La Orange Pi espera tu decisión." : ". Revisa la caja manualmente.") +
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
    li.appendChild(crear("span", "punto " + (a.activa ? "rojo" : "")));
    const lote = a.lote_id ? ` · lote #${a.lote_id}` : "";
    li.appendChild(crear("span", "", motivoHumano(a.motivo) + lote));
    lista.appendChild(li);
  }
}

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
    tdPunto.appendChild(crear("span", "punto " + (a.activa ? "rojo" : "")));
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

const RENDERS = {
  inicio: renderInicio,
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
