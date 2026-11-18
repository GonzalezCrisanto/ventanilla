// Panel de admin: las estadísticas de la corrida. Se carga antes que app.js y usa sus ayudas
// ($, api, pesos), que existen para cuando esto se ejecuta.
//
// El panel se arma UNA vez y después cada segundo solo se actualizan valores y anchos: así el
// tooltip y el foco del teclado no se pierden cuando llegan datos nuevos. Todo lo que viene del
// servidor entra con textContent, nunca como HTML.

const SVG = "http://www.w3.org/2000/svg";

// Qué le pasó a cada reserva. El orden es el de la barra y el de la leyenda.
const RESULTADOS = [
  { clave: "compradas", clase: "s-comprada", nombre: "Compradas", explica: "Pagaron y tienen su boleto" },
  { clave: "en_curso", clase: "s-en_curso", nombre: "En curso", explica: "Reservaron y todavía pueden pagar" },
  { clave: "vencidas", clase: "s-vencida", nombre: "Reservaron y no pagaron", explica: "La reserva venció y el asiento se liberó" },
  { clave: "rechazadas", clase: "s-rechazada", nombre: "Pago rechazado", explica: "La pasarela saturada no aprobó el cobro" },
];
const ICONOS = {
  ok: '<svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="11" fill="currentColor"/><path d="M7 12.5l3.2 3.2L17 8.8" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  mal: '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M12 2.5l10.5 18.5h-21z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 9.5v5M12 17.6v.2" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>',
  aviso: '<svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="11" fill="currentColor"/><path d="M12 6.5v6.5M12 16.6v.2" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>',
  neutro: '<svg viewBox="0 0 24 24" width="26" height="26"><circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5.5l3.5 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

const VENTANA = 60;  // segundos del gráfico; el servidor manda un punto por segundo
const GRAFICO = { ancho: 680, alto: 210, izq: 34, der: 78, arriba: 12, abajo: 28 };

let panel = null;       // referencias a los elementos del panel, una vez armado
let cicloAdmin = 0;     // cada vez que se entra o sale de la vista cambia: corta los ciclos viejos
let ultimaActividad = [];
let puntoGrafico = null;  // columna del gráfico bajo el puntero o el teclado

// --- Ayudas ---------------------------------------------------------------------------------

function el(etiqueta, clase, texto) {
  const nodo = document.createElement(etiqueta);
  if (clase) nodo.className = clase;
  if (texto !== undefined) nodo.textContent = texto;
  return nodo;
}

function elSvg(etiqueta, atributos) {
  const nodo = document.createElementNS(SVG, etiqueta);
  for (const [nombre, valor] of Object.entries(atributos || {})) nodo.setAttribute(nombre, valor);
  return nodo;
}

// Solo toca el DOM si el texto cambió.
function poner(nodo, texto) {
  if (nodo.textContent !== String(texto)) nodo.textContent = String(texto);
}

const miles = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
const porcentaje = (parte, total) => (total > 0 ? Math.round((100 * parte) / total) : 0);
const plural = (n, uno, varios) => `${miles(n)} ${n === 1 ? uno : varios}`;

function reloj(segundos) {
  const s = Math.floor(segundos);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

// --- Tooltip: un solo globo para todo el panel ---------------------------------------------------
// Muestra lo mismo con el mouse y con el foco del teclado, y nunca es la única forma de leer un dato:
// cada cifra también está escrita en la leyenda o en la tabla.

function rellenarTip(titulo, filas) {
  const tip = $("tip");
  tip.replaceChildren();
  tip.appendChild(el("b", "", titulo));
  for (const fila of filas) {
    if (typeof fila === "string") {
      tip.appendChild(el("div", "", fila));
      continue;
    }
    const linea = el("div", "tip-fila");
    const clave = el("span", "clave " + fila.clave);
    linea.append(clave, el("span", "", fila.texto));
    tip.appendChild(linea);
  }
}

function ubicarTip(x, y) {
  const tip = $("tip");
  tip.hidden = false;
  let izquierda = x + 14, arriba = y + 16;
  if (izquierda + tip.offsetWidth > window.innerWidth - 8) izquierda = x - tip.offsetWidth - 14;
  if (arriba + tip.offsetHeight > window.innerHeight - 8) arriba = y - tip.offsetHeight - 14;
  tip.style.left = `${Math.max(8, izquierda)}px`;
  tip.style.top = `${Math.max(8, arriba)}px`;
}

function ocultarTip() { $("tip").hidden = true; }

function tipDeElemento(elemento, x, y) {
  const [titulo, ...resto] = elemento.dataset.tip.split("\n");
  rellenarTip(titulo, resto);
  ubicarTip(x, y);
}

function conectarTips() {
  const raiz = $("vista-admin");
  raiz.addEventListener("pointermove", (evento) => {
    if (evento.target.closest("#grafico-actividad")) return;  // el gráfico tiene el suyo
    const marca = evento.target.closest("[data-tip]");
    if (marca) tipDeElemento(marca, evento.clientX, evento.clientY);
    else ocultarTip();
  });
  raiz.addEventListener("pointerleave", ocultarTip);
  raiz.addEventListener("focusin", (evento) => {
    const marca = evento.target.closest("[data-tip]");
    if (!marca) return;
    const caja = marca.getBoundingClientRect();
    tipDeElemento(marca, caja.left + caja.width / 2, caja.top);
  });
  raiz.addEventListener("focusout", ocultarTip);
}

// --- Armado (una sola vez) ----------------------------------------------------------------------

function construirAdmin() {
  panel = { viajes: new Map(), segmentos: {}, leyenda: {} };

  // Barra alta de reservas + su leyenda
  const barra = $("barra-reservas");
  panel.vacia = el("div", "segmento vacia", "Todavía no hay reservas");
  barra.appendChild(panel.vacia);
  const leyenda = $("leyenda-reservas");
  for (const r of RESULTADOS) {
    const segmento = el("div", "segmento " + r.clase);
    segmento.tabIndex = 0;
    barra.appendChild(segmento);
    panel.segmentos[r.clave] = segmento;

    const item = el("li");
    const cuadro = el("span", "cuadro " + r.clase);
    const cifra = el("span", "cifra");
    item.append(cuadro, el("span", "nombre", r.nombre), cifra, el("span", "explica", r.explica));
    leyenda.appendChild(item);
    panel.leyenda[r.clave] = cifra;
  }

  construirGrafico();
  conectarTips();
}

function construirGrafico() {
  const svg = $("grafico-actividad");
  const g = GRAFICO;
  const base = g.alto - g.abajo;
  panel.grilla = [0, 1, 2].map(() => {
    const linea = elSvg("line", { class: "g-grilla", x1: g.izq, x2: g.ancho - g.der });
    const texto = elSvg("text", { class: "g-texto", x: g.izq - 8, "text-anchor": "end" });
    svg.append(linea, texto);
    return { linea, texto };
  });
  svg.appendChild(elSvg("line", { class: "g-base", x1: g.izq, x2: g.ancho - g.der, y1: base, y2: base }));
  const eje = [["hace 60 s", g.izq, "start"], ["hace 30 s", g.izq + (g.ancho - g.izq - g.der) / 2, "middle"], ["ahora", g.ancho - g.der, "end"]];
  for (const [texto, x, ancla] of eje) {
    const t = elSvg("text", { class: "g-texto", x, y: g.alto - 8, "text-anchor": ancla });
    t.textContent = texto;
    svg.appendChild(t);
  }
  panel.lineas = {};
  panel.puntos = {};
  panel.etiquetas = {};
  panel.cruz = elSvg("line", { class: "g-cruz", y1: g.arriba, y2: base, visibility: "hidden" });
  svg.appendChild(panel.cruz);
  for (const clave of ["reservas", "compras"]) {
    panel.lineas[clave] = elSvg("path", { class: "g-linea " + clave });
    svg.appendChild(panel.lineas[clave]);
  }
  for (const clave of ["reservas", "compras"]) {
    panel.puntos[clave] = elSvg("circle", { class: "g-punto " + clave, r: 4.5 });  // punta de la línea, o el punto marcado
    panel.etiquetas[clave] = elSvg("text", { class: "g-etiqueta" });
    panel.etiquetas[clave].textContent = clave === "reservas" ? "Reservas" : "Compras";
    svg.append(panel.puntos[clave], panel.etiquetas[clave]);
  }
  svg.addEventListener("pointermove", (evento) => {
    const caja = svg.getBoundingClientRect();
    const x = ((evento.clientX - caja.left) / caja.width) * g.ancho;
    puntoGrafico = Math.max(0, Math.min(VENTANA - 1, Math.round(((x - g.izq) / anchoPlano()) * (VENTANA - 1))));
    marcarPunto(evento.clientX, evento.clientY);
  });
  svg.addEventListener("pointerleave", () => soltarPunto());
  svg.addEventListener("blur", () => soltarPunto());
  svg.addEventListener("keydown", (evento) => {
    if (evento.key !== "ArrowLeft" && evento.key !== "ArrowRight") return;
    evento.preventDefault();
    const paso = evento.key === "ArrowLeft" ? -1 : 1;
    puntoGrafico = Math.max(0, Math.min(VENTANA - 1, (puntoGrafico === null ? VENTANA - 1 : puntoGrafico) + paso));
    const caja = svg.getBoundingClientRect();
    marcarPunto(caja.left + (xDe(puntoGrafico) / g.ancho) * caja.width, caja.top + caja.height / 3);
  });
}

const anchoPlano = () => GRAFICO.ancho - GRAFICO.izq - GRAFICO.der;
const xDe = (i) => GRAFICO.izq + (i * anchoPlano()) / (VENTANA - 1);

// --- Datos -> pantalla -----------------------------------------------------------------------

function dibujarAdmin(d) {
  dibujarVeredicto(d);
  dibujarKpis(d);
  dibujarReservas(d.reservas);
  dibujarOcupacion(d.viajes);
  dibujarPasarela(d);
  ultimaActividad = d.actividad;
  dibujarGrafico(d.actividad);
  dibujarCompras(d.ultimas_compras);
  dibujarHistorial(d);
  const modo = d.modo === "segura" ? "segura" : "insegura";
  poner($("admin-sub"), `Boletería ${modo} · funcionando hace ${reloj(d.segundos)} · se actualiza solo cada segundo`);
}

function dibujarVeredicto(d) {
  const v = d.veredicto;
  const caja = $("veredicto");
  if (caja.dataset.nivel !== v.nivel) {
    caja.className = "veredicto " + v.nivel;
    caja.dataset.nivel = v.nivel;
    $("v-icono").innerHTML = ICONOS[v.nivel];  // íconos fijos del código, no datos del servidor
  }
  poner($("v-heroe"), d.integridad.asientos_dobles);
  poner($("v-titulo"), v.titulo);
  poner($("v-detalle"), v.detalle);
}

function dibujarKpis(d) {
  const r = d.reservas, i = d.integridad;
  poner($("k-reservas"), miles(r.total));
  poner($("k-reservas-pie"), r.en_curso > 0 ? `${miles(r.en_curso)} todavía en curso` : "");
  poner($("k-compras"), miles(r.compradas));
  poner($("k-compras-pie"), i.boletos_de_mas > 0 ? `${plural(i.boletos, "boleto", "boletos")}, ${i.boletos_de_mas} de más` : "boletos emitidos");
  poner($("k-vencidas"), miles(r.vencidas));
  poner($("k-vencidas-pie"), r.total > 0 ? `${porcentaje(r.vencidas, r.total)}% de las reservas` : "");
  poner($("k-conversion"), r.conversion === null ? "—" : `${Math.round(r.conversion * 100)}%`);
  poner($("k-ingresos"), pesos(d.ingresos.validos));
  poner($("k-ingresos-pie"), d.ingresos.cobrado_de_mas > 0 ? `+${pesos(d.ingresos.cobrado_de_mas)} cobrado de más` : "por asientos vendidos");
}

function dibujarReservas(r) {
  const total = RESULTADOS.reduce((suma, x) => suma + r[x.clave], 0);
  const barra = $("barra-reservas");
  panel.vacia.hidden = total > 0;
  for (const x of RESULTADOS) {
    const valor = r[x.clave], segmento = panel.segmentos[x.clave], pct = porcentaje(valor, total);
    segmento.hidden = valor === 0;
    segmento.style.flexGrow = String(valor);
    poner(segmento, pct >= 14 ? `${pct}%` : "");  // si no entra con aire, no se escribe: la leyenda lo dice
    segmento.dataset.tip = `${plural(valor, "reserva", "reservas")}\n${x.nombre}: ${pct}% del total`;
    poner(panel.leyenda[x.clave], total > 0 ? `${miles(valor)} · ${pct}%` : "0");
  }
  barra.setAttribute("aria-label", total > 0
    ? "Resultado de las reservas: " + RESULTADOS.map((x) => `${miles(r[x.clave])} ${x.nombre.toLowerCase()}`).join(", ")
    : "Todavía no hay reservas");
  const notas = [];
  if (r.perdidas > 0) notas.push(`${plural(r.perdidas, "pedido perdió", "pedidos perdieron")} la carrera por el asiento: ya era de otro cliente.`);
  if (r.pagos_tarde > 0) notas.push(`${plural(r.pagos_tarde, "cliente intentó", "clientes intentaron")} pagar con la reserva ya vencida.`);
  poner($("nota-perdidas"), notas.join(" "));
}

function dibujarOcupacion(viajes) {
  const lista = $("lista-ocupacion");
  for (const v of viajes) {
    let fila = panel.viajes.get(v.id);
    if (!fila) {
      fila = { raiz: el("div", "fila-viaje"), segmentos: {} };
      const nombre = el("div", "fila-viaje-nombre");
      fila.nombre = el("span", "", `${v.origen} → ${v.destino}`);
      fila.salida = el("small", "", `${v.salida} · ${pesos(v.precio)}`);
      nombre.append(fila.nombre, fila.salida);
      const barra = el("div", "segmentos fina");
      for (const clave of ["vendidos", "reservados", "libres"]) {
        const segmento = el("div", "segmento s-" + clave.slice(0, -1));
        segmento.tabIndex = 0;
        barra.appendChild(segmento);
        fila.segmentos[clave] = segmento;
      }
      const cifras = el("div", "cifras");
      fila.cifras = el("span");
      fila.derecha = el("span", "cifras-derecha");
      fila.ingresos = el("b");
      fila.chip = el("span", "chip-doble");
      fila.chip.tabIndex = 0;
      fila.derecha.append(fila.ingresos, fila.chip);
      cifras.append(fila.cifras, fila.derecha);
      fila.raiz.append(nombre, barra, cifras);
      lista.appendChild(fila.raiz);
      panel.viajes.set(v.id, fila);
    }
    for (const clave of ["vendidos", "reservados", "libres"]) {
      const segmento = fila.segmentos[clave];
      segmento.hidden = v[clave] === 0;
      segmento.style.flexGrow = String(v[clave]);
      segmento.dataset.tip = `${v[clave]} ${clave}\n${v.origen} → ${v.destino}, ${v.salida}`;
    }
    poner(fila.cifras, `${v.vendidos} vendidos · ${v.reservados} reservados · ${v.libres} libres`);
    // Derecha: lo recaudado, y una marca roja si hay asientos con dos boletos
    poner(fila.ingresos, pesos(v.ingresos));
    fila.chip.hidden = v.dobles.length === 0;
    if (v.dobles.length > 0) {
      poner(fila.chip, plural(v.dobles.length, "asiento vendido dos veces", "asientos vendidos dos veces"));
      fila.chip.dataset.tip = `Con dos boletos\nAsientos: ${v.dobles.join(", ")}`;
    }
  }
}

function dibujarPasarela(d) {
  const p = d.pasarela, s = d.sistema;
  poner($("p-pico"), `${p.pico} de ${p.capacidad}`);
  $("p-relleno").style.width = `${porcentaje(p.pico, p.capacidad)}%`;
  $("p-medidor").classList.toggle("mal", p.rechazos > 0);
  poner($("p-rechazos"), miles(p.rechazos));
  const rojo = p.rechazos > 0;
  $("p-rechazos").closest(".dato-grande").classList.toggle("mal", rojo);
  const icono = $("p-icono");
  if (icono.dataset.nivel !== (rojo ? "mal" : "ok")) {
    icono.dataset.nivel = rojo ? "mal" : "ok";
    icono.innerHTML = ICONOS[rojo ? "mal" : "ok"];  // íconos fijos del código
    icono.style.color = rojo ? "var(--rojo)" : "var(--verde)";
  }
  poner($("s-cola"), s.cola);
  poner($("s-workers"), s.workers);
  poner($("s-hilos"), s.hilos);
  poner($("s-clientes"), s.clientes_lanzados);
}

// --- Gráfico de actividad ---------------------------------------------------------------------

function dibujarGrafico(actividad) {
  const g = GRAFICO, base = g.alto - g.abajo, alto = base - g.arriba;
  const maximo = Math.max(0, ...actividad.map((a) => Math.max(a.reservas, a.compras)));
  let techo = Math.max(4, maximo);
  if (techo % 2) techo += 1;
  panel.techo = techo;
  const yDe = (v) => base - (v / techo) * alto;

  [0, techo / 2, techo].forEach((valor, i) => {
    const { linea, texto } = panel.grilla[i];
    linea.setAttribute("y1", yDe(valor));
    linea.setAttribute("y2", yDe(valor));
    texto.setAttribute("y", yDe(valor) + 4);
    poner(texto, valor);
  });

  const ultimo = actividad.length - 1;
  const fin = {};
  for (const clave of ["reservas", "compras"]) {
    const camino = actividad.map((a, i) => `${i ? "L" : "M"}${xDe(i).toFixed(1)} ${yDe(a[clave]).toFixed(1)}`).join("");
    panel.lineas[clave].setAttribute("d", camino);
    fin[clave] = yDe(actividad[ultimo][clave]);
    panel.puntos[clave].setAttribute("cx", xDe(ultimo));
    panel.puntos[clave].setAttribute("cy", fin[clave]);
    panel.etiquetas[clave].setAttribute("x", xDe(ultimo) + 10);
    panel.etiquetas[clave].setAttribute("y", fin[clave] + 4);
  }
  // Si las dos puntas están pegadas, las etiquetas se pisarían: la leyenda de arriba las nombra
  const pegadas = Math.abs(fin.reservas - fin.compras) < 14;
  for (const clave of ["reservas", "compras"]) panel.etiquetas[clave].setAttribute("visibility", pegadas ? "hidden" : "visible");

  $("grafico-actividad").setAttribute("aria-label",
    `Reservas y compras por segundo en el último minuto. Máximo ${maximo} por segundo.`);
  if (puntoGrafico !== null) marcarPunto();
  dibujarTablaActividad(actividad);
}

// Marca una columna del gráfico: la cruz, los dos puntos y el globo con los valores de ese segundo.
function marcarPunto(x, y) {
  const g = GRAFICO, base = g.alto - g.abajo, alto = base - g.arriba;
  const a = ultimaActividad[puntoGrafico];
  if (!a) return;
  const techo = panel.techo;
  panel.cruz.setAttribute("x1", xDe(puntoGrafico));
  panel.cruz.setAttribute("x2", xDe(puntoGrafico));
  panel.cruz.setAttribute("visibility", "visible");
  for (const clave of ["reservas", "compras"]) {
    panel.puntos[clave].setAttribute("cx", xDe(puntoGrafico));
    panel.puntos[clave].setAttribute("cy", base - (a[clave] / techo) * alto);
    panel.etiquetas[clave].setAttribute("visibility", "hidden");
  }
  const hace = VENTANA - 1 - puntoGrafico;
  rellenarTip(hace === 0 ? "Ahora" : `Hace ${hace} s`, [
    { clave: "reservas", texto: `${plural(a.reservas, "reserva", "reservas")}` },
    { clave: "compras", texto: `${plural(a.compras, "compra", "compras")}` },
  ]);
  if (x !== undefined) ubicarTip(x, y);
}

function soltarPunto() {
  puntoGrafico = null;
  panel.cruz.setAttribute("visibility", "hidden");
  ocultarTip();
  if (ultimaActividad.length) dibujarGrafico(ultimaActividad);  // los puntos vuelven a la punta de la línea
}

function dibujarTablaActividad(actividad) {
  if (!document.querySelector(".tabla-alt").open) return;
  const cuerpo = $("tabla-actividad");
  cuerpo.replaceChildren();
  for (let tramo = 0; tramo < VENTANA / 5; tramo++) {
    const desde = actividad.length - 5 * (tramo + 1), hasta = actividad.length - 5 * tramo;
    const suma = (clave) => actividad.slice(desde, hasta).reduce((total, a) => total + a[clave], 0);
    const fila = el("tr");
    fila.append(el("td", "", tramo === 0 ? "últimos 5 s" : `hace ${5 * tramo}–${5 * (tramo + 1)} s`),
                el("td", "", suma("reservas")), el("td", "", suma("compras")));
    cuerpo.appendChild(fila);
  }
}

// --- Últimas compras y corridas anteriores ---------------------------------------------------------

function dibujarCompras(compras) {
  const lista = $("lista-compras");
  lista.replaceChildren();
  if (compras.length === 0) {
    lista.appendChild(el("li", "vacio", "Todavía no hay compras."));
    return;
  }
  for (const c of compras) {
    const item = el("li");
    item.append(el("span", "quien", c.cliente), el("span", "monto", pesos(c.precio)),
                el("span", "donde", `${c.viaje} · asiento ${c.asiento}`),
                el("span", "cuando", c.hace < 1.5 ? "ahora" : `hace ${Math.round(c.hace)} s`));
    lista.appendChild(item);
  }
}

function dibujarHistorial(d) {
  const r = d.reservas, i = d.integridad;
  // La corrida de ahora va primero, para compararla con las que ya terminaron
  const filas = [{ hora: "ahora", modo: d.modo, duracion: d.segundos, reservas: r.total, compradas: r.compradas,
                   vencidas: r.vencidas, rechazadas: r.rechazadas, asientos_dobles: i.asientos_dobles,
                   cobrado_de_mas: i.cobrado_de_mas, actual: true }]
    .concat([...d.historial].reverse());
  const clave = JSON.stringify(filas.map((f) => (f.actual ? { ...f, duracion: 0 } : f)));
  const contenedor = $("historial");
  if (contenedor.dataset.clave === clave) {  // nada cambió: no se redibuja, solo corre el reloj de la corrida de ahora
    if (panel.duracionActual) poner(panel.duracionActual, reloj(d.segundos));
    return;
  }
  contenedor.dataset.clave = clave;
  panel.duracionActual = null;

  contenedor.replaceChildren();
  if (d.historial.length === 0 && r.total === 0) {
    contenedor.appendChild(el("p", "vacio", "Todavía no hay corridas. Lanzá clientes simulados, reiniciá y probá con la otra boletería para comparar."));
    return;
  }
  const tabla = el("table");
  const cabecera = el("tr");
  for (const titulo of ["Corrida", "Boletería", "Reservas", "Compradas", "No pagaron", "Rechazadas", "Ventas dobles", "Cobrado de más", "Duración"]) {
    const th = el("th", "", titulo);
    th.scope = "col";
    cabecera.appendChild(th);
  }
  tabla.appendChild(el("thead")).appendChild(cabecera);
  const cuerpo = el("tbody");
  for (const f of filas) {
    const fila = el("tr");
    const modo = el("td");
    modo.appendChild(el("span", "insignia " + f.modo, f.modo === "segura" ? "Segura" : "Insegura"));
    const dobles = el("td", f.asientos_dobles > 0 ? "mal" : "", f.asientos_dobles);
    const dinero = el("td", f.cobrado_de_mas > 0 ? "mal" : "", f.cobrado_de_mas > 0 ? pesos(f.cobrado_de_mas) : "—");
    fila.append(el("td", "", f.actual ? "En curso" : f.hora), modo, el("td", "", miles(f.reservas)), el("td", "", miles(f.compradas)),
                el("td", "", miles(f.vencidas)), el("td", "", miles(f.rechazadas)), dobles, dinero, el("td", "", reloj(f.duracion)));
    if (f.actual) panel.duracionActual = fila.lastChild;
    cuerpo.appendChild(fila);
  }
  tabla.appendChild(cuerpo);
  const envoltorio = el("div", "tabla-scroll");
  envoltorio.appendChild(tabla);
  contenedor.appendChild(envoltorio);
}

// --- Entrar y salir de la vista ----------------------------------------------------------------------

async function pedirAdmin(ciclo) {
  if (ciclo !== cicloAdmin) return;  // se salió de la vista (o se entró de nuevo): este ciclo ya no corre
  try {
    const r = await api("GET", "/api/analiticas");
    if (r.ok && ciclo === cicloAdmin) dibujarAdmin(r.cuerpo);
  } catch (error) {
    // sin conexión: el mapa de la venta ya avisa; se reintenta en un segundo
  }
  if (ciclo === cicloAdmin) setTimeout(() => pedirAdmin(ciclo), 1000);
}

function activarAdmin(activo) {
  cicloAdmin += 1;
  if (!activo) {
    ocultarTip();
    return;
  }
  if (panel === null) construirAdmin();
  pedirAdmin(cicloAdmin);
}
