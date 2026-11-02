"""Controles al final de una corrida (los usan la demo, los tests y el benchmark).

Qué significa "no se pierden ventas" en este proyecto:
    - Ningún asiento tiene más de un boleto.
    - Cada boleto emitido corresponde al asiento que quedó vendido a ese
      cliente (si quedó a nombre de otro, o libre, es una venta perdida).
    - A cada cliente al que se le dijo "compraste" le corresponde un boleto,
      y no hay boletos de clientes a los que no se les dijo.
    - No quedan asientos reservados ni en pago al terminar.
    - Ningún cliente recibió un error. El que se cansó de esperar no cuenta
      como falla: su pedido no se ejecutó, así que no tiene nada a su nombre.

Se llama con todos los hilos ya terminados: en ese momento nadie más toca
los datos, así que no hace falta ningún lock para leerlos.
"""
from collections import Counter
from dataclasses import dataclass

from pasajes.modelo import EstadoAsiento


@dataclass
class Informe:
    duracion: float
    compras_informadas: int   # clientes a los que se les dijo "compraste"
    boletos_emitidos: int
    asientos_vendidos: int
    asientos_duplicados: int  # asientos con más de un boleto
    boletos_de_mas: int       # boletos que sobran por esos duplicados
    ventas_perdidas: int      # boletos cuyo asiento quedó a nombre de otro, o libre
    cobros_rechazados: int    # la pasarela saturada rechazó el cobro
    pico_cobros: int          # máximo de cobros simultáneos que recibió la pasarela
    asientos_colgados: int    # reservados o en pago al terminar
    abandonaron: int          # clientes que reservaron y no pagaron: su reserva venció sola
    sin_atender: int         # clientes que se cansaron de esperar: su pedido no se ejecutó
    con_error: int            # clientes a los que un worker les devolvió un error

    def esta_bien(self):
        return (self.asientos_duplicados == 0
                and self.ventas_perdidas == 0
                and self.asientos_colgados == 0
                and self.con_error == 0
                and self.compras_informadas == self.boletos_emitidos)


def verificar(corrida):
    boletos = vendidos = duplicados = de_mas = perdidas = colgados = 0

    for viaje in corrida.boleteria.viajes.values():
        boletos += len(viaje.boletos)
        vendidos += len(viaje.numeros_en_estado(EstadoAsiento.VENDIDO))
        colgados += len(viaje.numeros_en_estado(EstadoAsiento.RESERVADO))
        colgados += len(viaje.numeros_en_estado(EstadoAsiento.EN_PAGO))

        boletos_por_asiento = Counter(boleto.numero_asiento for boleto in viaje.boletos)
        for cantidad in boletos_por_asiento.values():
            if cantidad > 1:
                duplicados += 1
                de_mas += cantidad - 1

        for boleto in viaje.boletos:
            asiento = viaje.asientos[boleto.numero_asiento]
            if asiento.estado is not EstadoAsiento.VENDIDO or asiento.reserva is not boleto:
                perdidas += 1

    return Informe(
        duracion=corrida.duracion,
        compras_informadas=sum(1 for r in corrida.resultados if r.estado == "compró"),
        boletos_emitidos=boletos,
        asientos_vendidos=vendidos,
        asientos_duplicados=duplicados,
        boletos_de_mas=de_mas,
        ventas_perdidas=perdidas,
        cobros_rechazados=corrida.boleteria.pasarela.rechazos_por_saturacion,
        pico_cobros=corrida.boleteria.pasarela.maximo_en_curso,
        asientos_colgados=colgados,
        abandonaron=sum(1 for r in corrida.resultados if r.estado == "abandonó"),
        sin_atender=sum(1 for r in corrida.resultados if r.estado == "sin atender"),
        con_error=sum(1 for r in corrida.resultados if r.estado == "error"),
    )
