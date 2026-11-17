"""Regressions for overlapping payments and background storage failures."""
import sqlite3
import threading
import time

import pytest

from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viaje_mas_pedido
from pasajes.modelo import Cliente, EstadoAsiento, Reserva
from pasajes.pasarela import Pasarela
from pasajes.persistencia import RegistroVentas, contar_ventas


@pytest.mark.parametrize("expired", [False, True])
def test_overlapping_payment_charges_and_records_only_once(tmp_path, expired):
    entered, release = threading.Event(), threading.Event()
    charges, results = [], []

    def charge(price):
        charges.append(price)
        if len(charges) == 1:
            entered.set()
            assert release.wait(5)
        return True

    with RegistroVentas(tmp_path / "sales.db") as registro:
        office = BoleteriaSegura(viaje_mas_pedido(), Pasarela(), registro)
        office.pasarela.cobrar = charge
        trip = next(iter(office.viajes.values()))
        reservation = office.reservar(Cliente(1, "Ana"), trip.id, 5)
        first = threading.Thread(target=lambda: results.append(office.confirmar(reservation)))
        first.start()
        try:
            assert entered.wait(5)
            if expired:
                reservation.vence = time.monotonic() - 1
            assert office.confirmar(reservation)[0] is False
            office.liberar_vencidas()
            assert trip.asientos[5].estado is EstadoAsiento.EN_PAGO
            assert trip.asientos[5].reserva is reservation
        finally:
            release.set()
            first.join(timeout=5)
        assert not first.is_alive()
        assert results == [(True, "compra confirmada")]
        assert len(charges) == 1 and trip.boletos == [reservation]
    assert contar_ventas(registro.ruta) == 1
    assert registro.rechazadas_por_duplicado == 0


def test_payment_rejects_a_reservation_that_does_not_own_the_seat():
    office = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    trip = next(iter(office.viajes.values()))
    original = office.reservar(Cliente(1, "Ana"), trip.id, 5)
    forged = Reserva(Cliente(2, "Beto"), trip.id, 5, original.vence)
    assert office.confirmar(forged)[0] is False
    assert trip.asientos[5].reserva is original and trip.boletos == []


def test_storage_startup_failure_is_reported_and_joined(tmp_path):
    registro = RegistroVentas(tmp_path)  # A directory is not a database file.
    with pytest.raises(RuntimeError):
        with registro:
            pass
    assert not registro.hilo.is_alive()


def test_storage_write_failure_reaches_register_and_shutdown(tmp_path, monkeypatch):
    connect = sqlite3.connect

    class BrokenConnection:
        def __init__(self, path):
            self.connection = connect(path)

        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("injected write failure")
            return self.connection.execute(sql, *args)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(sqlite3, "connect", BrokenConnection)
    registro = RegistroVentas(tmp_path / "sales.db").__enter__()
    trip = viaje_mas_pedido()[0]
    reservation = Reserva(Cliente(1, "Ana"), trip.id, 5, vence=0)
    try:
        registro.registrar(trip, reservation)
        registro.hilo.join(timeout=5)
        assert not registro.hilo.is_alive()
        with pytest.raises(RuntimeError):
            registro.registrar(trip, reservation)
        office = BoleteriaSegura([trip], Pasarela(), registro)
        pending = office.reservar(Cliente(2, "Beto"), trip.id, 6)
        with pytest.raises(RuntimeError):
            office.confirmar(pending)
        assert trip.asientos[6].estado is EstadoAsiento.RESERVADO
    finally:
        with pytest.raises(RuntimeError):
            registro.__exit__(None, None, None)
