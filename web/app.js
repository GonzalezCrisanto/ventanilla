// Ventanilla: la página pide el mapa cada medio segundo y le manda las compras al servidor.
// El estado de verdad (asientos, reservas, ventas) vive en el servidor; acá solo se muestra.

const estado = {
  cliente: null,   // id que nos dio el servidor, guardado en la pestaña
  viaje: null,     // viaje que se está mirando
  elegido: null,   // asiento tocado, todavía sin reservar
  reserva: null,   // {codigo, viaje, asiento, precio, venceEn, total}
  ocupado: false,  // hay un pedido de compra en curso
  enFila: false,   // el pago se mandó y espera en la cola del servidor
};

const $ = (id) => document.getElementById(id);
// Punto para los miles, a mano: no todos los navegadores traen el formato argentino.
const pesos = (monto) => "$" + String(monto).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
const botonesViaje = new Map();    // id del viaje -> su botón en la lista
const botonesAsiento = new Map();  // número -> su botón en el mapa
let mapaDibujado = null;           // de qué viaje son los botones del mapa
let relojReserva = null;
let pedidosEnFila = 0;             // último dato del servidor, para el aviso de la fila

// --- Hablar con el servidor ---------------------------------------------------

async function api(metodo, ruta, datos) {
  const opciones = { method: metodo, headers: {} };
  if (datos !== undefined) {
    opciones.headers["Content-Type"] = "application/json";
    opciones.body = JSON.stringify(datos);
  }
  const respuesta = await fetch(ruta, opciones);
  return { ok: respuesta.ok, estado: respuesta.status, cuerpo: await respuesta.json() };
}

async function iniciarSesion(forzar) {
  const guardado = sessionStorage.getItem("ventanilla-cliente");
  if (guardado && !forzar) {
    estado.cliente = Number(guardado);
    return;
  }
  const r = await api("POST", "/api/sesion", {});
  estado.cliente = r.cuerpo.cliente;
  sessionStorage.setItem("ventanilla-cliente", String(estado.cliente));
}

// --- Mostrar ---------------------------------------------------------------

function mostrarMensaje(texto, tipo) {
  const mensaje = $("mensaje");
  mensaje.textContent = texto;
  mensaje.className = "mensaje" + (tipo ? " " + tipo : "");
}

// La reserva tiene que seguir vigente cuando un worker ATIENDE el pago, no cuando se manda.
// Con mucha carga el pago espera en la fila, y conviene que el cliente lo sepa.
function textoAviso() {
  if (estado.reserva?.pagoIncierto) return "El resultado del pago es incierto. Consultá el estado del asiento antes de reintentar.";
  const pedidos = `${pedidosEnFila} ${pedidosEnFila === 1 ? "pedido" : "pedidos"}`;
  if (estado.enFila) {
    // Con la cola vacía el pago igual puede estar esperando: los workers están ocupados cobrando.
    const fila = pedidosEnFila > 0 ? `Tu pago está en la fila, con ${pedidos} esperando.` : "Tu pago se está procesando.";
    return `${fila} Si la reserva vence antes de que lo atiendan, el asiento se libera.`;
  }
  if (pedidosEnFila > 0) return `Hay ${pedidos} en la fila: pagá con tiempo.`;
  return "";
}

function ubicacion(numero) {
  const fila = Math.floor((numero - 1) / 4) + 1;
  const lado = [0, 3].includes((numero - 1) % 4) ? "ventanilla" : "pasillo";
  return `Fila ${fila}, ${lado}`;
}

function dibujarViajes(viajes) {
  const lista = $("lista-viajes");
  for (const viaje of viajes) {
    let boton = botonesViaje.get(viaje.id);
    if (!boton) {
      const item = document.createElement("li");
      boton = document.createElement("button");
      boton.className = "viaje";
      boton.innerHTML = '<span class="ruta"></span><span class="detalle"></span>';
      boton.addEventListener("click", () => elegirViaje(viaje.id));
      item.appendChild(boton);
      lista.appendChild(item);
      botonesViaje.set(viaje.id, boton);
    }
    boton.querySelector(".ruta").textContent = `${viaje.origen} a ${viaje.destino}`;
    boton.querySelector(".detalle").textContent = `${viaje.salida} · quedan ${viaje.libres}`;
    boton.classList.toggle("activo", viaje.id === estado.viaje);
    boton.setAttribute("aria-pressed", String(viaje.id === estado.viaje));
  }
}

