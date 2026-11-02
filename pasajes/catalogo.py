"""Viajes de ejemplo para la demo, los tests y el benchmark.

Rutas de larga distancia con su horario y su mapa de asientos. Sin marcas
reales. Cada función devuelve objetos nuevos: cada corrida empieza con
todos los asientos libres.
"""
from datetime import datetime

from pasajes.modelo import Asiento, Viaje

ASIENTOS_POR_MICRO = 40  # 10 filas de 2 + 2


def crear_viaje(id_viaje, origen, destino, salida, precio, cantidad_asientos=ASIENTOS_POR_MICRO):
    asientos = {numero: Asiento(numero) for numero in range(1, cantidad_asientos + 1)}
    return Viaje(id_viaje, origen, destino, salida, precio, asientos)


def viajes_de_ejemplo():
    """El catálogo completo: varias rutas y horarios."""
    return [
        crear_viaje("BA-MDP-0800", "Buenos Aires", "Mar del Plata", datetime(2026, 12, 19, 8, 0), 38000),
        crear_viaje("BA-MDP-2330", "Buenos Aires", "Mar del Plata", datetime(2026, 12, 19, 23, 30), 34000),
        crear_viaje("BA-COR-2200", "Buenos Aires", "Córdoba", datetime(2026, 12, 19, 22, 0), 52000),
        crear_viaje("ROS-MZA-2100", "Rosario", "Mendoza", datetime(2026, 12, 19, 21, 0), 61000),
    ]


def viaje_mas_pedido(cantidad_asientos=ASIENTOS_POR_MICRO):
    """Un solo viaje: todos los clientes compiten por los mismos asientos.

    Es el peor caso para la boletería, y el que usa la demo de la carrera."""
    return [
        crear_viaje("BA-MDP-0800", "Buenos Aires", "Mar del Plata", datetime(2026, 12, 19, 8, 0), 38000,
                    cantidad_asientos),
    ]
