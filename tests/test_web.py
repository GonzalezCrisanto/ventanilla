"""Pruebas de la web: el servidor HTTP que atiende cada pedido en un hilo.

Lo central: aunque 20 navegadores pidan el mismo asiento en el mismo
instante, solo uno lo consigue. Cada pedido HTTP es un hilo distinto, y todos
pasan por la misma cola y el mismo lock del viaje que la consola.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from pasajes import config
from pasajes.persistencia import RegistroVentas
from pasajes.sistema import Sistema
from web import ServidorVentanilla

VIAJE = "BA-MDP-0800"


def pedir_http(metodo, url, datos=None):
    """Devuelve (código HTTP, cuerpo en bytes), también para los 4xx."""
    cuerpo = None if datos is None else json.dumps(datos).encode("utf-8")
    pedido = urllib.request.Request(url, data=cuerpo, method=metodo, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=10) as respuesta:
            return respuesta.status, respuesta.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def pedir_json(metodo, url, datos=None):
    estado, cuerpo = pedir_http(metodo, url, datos)
    return estado, json.loads(cuerpo)


def nueva_sesion(url):
    return pedir_json("POST", url + "/api/sesion", {})[1]["cliente"]


def levantar():
    """El sistema y el servidor en un puerto libre (el 0 le pide uno al sistema operativo)."""
    registro = RegistroVentas(config.RUTA_BASE).__enter__()
    sistema = Sistema(registro)
    sistema.iniciar()
    servidor = ServidorVentanilla(("127.0.0.1", 0), sistema)
    hilo = threading.Thread(target=servidor.serve_forever, name="servidor-web")
    hilo.start()
    return f"http://127.0.0.1:{servidor.server_address[1]}", sistema, servidor, hilo, registro


def apagar(sistema, servidor, hilo, registro):
    """El cierre ordenado de web.py. Acá serve_forever corre en otro hilo, así que se usa shutdown()."""
    servidor.shutdown()
    hilo.join(timeout=5)
    servidor.server_close()  # espera los hilos de pedidos en curso
    sistema.detener()
    registro.__exit__(None, None, None)


@pytest.fixture
def web():
    url, sistema, servidor, hilo, registro = levantar()
    try:
        yield url
    finally:
        apagar(sistema, servidor, hilo, registro)


def test_sirve_la_pagina_y_sus_archivos(web):
    estado, cuerpo = pedir_http("GET", web + "/")
    assert estado == 200 and b"ventanilla" in cuerpo
    assert pedir_http("GET", web + "/estaticos/app.js")[0] == 200
    assert pedir_http("GET", web + "/estaticos/fuentes/barlow-400.woff2")[0] == 200


def test_no_deja_leer_archivos_fuera_de_la_carpeta_web(web):
    assert pedir_http("GET", web + "/estaticos/../requirements.txt")[0] == 404


def test_compra_completa_por_http(web):
    cliente = nueva_sesion(web)

    estado, reserva = pedir_json("POST", web + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 5})
    assert estado == 201 and reserva["vence_en"] > 0

    estado, _ = pedir_json("POST", f"{web}/api/reservas/{reserva['codigo']}/pago", {"cliente": cliente})
    assert estado == 200

    _, mapa = pedir_json("GET", f"{web}/api/viajes/{VIAJE}?cliente={cliente}")
    assert {"numero": 5, "estado": "vendido", "tuyo": True} in mapa["asientos"]


def test_veinte_navegadores_piden_el_mismo_asiento_a_la_vez(web):
    clientes = [nueva_sesion(web) for _ in range(20)]
    largada = threading.Barrier(20)  # los 20 pedidos salen juntos
    estados = [None] * 20  # cada hilo escribe solo su casillero

    def intentar(i):
        largada.wait(timeout=5)
        estados[i] = pedir_http("POST", web + "/api/reservas",
                                {"cliente": clientes[i], "viaje": VIAJE, "asiento": 7})[0]

    hilos = [threading.Thread(target=intentar, args=(i,)) for i in range(20)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=15)
        assert not hilo.is_alive()

    assert estados.count(201) == 1
    assert estados.count(409) == 19


def test_no_se_puede_pagar_una_reserva_ajena(web):
    ana, beto = nueva_sesion(web), nueva_sesion(web)
    _, reserva = pedir_json("POST", web + "/api/reservas", {"cliente": ana, "viaje": VIAJE, "asiento": 9})

    estado, _ = pedir_json("POST", f"{web}/api/reservas/{reserva['codigo']}/pago", {"cliente": beto})

    assert estado == 403


def test_el_pago_puede_vencer_esperando_en_la_fila(monkeypatch, sin_deadlock):
    """La regla elegida: la reserva tiene que seguir vigente cuando un worker
    ATIENDE el pago. Con el único worker ocupado cobrando otra compra, el pago
    de Ana sale a tiempo, espera en la fila y la reserva vence en el medio."""
    monkeypatch.setattr(config, "WORKERS", 1)
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.3)
    monkeypatch.setattr(config, "DEMORA_PAGO", 0.6)  # el cobro de Beto ocupa al único worker

    def corrida():
        url, sistema, servidor, hilo, registro = levantar()
        try:
            ana, beto = nueva_sesion(url), nueva_sesion(url)
            _, de_beto = pedir_json("POST", url + "/api/reservas", {"cliente": beto, "viaje": VIAJE, "asiento": 6})
            _, de_ana = pedir_json("POST", url + "/api/reservas", {"cliente": ana, "viaje": VIAJE, "asiento": 5})
            cobro_de_beto = threading.Thread(target=pedir_http, args=(
                "POST", f"{url}/api/reservas/{de_beto['codigo']}/pago", {"cliente": beto}))
            cobro_de_beto.start()
            time.sleep(0.05)  # el worker ya está cobrando lo de Beto
            resultado = pedir_json("POST", f"{url}/api/reservas/{de_ana['codigo']}/pago", {"cliente": ana})
            cobro_de_beto.join(timeout=5)
            return resultado
        finally:
            apagar(sistema, servidor, hilo, registro)

    estado, respuesta = sin_deadlock(corrida)

    assert estado == 409
    assert "venció mientras el pago esperaba en la fila" in respuesta["error"]


def test_un_pago_tardio_avisa_que_la_reserva_ya_habia_vencido(web, monkeypatch):
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 0.1)
    cliente = nueva_sesion(web)
    _, reserva = pedir_json("POST", web + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 8})
    time.sleep(0.3)

    estado, respuesta = pedir_json("POST", f"{web}/api/reservas/{reserva['codigo']}/pago", {"cliente": cliente})

    assert estado == 409
    assert "ya había vencido" in respuesta["error"]


def test_un_pedido_que_nadie_atendio_no_deja_venta_ni_reserva(monkeypatch, sin_deadlock):
    """Con el único worker ocupado, los pedidos de Ana vencen sin que nadie los
    tome. La web le dice que no se hizo nada, y es verdad: ni venta ni reserva
    fantasma. Después, con el worker libre, Ana reintenta y compra."""
    monkeypatch.setattr(config, "WORKERS", 1)
    monkeypatch.setattr(config, "TIMEOUT_RESPUESTA", 0.5)
    monkeypatch.setattr(config, "DEMORA_PAGO", 2.0)  # el cobro de Beto ocupa al único worker 2 s
    monkeypatch.setattr(config, "TIEMPO_RESERVA", 30)

    def corrida():
        url, sistema, servidor, hilo, registro = levantar()
        try:
            ana, beto = nueva_sesion(url), nueva_sesion(url)
            _, de_beto = pedir_json("POST", url + "/api/reservas", {"cliente": beto, "viaje": VIAJE, "asiento": 6})
            _, de_ana = pedir_json("POST", url + "/api/reservas", {"cliente": ana, "viaje": VIAJE, "asiento": 5})
            cobro_de_beto = threading.Thread(target=pedir_http, args=(
                "POST", f"{url}/api/reservas/{de_beto['codigo']}/pago", {"cliente": beto}))
            cobro_de_beto.start()
            time.sleep(0.1)  # el worker ya está cobrando lo de Beto
            pago = pedir_http("POST", f"{url}/api/reservas/{de_ana['codigo']}/pago", {"cliente": ana})[0]
            reserva = pedir_http("POST", url + "/api/reservas", {"cliente": ana, "viaje": VIAJE, "asiento": 7})[0]
            cobro_de_beto.join(timeout=10)
            time.sleep(0.3)  # el worker descarta los pedidos abandonados
            _, mapa = pedir_json("GET", f"{url}/api/viajes/{VIAJE}?cliente={ana}")
            reintento = pedir_http("POST", f"{url}/api/reservas/{de_ana['codigo']}/pago", {"cliente": ana})[0]
            return pago, reserva, mapa["asientos"], reintento
        finally:
            apagar(sistema, servidor, hilo, registro)

    pago, reserva, asientos, reintento = sin_deadlock(corrida)

    assert pago == 503 and reserva == 503
    assert {"numero": 5, "estado": "reservado", "tuyo": True} in asientos  # no se cobró: sigue reservado
    assert {"numero": 7, "estado": "libre", "tuyo": False} in asientos     # no quedó una reserva fantasma
    assert reintento == 200


def test_valida_lo_que_manda_el_navegador(web):
    cliente = nueva_sesion(web)
    assert pedir_http("POST", web + "/api/reservas", {"cliente": cliente, "viaje": VIAJE, "asiento": 99})[0] == 400
    assert pedir_http("POST", web + "/api/reservas", {"cliente": "x", "viaje": VIAJE, "asiento": 1})[0] == 401
    assert pedir_http("POST", web + "/api/simulacion", {"cantidad": 0})[0] == 400


def test_el_cierre_no_deja_hilos_colgados(sin_deadlock):
    hilos_antes = set(threading.enumerate())

    def corrida():
        url, sistema, servidor, hilo, registro = levantar()
        assert pedir_http("POST", url + "/api/simulacion", {"cantidad": 30})[0] == 202
        assert pedir_http("GET", f"{url}/api/viajes/{VIAJE}?cliente=0")[0] == 200
        apagar(sistema, servidor, hilo, registro)

    sin_deadlock(corrida)

    sobrantes = [hilo.name for hilo in threading.enumerate() if hilo not in hilos_antes]
    assert sobrantes == []
