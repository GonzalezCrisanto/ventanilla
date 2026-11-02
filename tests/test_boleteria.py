"""Pruebas de la boletería sin concurrencia: un solo hilo, paso a paso.

Corren con las dos versiones. Sin hilos de por medio se comportan igual:
la lógica es la misma, y la diferencia aparece recién con concurrencia
(ver test_concurrencia.py).
"""
import time

import pytest

from pasajes import config
from pasajes.boleteria_insegura import BoleteriaInsegura
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viaje_mas_pedido
from pasajes.modelo import Cliente, EstadoAsiento, EstadoReserva
from pasajes.pasarela import Pasarela

VIAJE = "BA-MDP-0800"
ANA = Cliente(1, "Ana")
BETO = Cliente(2, "Beto")


@pytest.fixture(params=[BoleteriaInsegura, BoleteriaSegura], ids=["insegura", "segura"])
def boleteria(request):
    return request.param(viaje_mas_pedido(), Pasarela())


def asiento(boleteria, numero):
    return boleteria.viajes[VIAJE].asientos[numero]


def test_reservar_un_asiento_libre(boleteria):
    reserva = boleteria.reservar(ANA, VIAJE, 5)

    assert reserva is not None and reserva.cliente is ANA
    assert asiento(boleteria, 5).estado is EstadoAsiento.RESERVADO
    assert 5 not in boleteria.asientos_libres(VIAJE)


def test_la_foto_muestra_estado_y_cliente_de_cada_asiento(boleteria):
    reserva = boleteria.reservar(ANA, VIAJE, 5)
    boleteria.confirmar(reserva)
    boleteria.reservar(BETO, VIAJE, 6)

    foto = boleteria.foto_asientos(VIAJE)

    assert foto[5] == (EstadoAsiento.VENDIDO, ANA)
    assert foto[6] == (EstadoAsiento.RESERVADO, BETO)
    assert foto[7] == (EstadoAsiento.LIBRE, None)


def test_no_se_puede_reservar_un_asiento_ocupado(boleteria):
    boleteria.reservar(ANA, VIAJE, 5)

    assert boleteria.reservar(BETO, VIAJE, 5) is None


def test_confirmar_emite_un_boleto(boleteria):
    reserva = boleteria.reservar(ANA, VIAJE, 5)

    assert boleteria.confirmar(reserva) == (True, "compra confirmada")
    assert reserva.estado is EstadoReserva.CONFIRMADA
    assert asiento(boleteria, 5).estado is EstadoAsiento.VENDIDO
    assert asiento(boleteria, 5).reserva is reserva
    assert boleteria.viajes[VIAJE].boletos == [reserva]


def test_no_se_puede_confirmar_dos_veces(boleteria):
    reserva = boleteria.reservar(ANA, VIAJE, 5)
    boleteria.confirmar(reserva)

    assert boleteria.confirmar(reserva) == (False, "la reserva está confirmada")
    assert len(boleteria.viajes[VIAJE].boletos) == 1


def test_una_reserva_vencida_no_se_puede_confirmar(boleteria, monkeypatch):
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.05)
    reserva = boleteria.reservar(ANA, VIAJE, 5)
    time.sleep(0.1)

    assert boleteria.confirmar(reserva) == (False, "la reserva venció")
    assert reserva.estado is EstadoReserva.VENCIDA
    assert asiento(boleteria, 5).estado is EstadoAsiento.LIBRE
    assert boleteria.viajes[VIAJE].boletos == []


def test_el_vencimiento_libera_el_asiento_para_otro_cliente(boleteria, monkeypatch):
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.05)
    reserva = boleteria.reservar(ANA, VIAJE, 5)
    time.sleep(0.1)

    boleteria.liberar_vencidas()

    assert reserva.estado is EstadoReserva.VENCIDA
    assert asiento(boleteria, 5).estado is EstadoAsiento.LIBRE
    assert boleteria.reservar(BETO, VIAJE, 5) is not None


def test_una_reserva_vigente_no_se_libera(boleteria):
    reserva = boleteria.reservar(ANA, VIAJE, 5)  # vence en 2 s

    boleteria.liberar_vencidas()

    assert reserva.estado is EstadoReserva.ACTIVA
    assert asiento(boleteria, 5).estado is EstadoAsiento.RESERVADO


def test_un_cobro_rechazado_libera_el_asiento(boleteria):
    boleteria.pasarela.capacidad = 0  # una pasarela que rechaza todo
    reserva = boleteria.reservar(ANA, VIAJE, 5)

    assert boleteria.confirmar(reserva) == (False, "la pasarela rechazó el cobro")
    assert reserva.estado is EstadoReserva.RECHAZADA
    assert asiento(boleteria, 5).estado is EstadoAsiento.LIBRE
    assert asiento(boleteria, 5).reserva is None
