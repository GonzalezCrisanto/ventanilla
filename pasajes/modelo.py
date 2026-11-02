"""Modelo del dominio: Viaje, Asiento, Cliente y Reserva.

Estados de un asiento:

    LIBRE ──reservar──▶ RESERVADO ──iniciar pago──▶ EN_PAGO ──cobro ok──▶ VENDIDO
      ▲                     │                          │
      └────── vence ────────┘                          └── cobro rechazado ──▶ LIBRE

El estado EN_PAGO existe para que el vencimiento no libere un asiento
mientras su pago está en curso.

El modelo no sabe nada de hilos: solo guarda datos. Quién los protege y
cómo lo deciden las boleterías (la insegura no los protege; la segura
tiene un Lock por viaje). Todo lo que cambia de un viaje (sus asientos y
sus boletos) vive adentro del Viaje, así un solo lock alcanza para todo.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EstadoAsiento(Enum):
    LIBRE = "libre"
    RESERVADO = "reservado"
    EN_PAGO = "en pago"
    VENDIDO = "vendido"


class EstadoReserva(Enum):
    ACTIVA = "activa"          # retiene el asiento mientras el cliente paga
    CONFIRMADA = "confirmada"  # se cobró y se emitió el boleto
    VENCIDA = "vencida"        # pasó el tiempo sin confirmar
    RECHAZADA = "rechazada"    # la pasarela no aprobó el cobro


@dataclass(frozen=True)
class Cliente:
    id: int
    nombre: str


# eq=False: dos reservas son "la misma" solo si son el mismo objeto. El
# cliente se queda con su Reserva como comprobante y la presenta para
# confirmar; por eso no hace falta numerarlas con un contador compartido.
@dataclass(eq=False)
class Reserva:
    cliente: Cliente
    id_viaje: str
    numero_asiento: int
    vence: float  # instante de time.monotonic(); no cambia si alguien toca el reloj de la máquina
    estado: EstadoReserva = EstadoReserva.ACTIVA


@dataclass
class Asiento:
    numero: int
    estado: EstadoAsiento = EstadoAsiento.LIBRE
    reserva: Reserva | None = None  # la reserva que lo ocupa; None si está libre


@dataclass
class Viaje:
    id: str
    origen: str
    destino: str
    salida: datetime
    precio: int
    asientos: dict[int, Asiento]
    boletos: list[Reserva] = field(default_factory=list)  # una entrada por cada compra confirmada

    def numeros_en_estado(self, estado):
        """Números de asiento que están en el estado pedido."""
        return [numero for numero, asiento in self.asientos.items() if asiento.estado is estado]