function crearMapa(asientos) {
  const contenedor = $("asientos");
  contenedor.replaceChildren();
  botonesAsiento.clear();
  for (const asiento of asientos) {
    const boton = document.createElement("button");
    const posicion = (asiento.numero - 1) % 4;  // 0 y 1 a la izquierda del pasillo, 2 y 3 a la derecha
    boton.className = "asiento";
    boton.style.gridColumn = String(posicion < 2 ? posicion + 1 : posicion + 2);
    boton.style.gridRow = String(Math.floor((asiento.numero - 1) / 4) + 1);
    boton.addEventListener("click", () => tocarAsiento(asiento.numero));
    contenedor.appendChild(boton);
    botonesAsiento.set(asiento.numero, boton);
  }
  mapaDibujado = estado.viaje;
}

function dibujarMapa(datos) {
  if (mapaDibujado !== datos.viaje.id) crearMapa(datos.asientos);
  const v = datos.viaje;
  $("titulo-viaje").textContent = `${v.origen} a ${v.destino} · ${v.salida} · ${pesos(v.precio)}`;
  for (const asiento of datos.asientos) {
    const boton = botonesAsiento.get(asiento.numero);
    const texto = { libre: String(asiento.numero).padStart(2, "0"), reservado: "R", pagando: "$", vendido: "X" };
    boton.textContent = asiento.tuyo ? "TU" : texto[asiento.estado];
    boton.className = `asiento ${asiento.estado}` + (asiento.tuyo ? " tuyo" : "");
    boton.classList.toggle("elegido", asiento.numero === estado.elegido);
    boton.setAttribute("aria-label", `Asiento ${asiento.numero}, ${asiento.estado}` + (asiento.tuyo ? ", tuyo" : ""));
    boton.dataset.estado = asiento.estado;
    // La competencia en vivo: otro cliente tomó el asiento que estabas mirando.
    if (asiento.numero === estado.elegido && asiento.estado !== "libre" && !estado.ocupado) {
      estado.elegido = null;
      mostrarMensaje(`Otro cliente tomó el asiento ${asiento.numero} mientras lo mirabas.`, "error");
      dibujarCompra();
    }
  }
}

function mostrarMetricas(sistema) {
  $("m-hilos").textContent = sistema.hilos;
  $("m-cola").textContent = sistema.cola;
  $("m-cobros").textContent = `${sistema.cobros} de ${sistema.capacidad}`;
  $("m-comprando").textContent = sistema.comprando;
  $("m-vendidos").textContent = sistema.vendidos;
  $("m-cobros-caja").classList.toggle("lleno", sistema.cobros >= sistema.capacidad);
  pedidosEnFila = sistema.cola;
  const aviso = $("aviso-fila");
  if (aviso) aviso.textContent = textoAviso();
}

function dibujarCompra() {
  const panel = $("compra");
  panel.replaceChildren();
  const parrafo = (clase, texto) => {
    const p = document.createElement("div");
    p.className = clase;
    p.textContent = texto;
    panel.appendChild(p);
    return p;
  };
  const boton = (texto, accion) => {
    const b = document.createElement("button");
    b.className = "boton principal";
    b.textContent = estado.enFila ? "Pago en la fila…" : estado.ocupado ? "Un momento…" : texto;
    b.setAttribute("aria-busy", String(estado.ocupado));
    b.addEventListener("click", () => { if (!estado.ocupado) accion(); });
    panel.appendChild(b);
  };

  if (estado.reserva) {
    const r = estado.reserva;
    parrafo("asiento-grande", `Asiento ${r.asiento}`);
    parrafo("ubicacion", ubicacion(r.asiento));
    if (!r.pagoIncierto) {
      const vence = parrafo("vence", "");
      vence.innerHTML = 'Vence en <b id="cuenta"></b>';
      const reloj = parrafo("reloj", "");
      reloj.appendChild(document.createElement("div")).id = "barra";
    }
    parrafo("aviso", textoAviso()).id = "aviso-fila";
    boton(`Pagar ${pesos(r.precio)}`, pagar);
    actualizarCuenta();
  } else if (estado.elegido) {
    parrafo("asiento-grande", `Asiento ${estado.elegido}`);
    parrafo("ubicacion", ubicacion(estado.elegido));
    boton(`Reservar asiento ${estado.elegido}`, reservar);
  } else {
    parrafo("ayuda", "Tocá un asiento libre del micro.");
  }
}

function actualizarCuenta() {
  if (!estado.reserva || estado.reserva.pagoIncierto) return;
  const restante = Math.max(0, estado.reserva.venceEn - performance.now());
  const segundos = Math.ceil(restante / 1000);
  const cuenta = $("cuenta");
  if (cuenta) cuenta.textContent = `${Math.floor(segundos / 60)}:${String(segundos % 60).padStart(2, "0")}`;
  const barra = $("barra");
  if (barra) barra.style.width = `${(100 * restante) / estado.reserva.total}%`;
  if (restante === 0 && !estado.ocupado) {
    estado.reserva = null;
    clearInterval(relojReserva);
    mostrarMensaje("Tu reserva venció: el asiento se liberó.", "error");
    dibujarCompra();
  }
}

