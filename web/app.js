// Ventanilla: la página pide el mapa cada medio segundo y le manda las compras al servidor.
// El estado de verdad (asientos, reservas, ventas) vive en el servidor; acá solo se muestra.
// Dos vistas en la misma página: la venta y el panel de admin (admin.js, #/admin).
// Arriba hay un botón para cambiar de boletería (segura o insegura) y otro para reiniciar.

const estado = {
  cliente: null,   // id que nos dio el servidor, guardado en la pestaña
  viaje: null,     // viaje que se está mirando
  elegido: null,   // asiento tocado, todavía sin reservar
  reserva: null,   // {codigo, viaje, asiento, precio, venceEn, total}
  ocupado: false,  // hay un pedido de compra en curso
  enFila: false,   // el pago se mandó y espera en la cola del servidor
  modo: null,      // "segura" o "insegura": la boletería que está funcionando
  partida: null,   // número de simulación; si cambia, alguien reinició y lo nuestro ya no vale
  reiniciando: false,
  vista: "venta",  // "venta" o "admin"
};

const DETALLE_MODO = {
  segura: "Cada viaje tiene su Lock y la pasarela un semáforo: nunca se vende un asiento dos veces.",
  insegura: "Sin Lock ni semáforo: con muchos clientes a la vez se vende un asiento dos veces y se satura la pasarela.",
};

const $ = (id) => document.getElementById(id);
// Punto para los miles, a mano: no todos los navegadores traen el formato argentino.
const pesos = (monto) => "$" + String(monto).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
const botonesViaje = new Map();    // id del viaje -> su botón en la lista
const botonesAsiento = new Map();  // número -> su botón en el mapa
let mapaDibujado = null;           // de qué viaje son los botones del mapa
let relojReserva = null;
let pedidosEnFila = 0;             // último dato del servidor, para el aviso de la fila
let temporizadorAviso = null;

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
  if (!r.ok) return;  // se está reiniciando: reservar() vuelve a pedir la sesión si hace falta
  estado.cliente = r.cuerpo.cliente;
  sessionStorage.setItem("ventanilla-cliente", String(estado.cliente));
}

// --- Mostrar ---------------------------------------------------------------

function mostrarMensaje(texto, tipo) {
  const mensaje = $("mensaje");
  mensaje.textContent = texto;
  mensaje.className = "mensaje" + (tipo ? " " + tipo : "");
}

// Aviso breve abajo de la pantalla, visible en las dos vistas.
function avisar(texto, tipo) {
  const aviso = $("aviso");
  aviso.textContent = texto;
  aviso.className = "aviso-global visible" + (tipo ? " " + tipo : "");
  clearTimeout(temporizadorAviso);
  temporizadorAviso = setTimeout(() => aviso.classList.remove("visible"), 3800);
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
    // El CSS decide la orientación del micro: parado en pantallas angostas, acostado en las anchas.
    boton.style.setProperty("--lado", String(posicion < 2 ? posicion + 1 : posicion + 2));
    boton.style.setProperty("--fila", String(Math.floor((asiento.numero - 1) / 4) + 1));
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
    boton.textContent = asiento.doble ? "2×" : asiento.tuyo ? "TU" : texto[asiento.estado];
    boton.className = `asiento ${asiento.estado}` + (asiento.tuyo ? " tuyo" : "") + (asiento.doble ? " doble" : "");
    boton.classList.toggle("elegido", asiento.numero === estado.elegido);
    boton.setAttribute("aria-label", `Asiento ${asiento.numero}, ${asiento.estado}` + (asiento.tuyo ? ", tuyo" : "")
                                     + (asiento.doble ? ", vendido a dos pasajeros" : ""));
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
  sincronizarPartida(sistema);
}

// El servidor dice qué boletería funciona y en qué simulación vamos. Si no coincide con lo que
// tenemos, alguien reinició o cambió de modo desde otra pestaña: se descarta lo nuestro.
function sincronizarPartida(sistema) {
  if (estado.reiniciando) return;  // el reinicio que hicimos nosotros lo resuelve reiniciar()
  if (estado.partida !== null && sistema.partida !== estado.partida) {
    estado.partida = sistema.partida;
    descartarCompra();
    iniciarSesion(true);
    avisar("La simulación se reinició.");
  }
  estado.partida = sistema.partida;
  if (sistema.modo !== estado.modo) aplicarModo(sistema.modo);
}

