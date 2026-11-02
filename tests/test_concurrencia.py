"""Pruebas con muchos hilos a la vez: lo que pide la consigna.

- La versión segura nunca vende un asiento dos veces y no pierde ventas.
- La versión insegura, con la misma carga, efectivamente falla.

Cada simulación corre con timeout (fixture sin_deadlock) y las pruebas se
repiten: con hilos, que una corrida salga bien puede ser suerte.
"""
import threading
import time

from pasajes import config
from pasajes.boleteria_insegura import BoleteriaInsegura
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import ASIENTOS_POR_MICRO, viaje_mas_pedido, viajes_de_ejemplo
from pasajes.modelo import Cliente, EstadoAsiento, EstadoReserva
from pasajes.pasarela import Pasarela
from pasajes.persistencia import RegistroVentas, contar_ventas
from pasajes.simulacion import correr_simulacion, crear_clientes
from pasajes.verificacion import verificar

CORRIDAS = 3


# --- Versión segura -----------------------------------------------------

def test_segura_nunca_vende_un_asiento_dos_veces(simular):
    for _ in range(CORRIDAS):
        _, informe = simular(BoleteriaSegura, clientes=200, workers=8)

        assert informe.asientos_duplicados == 0
        assert informe.boletos_de_mas == 0


def test_segura_no_pierde_ventas(simular):
    for _ in range(CORRIDAS):
        _, informe = simular(BoleteriaSegura, clientes=200, workers=8)

        assert informe.ventas_perdidas == 0
        assert informe.compras_informadas == informe.boletos_emitidos
        assert informe.asientos_colgados == 0
        assert informe.con_error == 0
        # 200 clientes para 40 asientos: se tienen que vender todos.
        assert informe.asientos_vendidos == ASIENTOS_POR_MICRO


def test_segura_guarda_en_sqlite_todo_lo_que_vende(sin_deadlock):
    def correr():
        with RegistroVentas(config.RUTA_BASE) as registro:
            boleteria = BoleteriaSegura(viajes_de_ejemplo(), Pasarela(), registro)
            corrida = correr_simulacion(boleteria, crear_clientes(300), 8)
        return corrida, registro

    corrida, registro = sin_deadlock(correr)
    informe = verificar(corrida)

    assert informe.esta_bien()
    assert contar_ventas(config.RUTA_BASE) == informe.boletos_emitidos
    assert registro.rechazadas_por_duplicado == 0


def test_el_semaforo_no_deja_saturar_la_pasarela(simular):
    _, insegura = simular(BoleteriaInsegura, clientes=200, workers=8)
    _, segura = simular(BoleteriaSegura, clientes=200, workers=8)

    assert insegura.cobros_rechazados > 0  # sin semáforo, la pasarela rebota cobros
    assert segura.cobros_rechazados == 0
    # Y no los hace pasar de a uno, como haría un Lock: hubo cobros en paralelo.
    assert segura.pico_cobros > 1


def test_las_reservas_abandonadas_vencen_y_se_liberan(simular, monkeypatch):
    monkeypatch.setattr(config, "PROB_ABANDONO", 0.3)
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.3)

    corrida, informe = simular(BoleteriaSegura, clientes=100, workers=8)
    abandonadas = [r.reserva for r in corrida.resultados if r.estado == "abandonó"]

    assert abandonadas  # con 30 % de abandono, alguna hay
    assert all(reserva.estado is EstadoReserva.VENCIDA for reserva in abandonadas)
    assert informe.esta_bien()  # incluye: ningún asiento quedó reservado ni en pago


def test_el_vencimiento_no_libera_un_asiento_durante_el_cobro(monkeypatch):
    """El problema que evita EN_PAGO: la reserva vence mientras se está cobrando.

    Sin ese estado, el hilo de vencimientos liberaría el asiento en medio del
    cobro, otro cliente lo reservaría y al final habría dos boletos."""
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.1)
    monkeypatch.setattr(config, "DEMORA_PAGO", 0.4)  # el cobro tarda más que la reserva
    boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela())
    viaje = boleteria.viajes["BA-MDP-0800"]
    reserva = boleteria.reservar(Cliente(1, "Ana"), viaje.id, 5)

    respuesta = {}
    cobro = threading.Thread(target=lambda: respuesta.update(confirmar=boleteria.confirmar(reserva)))
    cobro.start()
    time.sleep(0.2)  # la reserva ya pasó su vencimiento, pero el cobro sigue en curso

    boleteria.liberar_vencidas()
    assert viaje.asientos[5].estado is EstadoAsiento.EN_PAGO
    assert boleteria.reservar(Cliente(2, "Beto"), viaje.id, 5) is None

    cobro.join(timeout=5)
    assert not cobro.is_alive()
    assert respuesta["confirmar"] == (True, "compra confirmada")
    assert viaje.boletos == [reserva]


def test_con_clientes_impacientes_nadie_compra_sin_enterarse(simular, monkeypatch):
    """Muchos clientes se cansan de esperar, pero ninguno queda con una compra
    que cree fallida: su pedido se abandona y no se ejecuta. Antes de la
    cancelación cooperativa, esta corrida daba más boletos que compras informadas."""
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.05)
    monkeypatch.setattr(config, "DEMORA_PAGO", 0.1)
    for _ in range(CORRIDAS):
        _, informe = simular(BoleteriaSegura, clientes=200, workers=8)

        assert informe.sin_atender > 0  # la impaciencia de verdad ocurrió
        assert informe.compras_informadas == informe.boletos_emitidos
        assert informe.esta_bien()


# --- Versión insegura ---------------------------------------------------

def test_insegura_vende_asientos_dos_veces(simular, monkeypatch):
    # Pasarela sin límite: así la única falla posible es la que arregla el lock.
    monkeypatch.setattr(config, "CAPACIDAD_PASARELA", 1000)
    for _ in range(CORRIDAS):
        _, informe = simular(BoleteriaInsegura, clientes=200, workers=8)

        assert informe.asientos_duplicados > 0
        assert informe.boletos_emitidos > informe.asientos_vendidos
        assert not informe.esta_bien()


def test_insegura_con_un_solo_worker_no_tiene_carrera(simular, monkeypatch):
    """La cola atiende los pedidos de a uno: sin concurrencia no hay carrera,
    aunque no haya lock. La carrera aparece recién con dos o más workers."""
    monkeypatch.setattr(config, "CAPACIDAD_PASARELA", 1000)
    _, informe = simular(BoleteriaInsegura, clientes=200, workers=1)

    assert informe.esta_bien()
