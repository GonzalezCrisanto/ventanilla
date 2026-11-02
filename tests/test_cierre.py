"""Finalización ordenada y ausencia de deadlocks.

Toda prueba que arranca hilos los espera con timeout: si algo se trabara,
la prueba falla en vez de quedarse esperando para siempre.
"""
import queue
import threading
import time

import pytest

from pasajes import config
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viaje_mas_pedido, viajes_de_ejemplo
from pasajes.modelo import Cliente, Reserva
from pasajes.pasarela import Pasarela
from pasajes.persistencia import RegistroVentas, contar_ventas
from pasajes.simulacion import Pedido, ResultadoCliente, hilo_cliente, hilo_vencimientos, hilo_worker

VIAJE = "BA-MDP-0800"


@pytest.mark.parametrize("workers", [1, 2, 8, 32])
def test_la_simulacion_termina_sin_hilos_colgados(simular, workers):
    hilos_antes = set(threading.enumerate())

    _, informe = simular(BoleteriaSegura, clientes=300, workers=workers, viajes=viajes_de_ejemplo())

    sobrantes = [hilo.name for hilo in threading.enumerate() if hilo not in hilos_antes]
    assert sobrantes == []
    assert informe.esta_bien()


def test_los_workers_atienden_todo_lo_encolado_antes_del_centinela():
    """Centinelas + FIFO: lo que entró a la cola antes del cierre se atiende igual."""
    boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    cola = queue.Queue()
    pedidos = [Pedido("reservar", Cliente(n, f"Pasajero {n}"), VIAJE, n) for n in range(1, 41)]
    for pedido in pedidos:
        cola.put(pedido)
    workers = [threading.Thread(target=hilo_worker, args=(boleteria, cola)) for _ in range(4)]
    for _ in workers:
        cola.put(None)  # los centinelas quedan detrás de los 40 pedidos

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    respuestas = [pedido.respuesta.get_nowait() for pedido in pedidos]
    assert all(respuesta is not None for respuesta in respuestas)  # 40 reservas, ninguna perdida


def test_el_hilo_de_vencimientos_se_apaga_enseguida(monkeypatch):
    """fin.set() lo despierta aunque esté en medio de una espera larga."""
    monkeypatch.setattr(config, "INTERVALO_VENCIMIENTOS", 60)
    fin = threading.Event()
    hilo = threading.Thread(target=hilo_vencimientos, args=(BoleteriaSegura(viaje_mas_pedido(), Pasarela()), fin))
    hilo.start()

    inicio = time.perf_counter()
    fin.set()
    hilo.join(timeout=2)

    assert not hilo.is_alive()
    assert time.perf_counter() - inicio < 1


def test_un_cliente_no_espera_para_siempre_si_nadie_atiende(monkeypatch):
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.2)
    cola = queue.Queue()  # no hay workers: nadie va a responder
    apertura = threading.Event()
    apertura.set()
    resultado = ResultadoCliente(Cliente(1, "Ana"))
    rutas = [("Buenos Aires", "Mar del Plata")]
    hilo = threading.Thread(target=hilo_cliente,
                            args=(BoleteriaSegura(viaje_mas_pedido(), Pasarela()), cola, rutas, apertura, resultado))

    hilo.start()
    hilo.join(timeout=2)

    assert not hilo.is_alive()
    assert resultado.estado == "sin atender"
    # Su pedido quedó en la cola marcado como abandonado: ningún worker lo va a ejecutar.
    assert cola.get_nowait().estado == "abandonado"


def test_el_escritor_guarda_todo_antes_de_cerrar(tmp_path):
    ruta = tmp_path / "ventas.db"
    viaje = viaje_mas_pedido()[0]

    with RegistroVentas(ruta) as registro:
        # 40 hilos registran a la vez, como harían los workers.
        hilos = [
            threading.Thread(target=registro.registrar,
                             args=(viaje, Reserva(Cliente(n, f"Pasajero {n}"), viaje.id, n, vence=0)))
            for n in range(1, 41)
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=5)
    # Al salir del with ya se mandó el centinela y el escritor terminó.

    assert not registro.hilo.is_alive()
    assert contar_ventas(ruta) == 40
    assert registro.rechazadas_por_duplicado == 0


def test_la_base_rechaza_un_segundo_boleto_para_el_mismo_asiento(tmp_path):
    ruta = tmp_path / "ventas.db"
    viaje = viaje_mas_pedido()[0]

    with RegistroVentas(ruta) as registro:
        registro.registrar(viaje, Reserva(Cliente(1, "Ana"), viaje.id, 5, vence=0))
        registro.registrar(viaje, Reserva(Cliente(2, "Beto"), viaje.id, 5, vence=0))

    assert contar_ventas(ruta) == 1
    assert registro.rechazadas_por_duplicado == 1
