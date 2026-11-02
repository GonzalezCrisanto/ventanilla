"""Lo que comparten todas las pruebas.

- config_rapida: achica los tiempos para que las pruebas tarden poco y fija
  todos los parámetros, así una variable de entorno que quedó en la terminal
  no cambia el resultado. Cada prueba usa su propia base SQLite temporal.
- sin_deadlock: corre algo en otro hilo y falla si no termina a tiempo.
- simular: arma una boletería, corre la simulación (con timeout) y la verifica.
"""
import threading

import pytest

from pasajes import config
from pasajes.catalogo import viaje_mas_pedido
from pasajes.pasarela import Pasarela
from pasajes.simulacion import correr_simulacion, crear_clientes
from pasajes.verificacion import verificar

TIMEOUT = 30  # segundos; una simulación de prueba tarda bastante menos


@pytest.fixture(autouse=True)
def config_rapida(monkeypatch, tmp_path):
    valores = {
        "MAX_PEDIDOS_EN_COLA": 50,
        "TIEMPO_RESERVA": 2.0,
        "INTERVALO_VENCIMIENTOS": 0.02,
        "VENTANA_CARRERA": 0.001,
        "CAPACIDAD_PASARELA": 4,
        "PAGOS_SIMULTANEOS": 4,
        "DEMORA_PAGO": 0.01,
        "INTENTOS_POR_CLIENTE": 3,
        "DEMORA_CLIENTE_MIN": 0.005,
        "DEMORA_CLIENTE_MAX": 0.02,
        "PROB_ABANDONO": 0.0,
        "TIMEOUT_RESPUESTA": 10.0,
        "SEMILLA": 42,
        "RUTA_BASE": tmp_path / "ventas.db",
    }
    for nombre, valor in valores.items():
        monkeypatch.setattr(config, nombre, valor)


@pytest.fixture
def sin_deadlock():
    """Devuelve una función que corre funcion(*args) con un límite de tiempo.

    Si no termina en `timeout` segundos, la prueba falla: algo quedó trabado."""
    def correr(funcion, *args, timeout=TIMEOUT):
        resultado = {}

        def objetivo():
            try:
                resultado["valor"] = funcion(*args)
            except BaseException as error:  # se vuelve a lanzar en el hilo de la prueba
                resultado["error"] = error

        # Solo este hilo de control es daemon, para que una prueba trabada no
        # frene a las demás. Los hilos de la simulación no lo son.
        hilo = threading.Thread(target=objetivo, name="control-de-prueba", daemon=True)
        hilo.start()
        hilo.join(timeout)
        if hilo.is_alive():
            pytest.fail(f"no terminó en {timeout} s: hay un deadlock o un hilo colgado")
        if "error" in resultado:
            raise resultado["error"]
        return resultado["valor"]

    return correr


@pytest.fixture
def simular(sin_deadlock):
    """Devuelve una función que corre una simulación completa y la verifica."""
    def correr(clase_boleteria, clientes=200, workers=8, viajes=None):
        if viajes is None:
            viajes = viaje_mas_pedido()
        boleteria = clase_boleteria(viajes, Pasarela())
        corrida = sin_deadlock(correr_simulacion, boleteria, crear_clientes(clientes), workers)
        return corrida, verificar(corrida)

    return correr
