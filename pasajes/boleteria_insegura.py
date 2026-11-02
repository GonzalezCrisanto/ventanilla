"""Versión 1 (insegura, a propósito): la boletería SIN sincronización.

Hace lo mismo que la versión segura pero sin Lock ni semáforo. Reservar es
un "chequear y después actuar":

    if asiento.estado == LIBRE:      # 1) leo
        ...                          #    acá otro hilo puede leer lo mismo
        asiento.estado = RESERVADO   # 2) escribo

Con dos o más workers, dos hilos pueden pasar el chequeo antes de que
alguno escriba. Los dos clientes reciben una reserva válida para el mismo
asiento, los dos pagan, y se emiten dos boletos: el asiento queda vendido
dos veces. La pausa VENTANA_CARRERA de config ensancha ese hueco para que
la falla se reproduzca siempre en la demo y en los tests.

Con un solo worker no hay carrera: la cola atiende los pedidos de a uno.
"""
import time

from pasajes import config
from pasajes.modelo import EstadoAsiento, EstadoReserva, Reserva


class BoleteriaInsegura:
    nombre = "insegura (sin Lock ni semáforo)"

    def __init__(self, viajes, pasarela):
        self.viajes = {viaje.id: viaje for viaje in viajes}
        self.pasarela = pasarela

    def asientos_libres(self, id_viaje):
        """Consulta: los números de asiento libres de un viaje."""
        return self.viajes[id_viaje].numeros_en_estado(EstadoAsiento.LIBRE)

    def foto_asientos(self, id_viaje):
        """Consulta: {número: (estado, cliente o None)} de todos los asientos."""
        foto = {}
        for numero, asiento in self.viajes[id_viaje].asientos.items():
            reserva = asiento.reserva  # sin lock, se lee una sola vez (puede cambiar entre lecturas)
            foto[numero] = (asiento.estado, reserva.cliente if reserva else None)
        return foto

    def reservar(self, cliente, id_viaje, numero):
        """Retiene el asiento por config.TIEMPO_RESERVA segundos.

        Devuelve la Reserva, o None si el asiento ya no estaba libre."""
        asiento = self.viajes[id_viaje].asientos[numero]

        # 1) Leo: ¿está libre?
        if asiento.estado is not EstadoAsiento.LIBRE:
            return None

        # Entre leer y escribir hay trabajo (validar al pasajero, calcular la
        # tarifa). Mientras tanto, otro worker puede leer este mismo asiento y
        # verlo libre también.
        time.sleep(config.VENTANA_CARRERA)

        # 2) Escribo: lo retengo a mi nombre, pisando lo que haya escrito otro.
        reserva = Reserva(cliente, id_viaje, numero, vence=time.monotonic() + config.TIEMPO_RESERVA)
        asiento.estado = EstadoAsiento.RESERVADO
        asiento.reserva = reserva
        return reserva

    def confirmar(self, reserva):
        """Cobra la reserva y emite el boleto. Devuelve (ok, motivo)."""
        viaje = self.viajes[reserva.id_viaje]
        asiento = viaje.asientos[reserva.numero_asiento]

        if reserva.estado is not EstadoReserva.ACTIVA:
            return False, f"la reserva está {reserva.estado.value}"
        if time.monotonic() >= reserva.vence:
            self._vencer(reserva, asiento)
            return False, "la reserva venció"

        # EN_PAGO: el hilo de vencimientos no toca asientos con un cobro en curso.
        asiento.estado = EstadoAsiento.EN_PAGO

        # Sin semáforo: si muchos workers cobran a la vez, la pasarela se satura.
        cobrado = self.pasarela.cobrar(viaje.precio)

        if not cobrado:
            reserva.estado = EstadoReserva.RECHAZADA
            asiento.estado = EstadoAsiento.LIBRE
            asiento.reserva = None
            return False, "la pasarela rechazó el cobro"

        reserva.estado = EstadoReserva.CONFIRMADA
        asiento.estado = EstadoAsiento.VENDIDO
        asiento.reserva = reserva
        viaje.boletos.append(reserva)
        return True, "compra confirmada"

    def liberar_vencidas(self):
        """Libera los asientos cuya reserva venció. Lo llama el hilo de vencimientos."""
        ahora = time.monotonic()
        for viaje in self.viajes.values():
            for asiento in viaje.asientos.values():
                # Sin lock, el asiento puede cambiar entre una lectura y la
                # siguiente: por eso se lee la reserva una vez y se controla.
                reserva = asiento.reserva
                if asiento.estado is EstadoAsiento.RESERVADO and reserva is not None and ahora >= reserva.vence:
                    self._vencer(reserva, asiento)

    def _vencer(self, reserva, asiento):
        reserva.estado = EstadoReserva.VENCIDA
        if asiento.reserva is reserva:  # solo si el asiento sigue siendo de esta reserva
            asiento.estado = EstadoAsiento.LIBRE
            asiento.reserva = None
