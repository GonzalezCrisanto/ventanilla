// Optional frontend regressions: node --test tests/frontend.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function page(reply) {
  const nodes = new Map(), clock = { now: 0 };
  const element = () => ({ style: {}, children: [], textContent: '',
    replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    setAttribute() {}, addEventListener() {} });
  const document = { createElement: element, getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  } };
  const context = vm.createContext({ fetch: reply, document, clock,
    performance: { now: () => clock.now }, setInterval: () => 1, clearInterval() {} });
  const source = readFileSync(join(__dirname, '../web/app.js'), 'utf8').replace(/arrancar\(\);\s*$/, '');
  vm.runInContext(source + `
    estado.cliente = 1; estado.viaje = 'trip'; estado.elegido = 5;
    estado.reserva = { codigo: 'code', asiento: 5, precio: 100, total: 30000, venceEn: 30000 };
    globalThis.actions = { reservar, pagar, actualizarCuenta, estado };
  `, context);
  return context;
}

for (const action of ['reservar', 'pagar']) {
  test(`${action} recovers its controls after a rejected fetch`, async () => {
    let requests = 0;
    const context = page(async () => { requests++; throw new Error('offline'); });
    const reservation = context.actions.estado.reserva;
    await context.actions[action]();
    assert.equal(context.actions.estado.ocupado, false);
    assert.equal(context.actions.estado.enFila, false);
    assert.equal(context.actions.estado.reserva, reservation);
    assert.equal(requests, 1); // Never automatically retry an uncertain payment.
    assert.ok(context.document.getElementById('mensaje').textContent);
  });
}

for (const failure of ['network', '500']) {
  for (const expiresDuringRequest of [true, false]) {
    test(`uncertain ${failure} payment never asserts expiry (${expiresDuringRequest})`, async () => {
      let context, requests = 0;
      context = page(async () => {
        requests++;
        if (expiresDuringRequest) context.clock.now = 31000;
        if (failure === 'network') throw new Error('offline');
        return { ok: false, status: 500, json: async () => ({ error: 'storage failure' }) };
      });
      const reservation = context.actions.estado.reserva;
      await context.actions.pagar(); // Uses real rendering and countdown, not stubs.
      context.clock.now = 32000;
      context.actions.actualizarCuenta(); // The later interval must not claim release either.
      assert.equal(context.actions.estado.reserva, reservation);
      assert.doesNotMatch(context.document.getElementById('mensaje').textContent, /se liberó/);
      assert.equal(requests, 1);
    });
  }
}

test('a definite 503 still expires an unpaid reservation normally', async () => {
  const context = page(async () => ({ ok: false, status: 503, json: async () => ({ error: 'not executed' }) }));
  await context.actions.pagar();
  context.clock.now = 31000;
  context.actions.actualizarCuenta();
  assert.equal(context.actions.estado.reserva, null);
  assert.match(context.document.getElementById('mensaje').textContent, /se liberó/);
});

for (const status of [503, 500, 200, 409]) {
  test(`payment handles HTTP ${status} without losing retryable reservations`, async () => {
    const context = page(async () => ({ ok: status === 200, status,
      json: async () => ({ error: 'try again', mensaje: 'confirmed' }) }));
    await context.actions.pagar();
    assert.equal(context.actions.estado.ocupado, false);
    assert.equal(context.actions.estado.enFila, false);
    assert.equal(context.actions.estado.reserva !== null, status >= 500);
  });
}
