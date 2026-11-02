"""Interfaz de consola: el sistema funcionando, y vos comprando adentro.

Al abrir la consola arranca la boletería segura con todos sus hilos (los
workers, el de vencimientos y el escritor de SQLite) y queda funcionando
hasta que salís. Desde el menú podés:
    - ver los viajes y el mapa de asientos de cada micro;
    - comprar un pasaje: tu pedido entra a la MISMA cola que los clientes
      simulados, y lo atiende cualquiera de los workers;
    - lanzar clientes simulados que compiten con vos, y mirar en vivo cómo
      se llena el micro;
    - ver las últimas ventas guardadas en SQLite.

Al salir (opción 0 o Ctrl+C) se muestra el cierre ordenado paso a paso.

En la consola reserva una persona, así que la reserva dura
TIEMPO_RESERVA_CONSOLA (30 s por defecto). Además los clientes simulados
tardan algunos segundos en pagar y cada cobro medio segundo, para que la
competencia y el semáforo de la pasarela se vean en pantalla.

La consola, la web y demo_carrera.py usan la misma base: no los corras a la vez.
La clase Sistema, que mantiene todo funcionando, está en pasajes/sistema.py.

Uso:  python consola.py
      $env:TIEMPO_RESERVA_CONSOLA=120; python consola.py    # los 2 minutos reales
"""
import os
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

from pasajes import config
from pasajes.modelo import Cliente, EstadoAsiento
from pasajes.persistencia import RegistroVentas, contar_ventas, ultimas_ventas
from pasajes.simulacion import Pedido, PedidoAbandonado, pedir
from pasajes.sistema import Sistema

YO = Cliente(0, "Vos")  # los clientes simulados se numeran desde 1

# Cómo se dibuja cada asiento en el mapa (el libre muestra su número).
SIMBOLOS = {EstadoAsiento.RESERVADO: "R ", EstadoAsiento.EN_PAGO: "$ ", EstadoAsiento.VENDIDO: "X "}
LEYENDA = "07 = libre   R = reservado   $ = pagando   X = vendido   TU = tuyo"


# --- Ayudas para mostrar y preguntar ------------------------------------------

def precio(monto):
    return f"${monto:,}".replace(",", ".")


def descripcion(viaje):
    return f"{viaje.id}: {viaje.origen} -> {viaje.destino}, {viaje.salida:%d/%m %H:%M}, {precio(viaje.precio)}"


def leer_numero(mensaje, minimo, maximo):
    """Pide un número entre minimo y maximo. Devuelve None si se deja vacío o no es válido."""
    texto = input(mensaje).strip()
    if texto.isdigit() and minimo <= int(texto) <= maximo:
        return int(texto)
    if texto:
        print(f"  Tiene que ser un número entre {minimo} y {maximo}.")
    return None


def elegir_viaje(sistema):
    viajes = list(sistema.boleteria.viajes.values())
    for n, viaje in enumerate(viajes, start=1):
        print(f"  {n}. {descripcion(viaje)}")
    eleccion = leer_numero("Viaje (Enter para volver): ", 1, len(viajes))
    return None if eleccion is None else viajes[eleccion - 1].id


def contar_estados(foto):
    return Counter(estado for estado, _ in foto.values())


def mostrar_mapa(sistema, id_viaje):
    viaje = sistema.boleteria.viajes[id_viaje]
    foto = sistema.boleteria.foto_asientos(id_viaje)  # una sola lectura, con el lock del viaje

    def celda(numero):
        estado, cliente = foto[numero]
        if cliente is not None and cliente.id == YO.id:
            return "TU"
        return SIMBOLOS.get(estado, f"{numero:02d}")

    print(f"\n{descripcion(viaje)}")
    print("            ventanilla       ventanilla")
    numeros = sorted(foto)
    for inicio in range(0, len(numeros), 4):  # 2 asientos, pasillo, 2 asientos
        fila = numeros[inicio:inicio + 4]
        izquierda = "  ".join(celda(n) for n in fila[:2])
        derecha = "  ".join(celda(n) for n in fila[2:])
        print(f"  Fila {inicio // 4 + 1:>2}   {izquierda}    |    {derecha}")
    estados = contar_estados(foto)
    print(f"  {LEYENDA}")
    print(f"  Libres {estados[EstadoAsiento.LIBRE]}, reservados {estados[EstadoAsiento.RESERVADO]}, "
          f"pagando {estados[EstadoAsiento.EN_PAGO]}, vendidos {estados[EstadoAsiento.VENDIDO]}")


# --- Opciones del menú --------------------------------------------------------

def ver_viajes(sistema):
    print()
    for viaje in sistema.boleteria.viajes.values():
        estados = contar_estados(sistema.boleteria.foto_asientos(viaje.id))
        print(f"  {descripcion(viaje)}")
        print(f"      libres {estados[EstadoAsiento.LIBRE]:>2} | reservados {estados[EstadoAsiento.RESERVADO]:>2} | "
              f"pagando {estados[EstadoAsiento.EN_PAGO]:>2} | vendidos {estados[EstadoAsiento.VENDIDO]:>2}")


def ver_mapa(sistema):
    id_viaje = elegir_viaje(sistema)
    if id_viaje is not None:
        mostrar_mapa(sistema, id_viaje)