function aplicarModo(modo) {
  estado.modo = modo;
  document.body.dataset.modo = modo;
  for (const radio of document.querySelectorAll('input[name="modo"]')) radio.checked = radio.value === modo;
  $("modo-detalle").textContent = DETALLE_MODO[modo];
}

// Lo que quedó de la simulación anterior: la reserva en curso, el asiento elegido, los mensajes.
function descartarCompra() {
  clearInterval(relojReserva);
  estado.reserva = null;
  estado.elegido = null;
  mostrarMensaje("");
  dibujarCompra();
}

function marcarReinicio(reiniciando) {
  estado.reiniciando = reiniciando;
  document.body.classList.toggle("reiniciando", reiniciando);
  const boton = $("btn-reiniciar");
  boton.setAttribute("aria-busy", String(reiniciando));
  boton.disabled = reiniciando;
  $("txt-reiniciar").textContent = reiniciando ? "Reiniciando…" : "Reiniciar";
  for (const radio of document.querySelectorAll('input[name="modo"]')) radio.disabled = reiniciando;
}

// Reiniciar deja todos los asientos libres y la simulación en cero. Con `modo` también cambia de boletería.
async function reiniciar(modo) {
  if (estado.reiniciando) return;
  const anterior = estado.modo;
  marcarReinicio(true);
  if (modo) aplicarModo(modo);  // el cambio de color se ve al instante
  try {
    const r = await api("POST", "/api/reinicio", modo ? { modo } : {});
    if (!r.ok) throw new Error(r.cuerpo.error);
    estado.partida = r.cuerpo.partida;
    descartarCompra();
    aplicarModo(r.cuerpo.modo);
    await iniciarSesion(true);  // la memoria de la web también empezó de cero
    avisar(modo ? `Boletería ${r.cuerpo.modo}: simulación reiniciada.` : "Simulación reiniciada: todos los asientos están libres.");
  } catch (error) {
    aplicarModo(anterior);
    avisar("No se pudo reiniciar. Probá de nuevo.", "error");
  } finally {
    marcarReinicio(false);
  }
}

// --- Las dos vistas: #/ es la venta y #/admin el panel -------------------------------

function aplicarVista() {
  const hash = location.hash;
  if (hash !== "" && hash !== "#/" && hash !== "#/admin") return;  // otros enlaces (el de saltar) no cambian de vista
  const admin = hash === "#/admin";
  estado.vista = admin ? "admin" : "venta";
  document.body.dataset.vista = estado.vista;
  $("vista-venta").hidden = admin;
  $("vista-admin").hidden = !admin;
  const enlace = $("enlace-vista");
  enlace.href = admin ? "#/" : "#/admin";
  enlace.textContent = admin ? "← Volver a la venta" : "Panel de admin →";
  document.title = admin ? "Panel de admin · Ventanilla" : "Ventanilla · pasajes de micro";
  if (typeof activarAdmin === "function") activarAdmin(admin);
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
  try {
    const r = await api("POST", "/api/simulacion", { cantidad });
    avisar(r.ok ? `Salieron ${r.cuerpo.lanzados} clientes simulados a comprar.` : r.cuerpo.error, r.ok ? "" : "error");
  } catch (error) {
    avisar("Se perdió la conexión con el servidor.", "error");
  }
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

function conectarControles() {
  for (const boton of document.querySelectorAll("[data-cantidad]")) {
    boton.addEventListener("click", () => lanzarClientes(Number(boton.dataset.cantidad)));
  }
  for (const radio of document.querySelectorAll('input[name="modo"]')) {
    radio.addEventListener("change", () => { if (radio.checked && radio.value !== estado.modo) reiniciar(radio.value); });
  }
  $("btn-reiniciar").addEventListener("click", () => reiniciar());
  window.addEventListener("hashchange", aplicarVista);
}

async function cargarInicio() {
  await iniciarSesion(false);
  const r = await api("GET", "/api/viajes");
  if (!r.ok) {  // el servidor justo se está reiniciando: se vuelve a intentar enseguida
    setTimeout(cargarInicio, 500);
    return;
  }
  estado.viaje = r.cuerpo[0].id;
  dibujarViajes(r.cuerpo);
  dibujarCompra();
  actualizarMapa();
  setTimeout(actualizarViajes, 2000);
}

async function arrancar() {
  conectarControles();
  aplicarVista();
  await cargarInicio();
}

arrancar();
