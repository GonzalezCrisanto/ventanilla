"""Benchmark: cuánto se gana con concurrencia y dónde está el límite.

Experimento 1, el sistema. Un lote fijo de compras (reservar, cobrar y
confirmar, cada una en un asiento distinto) procesado por la versión segura:
    - secuencial: un solo hilo, un for, sin cola ni workers;
    - con 1, 2, 4, 8 y 16 workers: cola + un centinela por worker, como el sistema.
Se repite con una pasarela de 4 cobros a la vez (la de la demo) y con una
de 16. Casi todo el tiempo de una compra es esperar a la pasarela
(time.sleep, que suelta el GIL), así que los hilos esperan en paralelo.
Quedan afuera la demora de cada cliente y los choques al azar entre
clientes: no dependen de la concurrencia del servidor y hacen variar la medición.

Experimento 2, control para el GIL. La misma idea, pero cada tarea es
cálculo en Python puro (firmar un boleto). Se corre secuencial, con hilos y
con procesos. Lo único que cambia es la clase del pool:
ThreadPoolExecutor o ProcessPoolExecutor.

Cada medición se repite config.REPETICIONES veces y se usa la mediana.
Guarda resultados/benchmark.csv y resultados/benchmark.png.

Uso:  python benchmark.py
      $env:REPETICIONES=5; python benchmark.py
"""
import csv
import os
import platform
import queue
import statistics
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from pasajes import config
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viajes_de_ejemplo
from pasajes.modelo import Cliente
from pasajes.pasarela import Pasarela

WORKERS = [1, 2, 4, 8, 16]
CAPACIDADES_PASARELA = [4, 16]
HILOS_O_PROCESOS = [1, 2, 4, 8]
BOLETOS_A_FIRMAR = 32
ITERACIONES_FIRMA = 1_000_000  # unos 80 ms de CPU por boleto
CARPETA = config.CARPETA_PROYECTO / "resultados"

# Colores del gráfico: dos series validadas para daltonismo, tinta y grilla neutras.
SUPERFICIE, TINTA, TINTA_SECUNDARIA, TINTA_APAGADA = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRILLA, EJE = "#e1e0d9", "#c3c2b7"
SERIE_1, SERIE_2 = "#2a78d6", "#eb6834"


def medir(funcion, *args):
    inicio = time.perf_counter()
    funcion(*args)
    return time.perf_counter() - inicio


def mediana_de(una_corrida):
    """Corre una_corrida() varias veces (devuelve segundos) y se queda con la mediana."""
    return statistics.median(una_corrida() for _ in range(config.REPETICIONES))


# --- Experimento 1: el sistema ----------------------------------------------

def preparar(capacidad):
    """Una boletería nueva y la lista de compras: una por asiento, sin choques."""
    config.CAPACIDAD_PASARELA = capacidad
    config.PAGOS_SIMULTANEOS = capacidad
    viajes = viajes_de_ejemplo()[:2]  # 80 asientos: 80 compras
    boleteria = BoleteriaSegura(viajes, Pasarela())
    compras = []
    for viaje in viajes:
        for numero in viaje.asientos:
            n = len(compras) + 1
            compras.append((Cliente(n, f"Pasajero {n:03d}"), viaje.id, numero))
    return boleteria, compras


def comprar(boleteria, cliente, id_viaje, numero):
    reserva = boleteria.reservar(cliente, id_viaje, numero)
    boleteria.confirmar(reserva)


def secuencial(boleteria, compras):
    for compra in compras:
        comprar(boleteria, *compra)


