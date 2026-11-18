// Frontend de la demo: cambiar de boletería, reiniciar y las dos vistas.
// Se corre con: node --test tests/frontend_demo.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise((resolve) => setImmediate(resolve));

function page(reply, hash = '') {
  const nodes = new Map(), pedidos = [], vistaAdmin = [];
  const element = () => {
    const classes = new Set(), attrs = {};
    return { style: {}, children: [], textContent: '', className: '', dataset: {}, hidden: false, disabled: false,
      href: '', attrs, classes,
      classList: { toggle(c, on) { (on ? classes.add(c) : classes.delete(c)); }, add: (c) => classes.add(c),
                   remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
      replaceChildren() { this.children = []; },
      appendChild(child) { this.children.push(child); return child; },
      setAttribute(nombre, valor) { attrs[nombre] = valor; }, addEventListener() {} };
  };
  const document = { createElement: element, body: element(), title: '', querySelectorAll: () => [],
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); } };
  const memoria = new Map();
  const context = vm.createContext({
    document, location: { hash }, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {},
    performance: { now: () => 0 },
    sessionStorage: { getItem: (k) => memoria.get(k) ?? null, setItem: (k, v) => memoria.set(k, v) },
    fetch: async (ruta, opciones) => { pedidos.push({ ruta, metodo: opciones?.method }); return reply(ruta, opciones); },
    activarAdmin: (activo) => vistaAdmin.push(activo),
  });
  const source = readFileSync(join(__dirname, '../web/app.js'), 'utf8').replace(/arrancar\(\);\s*$/, '');
  vm.runInContext(source + `
    estado.cliente = 1; estado.viaje = 'trip'; estado.partida = 1; estado.modo = 'segura';
    estado.reserva = { codigo: 'code', asiento: 5, precio: 100, total: 30000, venceEn: 30000 };
    globalThis.app = { estado, sincronizarPartida, reiniciar, aplicarVista, aplicarModo };
  `, context);
  return { context, app: context.app, document, pedidos, vistaAdmin };
}

const json = (cuerpo, status = 200) => ({ ok: status < 400, status, json: async () => cuerpo });

test('si otra pestaña reinició, se descarta la reserva y se pide una sesión nueva', async () => {
  const { app, document, pedidos } = page(async () => json({ cliente: 77 }, 201));

  app.sincronizarPartida({ partida: 2, modo: 'segura' });
  await tick();

  assert.equal(app.estado.reserva, null);
  assert.equal(app.estado.partida, 2);
  assert.equal(app.estado.cliente, 77);
  assert.deepEqual(pedidos.map((p) => p.ruta), ['/api/sesion']);
  assert.match(document.getElementById('aviso').textContent, /reinició/);
});

test('con la misma simulación no se descarta nada', async () => {
  const { app, pedidos } = page(async () => json({}));
  const reserva = app.estado.reserva;

  app.sincronizarPartida({ partida: 1, modo: 'segura' });
  await tick();

  assert.equal(app.estado.reserva, reserva);
  assert.equal(pedidos.length, 0);
});

test('el modo que manda el servidor se refleja en la página (otra pestaña cambió de boletería)', () => {
  const { app, document } = page(async () => json({}));

  app.sincronizarPartida({ partida: 1, modo: 'insegura' });

  assert.equal(app.estado.modo, 'insegura');
  assert.equal(document.body.dataset.modo, 'insegura');
  assert.match(document.getElementById('modo-detalle').textContent, /Sin Lock/);
});

test('reiniciar cambia de modo, descarta la reserva y pide una sesión nueva', async () => {
  const { app, document, pedidos } = page(async (ruta) =>
    ruta === '/api/reinicio' ? json({ modo: 'insegura', partida: 2 }) : json({ cliente: 88 }, 201));

  await app.reiniciar('insegura');

  assert.deepEqual(pedidos.map((p) => p.ruta), ['/api/reinicio', '/api/sesion']);
  assert.equal(app.estado.modo, 'insegura');
  assert.equal(app.estado.partida, 2);
  assert.equal(app.estado.reserva, null);
  assert.equal(app.estado.cliente, 88);
  assert.equal(app.estado.reiniciando, false);
  assert.equal(document.getElementById('btn-reiniciar').attrs['aria-busy'], 'false');
  assert.equal(document.getElementById('btn-reiniciar').disabled, false);
  assert.match(document.getElementById('aviso').textContent, /insegura/);
});

test('si el reinicio falla, vuelve al modo anterior y deja los controles libres', async () => {
  for (const reply of [async () => { throw new Error('offline'); }, async () => json({ error: 'roto' }, 500)]) {
    const { app, document } = page(reply);
    const reserva = app.estado.reserva;

    await app.reiniciar('insegura');

    assert.equal(app.estado.modo, 'segura');
    assert.equal(document.body.dataset.modo, 'segura');
    assert.equal(app.estado.reiniciando, false);
    assert.equal(document.getElementById('btn-reiniciar').disabled, false);
    assert.equal(app.estado.reserva, reserva, 'si no se reinició, la reserva sigue valiendo');
    assert.match(document.getElementById('aviso').className, /error/);
  }
});

test('un segundo clic en Reiniciar mientras uno está en curso no manda otro pedido', async () => {
  let soltar;
  const { app, pedidos } = page((ruta) => ruta === '/api/reinicio'
    ? new Promise((resolve) => { soltar = () => resolve(json({ modo: 'segura', partida: 2 })); })
    : Promise.resolve(json({ cliente: 5 }, 201)));

  const primero = app.reiniciar();
  await tick();
  await app.reiniciar();  // segundo clic
  soltar();
  await primero;

  assert.equal(pedidos.filter((p) => p.ruta === '/api/reinicio').length, 1);
});

test('#/admin muestra el panel y avisa que se activó; #/ vuelve a la venta', () => {
  const { app, document, vistaAdmin } = page(async () => json({}), '#/admin');

  app.aplicarVista();
  assert.equal(document.getElementById('vista-admin').hidden, false);
  assert.equal(document.getElementById('vista-venta').hidden, true);
  assert.equal(document.getElementById('enlace-vista').href, '#/');
  assert.deepEqual(vistaAdmin, [true]);

});

test('un enlace interno que no es una vista (saltar al contenido) no cambia de vista', () => {
  const { app, document, vistaAdmin, context } = page(async () => json({}), '#/admin');
  app.aplicarVista();

  context.location.hash = '#principal';
  app.aplicarVista();

  assert.equal(document.getElementById('vista-admin').hidden, false);
  assert.deepEqual(vistaAdmin, [true]);  // no hubo un segundo cambio
});

test('volver a la venta apaga el panel', () => {
  const { app, document, vistaAdmin, context } = page(async () => json({}), '#/admin');
  app.aplicarVista();

  context.location.hash = '#/';
  app.aplicarVista();

  assert.equal(document.getElementById('vista-venta').hidden, false);
  assert.equal(document.getElementById('vista-admin').hidden, true);
  assert.deepEqual(vistaAdmin, [true, false]);
});
