"""El cliente que se cansa de esperar y el worker se disputan el pedido.

Antes, un pedido vencido por timeout quedaba en la cola y se ejecutaba igual:
el cliente podía quedarse con una compra que creía fallida, o con una reserva
fantasma. Ahora el pedido tiene un estado protegido por su propio lock: o lo
toma un worker, o lo abandona el cliente, nunca las dos cosas.
"""
import queue
import threading

import pytest

from pasajes import config
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viaje_mas_pedido
from pasajes.modelo import Cliente, EstadoAsiento
from pasajes.pasarela import Pasarela
from pasajes.simulacion import Pedido, PedidoAbandonado, hilo_worker, pedir

VIAJE = "BA-MDP-0800"
ANA = Cliente(1, "Ana")


def test_tomar_y_abandonar_se_excluyen():
    abandonado = Pedido("reservar", ANA, VIAJE, 5)
    assert abandonado.abandonar()
    assert not abandonado.tomar()  # el worker llega tarde: no lo procesa

    tomado = Pedido("reservar", ANA, VIAJE, 6)
    assert tomado.tomar()
    assert not tomado.abandonar()  # el cliente llega tarde: tiene que esperar el resultado


def test_un_pedido_abandonado_no_se_ejecuta():
    boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    cola = queue.Queue()
    pedido = Pedido("reservar", ANA, VIAJE, 5)
    pedido.abandonar()
    cola.put(pedido)
    cola.put(None)

    worker = threading.Thread(target=hilo_worker, args=(boleteria, cola))
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert boleteria.viajes[VIAJE].asientos[5].estado is EstadoAsiento.LIBRE
    assert pedido.respuesta.empty()  # nadie le contestó: no hizo falta


def test_si_ningun_worker_lo_toma_el_cliente_lo_abandona(monkeypatch):
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.1)
    pedido = Pedido("reservar", ANA, VIAJE, 5)

    with pytest.raises(PedidoAbandonado):
        pedir(queue.Queue(), pedido)  # una cola sin workers

    assert pedido.estado == "abandonado"


def test_con_la_cola_llena_el_pedido_ni_entra(monkeypatch):
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.1)
    cola = queue.Queue(maxsize=1)
    cola.put("otro pedido")

    with pytest.raises(PedidoAbandonado):
        pedir(cola, Pedido("reservar", ANA, VIAJE, 5))

    assert cola.qsize() == 1  # el pedido no entró: es seguro que no se ejecuta


def test_si_el_worker_ya_lo_tomo_el_cliente_espera_el_resultado(monkeypatch, sin_deadlock):
    """El cobro tarda más que el timeout, pero el worker lo tomó a tiempo:
    la compra sale y el cliente se entera, en vez de recibir un falso error."""
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.2)
    monkeypatch.setattr(config, "DEMORA_PAGO", 0.6)
    boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    reserva = boleteria.reservar(ANA, VIAJE, 5)
    cola = queue.Queue()
    worker = threading.Thread(target=hilo_worker, args=(boleteria, cola))
    worker.start()
    try:
        respuesta = sin_deadlock(pedir, cola, Pedido("confirmar", ANA, reserva=reserva))
    finally:
        cola.put(None)
        worker.join(timeout=5)

    assert respuesta == (True, "compra confirmada")
    assert boleteria.viajes[VIAJE].asientos[5].estado is EstadoAsiento.VENDIDO