def con_workers(boleteria, compras, workers):
    """El mismo esquema que el sistema: cola + workers + un centinela por worker."""
    cola = queue.Queue()
    for compra in compras:
        cola.put(compra)
    for _ in range(workers):
        cola.put(None)

    def worker():
        while True:
            compra = cola.get()
            if compra is None:  # centinela
                return
            comprar(boleteria, *compra)

    hilos = [threading.Thread(target=worker) for _ in range(workers)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()


def corrida_sistema(capacidad, workers):
    """Una corrida medida. workers=None es la versión secuencial."""
    boleteria, compras = preparar(capacidad)
    if workers is None:
        segundos = medir(secuencial, boleteria, compras)
    else:
        segundos = medir(con_workers, boleteria, compras, workers)

    # Una medición solo vale si la corrida fue correcta: todo vendido, sin duplicados.
    boletos = [(b.id_viaje, b.numero_asiento) for v in boleteria.viajes.values() for b in v.boletos]
    if len(boletos) != len(compras) or len(set(boletos)) != len(boletos):
        raise RuntimeError("la corrida medida no vendió todo o vendió de más")
    return segundos


# --- Experimento 2: control para el GIL --------------------------------------

def firmar_boleto(numero):
    """Trabajo de CPU en Python puro: un código de verificación para el boleto."""
    firma = numero
    for i in range(ITERACIONES_FIRMA):
        firma = (firma * 31 + i) % 1_000_000_007
    return firma


def firmar_secuencial():
    return [firmar_boleto(n) for n in range(BOLETOS_A_FIRMAR)]


def firmar_con_pool(clase_pool, cantidad):
    """Incluye crear y cerrar el pool: ese costo también se paga."""
    with clase_pool(max_workers=cantidad) as pool:
        return list(pool.map(firmar_boleto, range(BOLETOS_A_FIRMAR)))


# --- Resultados ----------------------------------------------------------------

def fila(experimento, variante, cantidad, segundos, base):
    print(f"  {variante:<28}{cantidad:>4}   {segundos:7.2f} s   x{base / segundos:5.2f}")
    return {"experimento": experimento, "variante": variante, "cantidad": cantidad,
            "segundos": round(segundos, 3), "aceleracion": round(base / segundos, 2)}


def guardar_csv(filas, ruta):
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def dibujar(filas, ruta, pie):
    # matplotlib se importa acá y no arriba: en Windows los procesos del
    # experimento 2 vuelven a importar este archivo, y así no lo cargan.
    import matplotlib
    matplotlib.use("Agg")  # dibuja a un archivo, sin abrir ventana
    import matplotlib.pyplot as plt

    def serie(experimento, variante):
        elegidas = [f for f in filas if f["experimento"] == experimento and f["variante"] == variante]
        return [f["cantidad"] for f in elegidas], [f["aceleracion"] for f in elegidas]

    figura, (izquierda, derecha) = plt.subplots(1, 2, figsize=(12, 5), facecolor=SUPERFICIE)
    # Cada panel: (eje, título, etiqueta del eje x, cantidades, series, nota (texto, x, y))
    paneles = [
        (izquierda, "Compras: casi todo el tiempo es esperar el cobro (E/S)", "Workers (hilos)", WORKERS, [
            ("Pasarela de 4 cobros a la vez (la de la demo)", *serie("sistema", "pasarela de 4"), SERIE_1),
            ("Pasarela de 16 cobros a la vez", *serie("sistema", "pasarela de 16"), SERIE_2),
        ], ("Techo: el semáforo deja pasar\n4 cobros a la vez", 8, 2.4)),
        (derecha, "Control: cálculo en Python puro (CPU)", "Hilos o procesos", HILOS_O_PROCESOS, [
            ("Hilos (ThreadPoolExecutor)", *serie("cpu", "hilos"), SERIE_1),
            ("Procesos (ProcessPoolExecutor)", *serie("cpu", "procesos"), SERIE_2),
        ], ("Con hilos no mejora: el GIL deja\nejecutar Python a un hilo por vez", 2.8, 0.45)),
    ]
    for eje, titulo, etiqueta_x, cantidades, series, (nota, nota_x, nota_y) in paneles:
        eje.set_facecolor(SUPERFICIE)
        eje.plot(cantidades, cantidades, color=TINTA_APAGADA, linewidth=1, label="Ideal: N veces más rápido con N")
        for orden, (nombre, xs, ys, color) in enumerate(series):
            # La primera serie va arriba: donde las dos coinciden, se ve la línea de la primera.
            eje.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=7, zorder=4 - orden,
                     markeredgecolor=SUPERFICIE, markeredgewidth=1.5, label=nombre)
            eje.annotate(f"×{ys[-1]:.1f}", (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                         va="center", color=TINTA_SECUNDARIA, fontsize=10)
        eje.text(nota_x, nota_y, nota, ha="center", va="center", color=TINTA_SECUNDARIA, fontsize=9)
        eje.set_xscale("log", base=2)
        eje.set_xticks(cantidades, [str(c) for c in cantidades])
        eje.minorticks_off()
        eje.set_ylim(0, max(cantidades) * 1.08)
        eje.set_xlim(cantidades[0] / 1.2, cantidades[-1] * 1.45)
        eje.grid(axis="y", color=GRILLA, linewidth=0.8)
        eje.set_axisbelow(True)
        for lado in ("top", "right"):
            eje.spines[lado].set_visible(False)
        for lado in ("left", "bottom"):
            eje.spines[lado].set_color(EJE)
        eje.tick_params(colors=TINTA_APAGADA, labelcolor=TINTA_SECUNDARIA)
        eje.set_title(titulo, loc="left", color=TINTA, fontsize=11)
        eje.set_xlabel(etiqueta_x, color=TINTA_SECUNDARIA)
        eje.set_ylabel("Veces más rápido que la versión secuencial", color=TINTA_SECUNDARIA)
        eje.legend(frameon=False, loc="upper left", labelcolor=TINTA_SECUNDARIA, fontsize=9, handlelength=3)

    figura.suptitle("¿Cuánto acelera la concurrencia?", x=0.01, ha="left", color=TINTA, fontsize=14)
    figura.text(0.01, -0.02, pie, color=TINTA_APAGADA, fontsize=9)
    figura.tight_layout()
    figura.savefig(ruta, dpi=150, facecolor=SUPERFICIE, bbox_inches="tight")
    plt.close(figura)


def main():
    gil = "con GIL" if getattr(sys, "_is_gil_enabled", lambda: True)() else "sin GIL"
    maquina = f"Python {platform.python_version()} {gil}, {os.cpu_count()} núcleos lógicos"
    print(f"{maquina}. Mediana de {config.REPETICIONES} corridas por medición.")
    filas = []

    demora = f"{config.DEMORA_PAGO * 1000:.0f} ms"
    print(f"\nExperimento 1: 80 compras con la versión segura (cada cobro tarda {demora})")
    base = mediana_de(lambda: corrida_sistema(4, None))
    filas.append(fila("sistema", "secuencial", 1, base, base))
    for capacidad in CAPACIDADES_PASARELA:
        for workers in WORKERS:
            segundos = mediana_de(lambda: corrida_sistema(capacidad, workers))
            filas.append(fila("sistema", f"pasarela de {capacidad}", workers, segundos, base))

    print(f"\nExperimento 2: firmar {BOLETOS_A_FIRMAR} boletos, cálculo en Python puro")
    base = mediana_de(lambda: medir(firmar_secuencial))
    filas.append(fila("cpu", "secuencial", 1, base, base))
    for nombre, clase_pool in (("hilos", ThreadPoolExecutor), ("procesos", ProcessPoolExecutor)):
        for cantidad in HILOS_O_PROCESOS:
            segundos = mediana_de(lambda: medir(firmar_con_pool, clase_pool, cantidad))
            filas.append(fila("cpu", nombre, cantidad, segundos, base))

    CARPETA.mkdir(exist_ok=True)
    guardar_csv(filas, CARPETA / "benchmark.csv")
    pie = (f"Mediana de {config.REPETICIONES} corridas. {maquina}. Izquierda: 80 compras con la versión segura, "
           f"cada cobro tarda {demora}. Derecha: firmar {BOLETOS_A_FIRMAR} boletos (incluye crear el pool).")
    dibujar(filas, CARPETA / "benchmark.png", pie)
    print(f"\nGuardado: {CARPETA / 'benchmark.csv'} y {CARPETA / 'benchmark.png'}")


if __name__ == "__main__":
    main()
