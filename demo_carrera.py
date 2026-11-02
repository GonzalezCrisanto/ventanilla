"""Demo de la condición de carrera: versión insegura contra versión segura.

Las dos versiones reciben la misma carga: todos los clientes quieren el
mismo viaje y entran juntos cuando abre la venta. Para cada corrida se
muestra cuántos asientos se vendieron dos veces, cuántas ventas se
perdieron y qué le pasó a la pasarela de pago. La versión segura además
guarda las ventas en SQLite, y se controla que lo guardado coincida.

Uso:  python demo_carrera.py
      $env:WORKERS=1; python demo_carrera.py    # con un solo worker no hay carrera
"""
from pasajes import config
from pasajes.boleteria_insegura import BoleteriaInsegura
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import ASIENTOS_POR_MICRO, viaje_mas_pedido
from pasajes.pasarela import Pasarela
from pasajes.persistencia import RegistroVentas, contar_ventas
from pasajes.simulacion import correr_simulacion, crear_clientes
from pasajes.verificacion import verificar

# Qué se muestra de cada Informe: (etiqueta, nombre del campo)
FILAS = [
    ("Duración (s)", "duracion"),
    ("Compras confirmadas a clientes", "compras_informadas"),
    ("Boletos emitidos", "boletos_emitidos"),
    ("Asientos vendidos", "asientos_vendidos"),
    # Si una reserva abandonada vence cuando ya no queda nadie comprando, ese
    # asiento termina libre: por eso la segura puede vender 39 de 40.
    ("Reservas abandonadas (vencieron solas)", "abandonaron"),
    ("Asientos vendidos dos veces", "asientos_duplicados"),
    ("Boletos de más", "boletos_de_mas"),
    ("Ventas perdidas", "ventas_perdidas"),
    ("Cobros rechazados (pasarela saturada)", "cobros_rechazados"),
    ("Asientos colgados al terminar", "asientos_colgados"),
    ("Clientes que se cansaron de esperar", "sin_atender"),
    ("Clientes con error", "con_error"),
]


def correr_insegura():
    boleteria = BoleteriaInsegura(viaje_mas_pedido(), Pasarela())
    corrida = correr_simulacion(boleteria, crear_clientes(config.CLIENTES), config.WORKERS)
    return verificar(corrida)


def correr_segura():
    """Devuelve el informe y cuántas ventas quedaron guardadas en SQLite."""
    with RegistroVentas(config.RUTA_BASE) as registro:
        boleteria = BoleteriaSegura(viaje_mas_pedido(), Pasarela(), registro)
        corrida = correr_simulacion(boleteria, crear_clientes(config.CLIENTES), config.WORKERS)
    # Al salir del with, el escritor ya guardó todo y terminó.
    return verificar(corrida), contar_ventas(config.RUTA_BASE)


def imprimir_tabla(titulo, informes):
    print(f"\n{titulo}")
    encabezado = "".join(f"{'corrida ' + str(n):>12}" for n in range(1, len(informes) + 1))
    print(f"  {'':<40}{encabezado}")
    for etiqueta, campo in FILAS:
        valores = [getattr(informe, campo) for informe in informes]
        celdas = "".join(f"{v:>12.2f}" if isinstance(v, float) else f"{v:>12}" for v in valores)
        print(f"  {etiqueta:<40}{celdas}")


def main():
    print(f"{config.CLIENTES} clientes y {config.WORKERS} workers compiten por "
          f"1 viaje de {ASIENTOS_POR_MICRO} asientos ({config.REPETICIONES} corridas por versión).")
    print(f"La pasarela de pago soporta {config.CAPACIDAD_PASARELA} cobros a la vez.")

    inseguras = [correr_insegura() for _ in range(config.REPETICIONES)]
    imprimir_tabla(f"Versión 1: {BoleteriaInsegura.nombre}", inseguras)

    corridas_seguras = [correr_segura() for _ in range(config.REPETICIONES)]
    seguras = [informe for informe, _ in corridas_seguras]
    imprimir_tabla(f"Versión 2: {BoleteriaSegura.nombre}", seguras)
    guardadas = ", ".join(str(cantidad) for _, cantidad in corridas_seguras)
    print(f"  {'Ventas guardadas en SQLite':<40}{guardadas:>12}")

    print("\nResumen")
    for nombre, informes in (("insegura", inseguras), ("segura", seguras)):
        con_falla = sum(1 for informe in informes if informe.asientos_duplicados > 0)
        correctas = sum(1 for informe in informes if informe.esta_bien())
        print(f"  Versión {nombre:<9} asientos vendidos dos veces en {con_falla} de {len(informes)} corridas; "
              f"{correctas} corridas sin ningún problema.")
    coinciden = all(cantidad == informe.boletos_emitidos for informe, cantidad in corridas_seguras)
    print(f"  Lo guardado en SQLite coincide con los boletos emitidos: {'sí' if coinciden else 'NO'}.")


if __name__ == "__main__":
    main()