def comprar(sistema):
    id_viaje = elegir_viaje(sistema)
    if id_viaje is None:
        return
    mostrar_mapa(sistema, id_viaje)
    numero = leer_numero("Asiento (Enter para volver): ", 1, len(sistema.boleteria.viajes[id_viaje].asientos))
    if numero is None:
        return

    print("  Tu pedido entra a la misma cola que los clientes simulados...")
    try:
        reserva = pedir(sistema.cola, Pedido("reservar", YO, id_viaje, numero))
        if reserva is None:
            _, dueño = sistema.boleteria.foto_asientos(id_viaje)[numero]
            if dueño is not None and dueño.id == YO.id:
                print("  Ese asiento ya es tuyo.")
            else:
                print("  Ese asiento ya no está libre: otro cliente lo ganó.")
            return
        vence = datetime.now() + timedelta(seconds=config.TIEMPO_RESERVA)
        print(f"  Asiento {numero} reservado. Tenés {config.TIEMPO_RESERVA:.0f} s para pagar "
              f"(vence a las {vence:%H:%M:%S}).")
        if input("  ¿Pagás ahora? (s/n): ").strip().lower() != "s":
            print("  No pagaste: la reserva se libera sola cuando venza.")
            return
        ok, motivo = pedir(sistema.cola, Pedido("confirmar", YO, reserva=reserva))
    except PedidoAbandonado:
        print("  Nadie atendió tu pedido a tiempo: no se hizo nada. Probá de nuevo.")
        return
    print(f"  {'Listo: ' if ok else 'No se pudo: '}{motivo}.")


def lanzar_clientes(sistema):
    cantidad = leer_numero("¿Cuántos clientes simulados? (1 a 500): ", 1, 500)
    if cantidad is not None:
        sistema.lanzar_clientes(cantidad)
        print(f"  Salieron {cantidad} clientes a comprar. Mirá un viaje en vivo para ver cómo compiten.")


def mirar_en_vivo(sistema):
    id_viaje = elegir_viaje(sistema)
    if id_viaje is None:
        return
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            mostrar_mapa(sistema, id_viaje)
            comprando = sistema.clientes_comprando()
            pasarela = sistema.boleteria.pasarela
            # Estos números son solo para mirar: se leen sin lock y pueden estar
            # un instante desactualizados, pero nunca se usan para decidir nada.
            print(f"\n  Clientes simulados comprando: {comprando}. Pedidos en la cola: {sistema.cola.qsize()}.")
            print(f"  Cobros en la pasarela ahora: {pasarela.en_curso} de {pasarela.capacidad} "
                  f"(el semáforo no deja pasar más). Ctrl+C para volver al menú.")
            estados = contar_estados(sistema.boleteria.foto_asientos(id_viaje))
            if comprando == 0 and estados[EstadoAsiento.RESERVADO] == 0 and estados[EstadoAsiento.EN_PAGO] == 0:
                print("  Ya no hay movimiento en este viaje.")
                return
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def ver_ventas(sistema):
    try:
        ventas = ultimas_ventas(config.RUTA_BASE)
        total = contar_ventas(config.RUTA_BASE)
    except sqlite3.OperationalError:
        print("  Todavía no hay ventas guardadas.")
        return
    print(f"\n  {total} ventas guardadas en {config.RUTA_BASE.name}. Las últimas:")
    for id_viaje, numero, nombre, monto, cuando in ventas:
        print(f"    {cuando}  {id_viaje}  asiento {numero:>2}  {nombre:<14} {precio(monto)}")
    print("  (El escritor guarda en segundo plano: una venta recién hecha puede tardar un instante en aparecer.)")


# Las opciones son funciones guardadas como valores, igual que en la clase 6.
OPCIONES = {
    "1": ("Ver viajes y disponibilidad", ver_viajes),
    "2": ("Ver el mapa de asientos de un viaje", ver_mapa),
    "3": ("Comprar un pasaje", comprar),
    "4": ("Lanzar clientes simulados", lanzar_clientes),
    "5": ("Mirar un viaje en vivo", mirar_en_vivo),
    "6": ("Ver las últimas ventas guardadas en SQLite", ver_ventas),
}


def menu(sistema):
    while True:
        print(f"\n=== Venta de pasajes === hilos vivos: {threading.active_count()} | "
              f"clientes simulados comprando: {sistema.clientes_comprando()} | "
              f"pedidos en la cola: {sistema.cola.qsize()}")
        for clave, (texto, _) in OPCIONES.items():
            print(f"  {clave}. {texto}")
        print("  0. Salir (cierre ordenado)")
        opcion = input("> ").strip()
        if opcion == "0":
            return
        if opcion in OPCIONES:
            OPCIONES[opcion][1](sistema)
        else:
            print("  Opción inválida.")


def main():
    # En la consola compra una persona: necesita tiempo para contestar y para
    # ver competir a los clientes simulados. Por eso los tiempos son más largos,
    # y el cobro tarda medio segundo para que se vean los asientos pagando ($).
    config.TIEMPO_RESERVA = config.TIEMPO_RESERVA_CONSOLA
    config.DEMORA_CLIENTE_MIN, config.DEMORA_CLIENTE_MAX = 0.5, 4.0
    config.DEMORA_PAGO = 0.5

    with RegistroVentas(config.RUTA_BASE) as registro:
        sistema = Sistema(registro)
        sistema.iniciar()
        print(f"Sistema en marcha: {config.WORKERS} workers, hilo de vencimientos y escritor de SQLite. "
              f"Las reservas duran {config.TIEMPO_RESERVA:.0f} s.")
        try:
            menu(sistema)
        except KeyboardInterrupt:
            print("\n  Cortaste con Ctrl+C.")
        except EOFError:
            print("\n  Se terminó la entrada.")
        finally:
            print("\nCierre ordenado:")
            sistema.detener()
            print("  4. Pongo el centinela en la cola del escritor de SQLite y lo espero.")
    print(f"Listo. Hilos vivos: {threading.active_count()} (solo el principal).")


if __name__ == "__main__":
    main()
