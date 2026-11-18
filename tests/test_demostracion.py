"""Pruebas de la demo: cambiar de boletería, reiniciar y el panel de admin.

Lo central:
- La boletería insegura puede vender un asiento dos veces y el panel lo muestra;
  la segura no, nunca.
- Reiniciar (o cambiar de modo) deja todo en cero, es rápido aunque haya
  clientes comprando y no deja ningún hilo vivo del sistema anterior.
- Lo que la web recordaba del sistema viejo (sesiones y reservas) no se
  puede usar en el nuevo.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.request

import pytest

from pasajes import config
from pasajes.analiticas import analizar
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viaje_mas_pedido
from pasajes.modelo import Cliente
from pasajes.pasarela import Pasarela
from pasajes.simulacion import Pedido, hilo_worker, pedir
from pasajes.sistema import Sistema
from web import ServidorVentanilla

VIAJE = "BA-MDP-0800"


def http(metodo, url, datos=None):
    """Devuelve (código HTTP, JSON), también para los 4xx y 5xx."""
    cuerpo = None if datos is None else json.dumps(datos).encode("utf-8")
    pedido = urllib.request.Request(url, data=cuerpo, method=metodo, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=15) as respuesta:
            return respuesta.status, json.loads(respuesta.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def nueva_sesion(url):
    return http("POST", url + "/api/sesion", {})[1]["cliente"]


@pytest.fixture
def servidor(monkeypatch):
    """La web completa arrancada en la boletería segura; con yield devuelve (url, servidor)."""
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 30)  # que ninguna reserva venza durante la prueba
    sistema = Sistema.abrir("segura", config.RUTA_BASE)
    servidor = ServidorVentanilla(("127.0.0.1", 0), sistema)
    hilo = threading.Thread(target=servidor.serve_forever, name="servidor-web")
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_address[1]}", servidor
    finally:
        servidor.shutdown()
        hilo.join(timeout=5)
        servidor.server_close()
        servidor.partida.sistema.cerrar()


def hacer_una_venta_doble(url, monkeypatch):
    """Dos clientes reservan el mismo asiento a la vez y los dos pagan. Solo posible sin Lock.

    Con una ventana de carrera de 50 ms, el segundo worker lee el asiento libre
    antes de que el primero lo escriba: las dos reservas son "válidas"."""
    monkeypatch.setattr(config, "VENTANA_CARRERA", 0.05)
    ana, beto = nueva_sesion(url), nueva_sesion(url)
    largada = threading.Barrier(2)
    reservas = {}

    def reservar(nombre, cliente):
        largada.wait(timeout=5)
        reservas[nombre] = http("POST", url + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 7})

    hilos = [threading.Thread(target=reservar, args=args) for args in (("ana", ana), ("beto", beto))]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)
    assert reservas["ana"][0] == 201 and reservas["beto"][0] == 201, reservas  # las dos "ganaron" el asiento
    for nombre, cliente in (("ana", ana), ("beto", beto)):
        codigo = reservas[nombre][1]["codigo"]
        assert http("POST", f"{url}/api/reservas/{codigo}/pago", {"cliente": cliente})[0] == 200


# --- Boletería insegura: el panel muestra la venta doble --------------------------------------

def test_la_insegura_vende_dos_veces_y_el_panel_y_el_mapa_lo_muestran(servidor, monkeypatch):
    url, srv = servidor
    assert http("POST", url + "/api/reinicio", {"modo": "insegura"})[0] == 200

    hacer_una_venta_doble(url, monkeypatch)

    _, a = http("GET", url + "/api/analiticas")
    assert a["modo"] == "insegura"
    assert a["integridad"]["asientos_dobles"] == 1
    assert a["integridad"]["boletos_de_mas"] == 1
    precio = 38000
    assert a["integridad"]["cobrado_de_mas"] == precio
    assert a["ingresos"]["validos"] == precio and a["ingresos"]["cobrado"] == 2 * precio
    assert a["veredicto"]["nivel"] == "mal"
    assert a["reservas"]["total"] == 2 and a["reservas"]["compradas"] == 2
    viaje = next(v for v in a["viajes"] if v["id"] == VIAJE)
    assert viaje["dobles"] == [7] and viaje["vendidos"] == 1

    _, mapa = http("GET", f"{url}/api/viajes/{VIAJE}?cliente=0")
    asientos = {s["numero"]: s for s in mapa["asientos"]}
    assert asientos[7]["doble"] is True
    assert "doble" not in asientos[8]  # el campo solo aparece cuando hay venta doble


def test_la_segura_no_marca_ventas_dobles_aunque_compitan_muchos(servidor):
    url, srv = servidor
    sistema = srv.partida.sistema

    sistema.lanzar_clientes(120)
    tope = time.monotonic() + 30
    while sistema.clientes_comprando() and time.monotonic() < tope:
        time.sleep(0.05)
    assert sistema.clientes_comprando() == 0

    _, a = http("GET", url + "/api/analiticas")
    assert a["integridad"]["asientos_dobles"] == 0 and a["integridad"]["cobrado_de_mas"] == 0
    assert a["veredicto"]["nivel"] == "ok"
    assert a["reservas"]["compradas"] == a["integridad"]["boletos"] > 0
    assert a["pasarela"]["rechazos"] == 0  # el semáforo la protege: nunca se satura
    assert a["pasarela"]["pico"] <= a["pasarela"]["capacidad"]


def test_el_embudo_de_reservas_suma_el_total(servidor):
    url, srv = servidor
    sistema = srv.partida.sistema
    sistema.lanzar_clientes(80)
    while sistema.clientes_comprando():
        time.sleep(0.05)

    r = http("GET", url + "/api/analiticas")[1]["reservas"]
    assert r["compradas"] + r["vencidas"] + r["rechazadas"] + r["en_curso"] == r["total"]
    assert r["conversion"] is not None and 0 <= r["conversion"] <= 1


# --- Reiniciar y cambiar de modo ----------------------------------------------------------------

def test_reiniciar_deja_todo_en_cero_cambia_de_modo_y_guarda_la_corrida(servidor):
    url, srv = servidor
    cliente = nueva_sesion(url)
    _, reserva = http("POST", url + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 5})
    assert http("POST", f"{url}/api/reservas/{reserva['codigo']}/pago", {"cliente": cliente})[0] == 200

    estado, cuerpo = http("POST", url + "/api/reinicio", {"modo": "insegura"})

    assert (estado, cuerpo) == (200, {"modo": "insegura", "partida": 2})
    _, a = http("GET", url + "/api/analiticas")
    assert a["modo"] == "insegura" and a["partida"] == 2
    assert a["reservas"]["total"] == 0 and a["ingresos"]["validos"] == 0
    assert all(v["libres"] == 40 for v in a["viajes"])
    assert len(a["historial"]) == 1  # la corrida anterior quedó guardada para comparar
    anterior = a["historial"][0]
    assert (anterior["modo"], anterior["reservas"], anterior["compradas"]) == ("segura", 1, 1)

    # Reiniciar sin modo repite el de ahora
    assert http("POST", url + "/api/reinicio", {})[1] == {"modo": "insegura", "partida": 3}


def test_una_corrida_sin_actividad_no_ensucia_el_historial(servidor):
    url, _ = servidor
    http("POST", url + "/api/reinicio", {})
    http("POST", url + "/api/reinicio", {"modo": "insegura"})
    assert http("GET", url + "/api/analiticas")[1]["historial"] == []


def test_tras_reiniciar_no_sirve_lo_que_la_web_recordaba(servidor):
    url, _ = servidor
    cliente = nueva_sesion(url)
    _, reserva = http("POST", url + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 5})

    http("POST", url + "/api/reinicio", {})

    # La sesión y la reserva eran del sistema anterior: pagarla en el nuevo vendería un asiento que nunca se reservó.
    assert http("POST", f"{url}/api/reservas/{reserva['codigo']}/pago", {"cliente": cliente})[0] == 404
    assert http("POST", url + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 6})[0] == 401
    _, mapa = http("GET", f"{url}/api/viajes/{VIAJE}?cliente={cliente}")
    assert {s["estado"] for s in mapa["asientos"]} == {"libre"}


def test_reiniciar_es_rapido_con_clientes_comprando_y_no_deja_hilos_vivos(servidor, monkeypatch, sin_deadlock):
    url, srv = servidor
    monkeypatch.setattr(config, "DEMORA_CLIENTE_MIN", 2.0)  # los clientes quedan "cargando la tarjeta"
    monkeypatch.setattr(config, "DEMORA_CLIENTE_MAX", 3.0)
    viejo = srv.partida.sistema
    assert http("POST", url + "/api/simulacion", {"cantidad": 150})[0] == 202
    time.sleep(0.5)
    assert viejo.clientes_comprando() > 100

    inicio = time.monotonic()
    estado, _ = sin_deadlock(http, "POST", url + "/api/reinicio", {"modo": "insegura"}, timeout=10)
    demora = time.monotonic() - inicio

    assert estado == 200
    assert demora < 2.0, f"reiniciar tardó {demora:.1f} s: esperó a que los clientes terminaran"
    hilos_del_viejo = [h for h, _ in viejo.clientes] + viejo.workers + [viejo.vencimientos]
    assert not any(h.is_alive() for h in hilos_del_viejo)
    assert viejo.cerrado and viejo.lanzar_clientes(5) == 0  # un pedido tardío no lanza nada en el sistema cerrado


def test_un_modo_desconocido_se_rechaza_y_no_reinicia(servidor):
    url, srv = servidor
    partida = srv.partida

    assert http("POST", url + "/api/reinicio", {"modo": "mágica"})[0] == 400

    assert srv.partida is partida


def test_sistema_rechaza_un_modo_desconocido():
    with pytest.raises(ValueError):
        Sistema(None, "mágica")


def test_cambiar_varias_veces_de_modo_no_acumula_hilos(servidor, sin_deadlock):
    url, _ = servidor
    antes = threading.active_count()

    for modo in ["insegura", "segura", "insegura", "segura"]:
        assert sin_deadlock(http, "POST", url + "/api/reinicio", {"modo": modo}, timeout=10)[0] == 200

    assert threading.active_count() == antes


def test_los_cuatro_viajes_y_las_cuentas_de_dinero_coinciden(servidor):
    url, srv = servidor
    srv.partida.sistema.lanzar_clientes(60)
    while srv.partida.sistema.clientes_comprando():
        time.sleep(0.05)

    a = http("GET", url + "/api/analiticas")[1]

    assert len(a["viajes"]) == 4
    assert a["ingresos"]["validos"] == sum(v["vendidos"] * v["precio"] for v in a["viajes"])


# --- Piezas sueltas ---------------------------------------------------------------------------------

def test_un_observador_que_falla_no_deja_al_cliente_sin_respuesta():
    """Las estadísticas son un extra: si fallan, el worker igual contesta y sigue vivo."""
    boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    cola = queue.Queue()

    def observador_roto(pedido, respuesta):
        raise RuntimeError("falló al anotar")

    worker = threading.Thread(target=hilo_worker, args=(boleteria, cola, observador_roto))
    worker.start()
    try:
        reserva = pedir(cola, Pedido("reservar", Cliente(1, "Ana"), VIAJE, 3))
        assert reserva is not None
        assert pedir(cola, Pedido("reservar", Cliente(2, "Beto"), VIAJE, 4)) is not None  # el worker sigue vivo
    finally:
        cola.put(None)
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_el_panel_arranca_neutro_sin_reservas(servidor):
    url, _ = servidor

    a = http("GET", url + "/api/analiticas")[1]

    assert a["veredicto"]["nivel"] == "neutro"
    assert a["reservas"]["total"] == 0 and a["reservas"]["conversion"] is None
    assert len(a["actividad"]) == 60 and a["ultimas_compras"] == []


def test_analizar_funciona_con_un_sistema_ya_cerrado(monkeypatch):
    sistema = Sistema.abrir("insegura", config.RUTA_BASE)
    try:
        sistema.lanzar_clientes(30)
        while sistema.clientes_comprando():
            time.sleep(0.05)
    finally:
        sistema.cerrar()

    a = analizar(sistema)

    assert a["modo"] == "insegura" and a["reservas"]["total"] > 0