// --- Acciones ------------------------------------------------------------------

function elegirViaje(id) {
  estado.viaje = id;
  estado.elegido = null;
  for (const [idViaje, boton] of botonesViaje) {
    boton.classList.toggle("activo", idViaje === id);
    boton.setAttribute("aria-pressed", String(idViaje === id));
  }
  dibujarCompra();
}

function tocarAsiento(numero) {
  if (estado.reserva) {
    mostrarMensaje(estado.reserva.pagoIncierto ? textoAviso() : "Primero pagá tu reserva, o esperá a que venza.", "error");
    return;
  }
  if (botonesAsiento.get(numero).dataset.estado !== "libre") {
    mostrarMensaje(`El asiento ${numero} no está libre.`, "error");
    return;
  }
  estado.elegido = numero;
  mostrarMensaje("");
  dibujarCompra();
}

async function reservar() {
  estado.ocupado = true;
  dibujarCompra();
  try {
    const pedido = () => api("POST", "/api/reservas", { cliente: estado.cliente, viaje: estado.viaje, asiento: estado.elegido });
    let r = await pedido();
    if (r.estado === 401) {  // el servidor se reinició: pedimos una sesión nueva
      await iniciarSesion(true);
      r = await pedido();
    }
    if (r.ok) {
      const c = r.cuerpo;
      estado.reserva = { codigo: c.codigo, viaje: c.viaje, asiento: c.asiento, precio: c.precio,
                         venceEn: performance.now() + c.vence_en * 1000, total: c.vence_en * 1000 };
      estado.elegido = null;
      relojReserva = setInterval(actualizarCuenta, 250);
      mostrarMensaje(`Reservado: tenés ${Math.round(c.vence_en)} segundos para pagar.`, "ok");
    } else {
      estado.elegido = null;
      mostrarMensaje(r.cuerpo.error, "error");
    }
  } catch (error) {
    mostrarMensaje("Se perdió la conexión. Consultá el mapa antes de intentar reservar de nuevo.", "error");
  } finally {
    estado.ocupado = false;
    dibujarCompra();
  }
}

async function pagar() {
  estado.ocupado = true;
  estado.enFila = true;
  dibujarCompra();
  try {
    const r = await api("POST", `/api/reservas/${estado.reserva.codigo}/pago`, { cliente: estado.cliente });
    if (r.estado < 500) {
      estado.reserva = null;
      clearInterval(relojReserva);
    } else if (r.estado !== 503) {
      estado.reserva.pagoIncierto = true;
    }
    mostrarMensaje(r.ok ? r.cuerpo.mensaje : r.cuerpo.error, r.ok ? "ok" : "error");
  } catch (error) {
    estado.reserva.pagoIncierto = true;
    mostrarMensaje("Se perdió la conexión. No se pudo confirmar el resultado: consultá el estado de tu asiento antes de reintentar.", "error");
  } finally {
    estado.ocupado = false;
    estado.enFila = false;
    dibujarCompra();
  }
}

async function lanzarClientes(cantidad) {
  const r = await api("POST", "/api/simulacion", { cantidad });
  mostrarMensaje(r.ok ? `Salieron ${cantidad} clientes simulados a comprar.` : r.cuerpo.error, r.ok ? "ok" : "error");
}

// --- Consultas periódicas ---------------------------------------------------------
// Cada consulta sale cuando terminó la anterior (setTimeout, no setInterval):
// si el servidor tarda, los pedidos no se apilan.

async function actualizarMapa() {
  try {
    const r = await api("GET", `/api/viajes/${encodeURIComponent(estado.viaje)}?cliente=${estado.cliente}`);
    if (r.ok) {
      dibujarMapa(r.cuerpo);
      mostrarMetricas(r.cuerpo.sistema);
    }
  } catch (error) {
    mostrarMensaje("Se perdió la conexión con el servidor.", "error");
  }
  setTimeout(actualizarMapa, 500);
}

async function actualizarViajes() {
  try {
    const r = await api("GET", "/api/viajes");
    if (r.ok) dibujarViajes(r.cuerpo);
  } catch (error) {
    // el mapa ya avisa si se perdió la conexión
  }
  setTimeout(actualizarViajes, 2000);
}

async function arrancar() {
  await iniciarSesion(false);
  const r = await api("GET", "/api/viajes");
  estado.viaje = r.cuerpo[0].id;
  dibujarViajes(r.cuerpo);
  dibujarCompra();
  for (const boton of document.querySelectorAll("[data-cantidad]")) {
    boton.addEventListener("click", () => lanzarClientes(Number(boton.dataset.cantidad)));
  }
  actualizarMapa();
  setTimeout(actualizarViajes, 2000);
}

arrancar();
