"""Versión 2 (corregida): la misma boletería con sincronización.

Hace exactamente el mismo trabajo que la versión insegura. Lo que cambia:

- Un threading.Lock por viaje. Cada operación sobre los asientos de un
  viaje se hace con el lock de ese viaje tomado, así "leo y después
  escribo" pasa a ser una sola operación que nadie puede interrumpir.
  Viajes distintos no se frenan entre sí.
- Un threading.BoundedSemaphore limita cuántos cobros mandamos a la vez a
  la pasarela de pago, para no saturarla.

Reglas para no trabarse (sin deadlocks):
- Nunca se toma más de un lock a la vez: no puede haber espera circular.
- Nunca se espera a la pasarela, al semáforo ni a una cola con un lock
  tomado: el cobro se hace afuera del lock.
"""
import threading
import time

from pasajes import config
from pasajes.modelo import EstadoAsiento, EstadoReserva, Reserva


class BoleteriaSegura:
    nombre = "segura (Lock por viaje + semáforo)"

    def __init__(self, viajes, pasarela, registro=None):
        self.viajes = {viaje.id: viaje for viaje in viajes}
        self.pasarela = pasarela
        self.registro = registro  # RegistroVentas (SQLite) o None para no guardar
        # Un lock por viaje: protege los asientos y los boletos de ese viaje.
        self.locks = {viaje.id: threading.Lock() for viaje in viajes}
        # Cuántos cobros pueden estar en la pasarela al mismo tiempo.
        self.semaforo_pagos = threading.BoundedSemaphore(config.PAGOS_SIMULTANEOS)

    def asientos_libres(self, id_viaje):
        """Consulta: los números de asiento libres de un viaje.

        Con el lock, la foto es consistente: no se mezclan asientos leídos
        antes y después de una reserva a medio hacer."""
        with self.locks[id_viaje]:
            return self.viajes[id_viaje].numeros_en_estado(EstadoAsiento.LIBRE)

    def foto_asientos(self, id_viaje):
        """Consulta: {número: (estado, cliente o None)} de todos los asientos.

        La usa la consola para dibujar el mapa del micro. Con el lock, el mapa
        no mezcla asientos de antes y de después de una compra."""
        with self.locks[id_viaje]:
            return {
                numero: (asiento.estado, asiento.reserva.cliente if asiento.reserva else None)
                for numero, asiento in self.viajes[id_viaje].asientos.items()
            }

    def reservar(self, cliente, id_viaje, numero):
        """Retiene el asiento por config.TIEMPO_RESERVA segundos.

        Devuelve la Reserva, o None si el asiento ya no estaba libre."""
        asiento = self.viajes[id_viaje].asientos[numero]

        with self.locks[id_viaje]:
            # 1) Leo: ¿está libre?
            if asiento.estado is not EstadoAsiento.LIBRE:
                return None

            # El mismo trabajo que en la versión insegura, pero ahora ningún
            # otro worker puede leer este viaje hasta que terminemos.
            time.sleep(config.VENTANA_CARRERA)

            # 2) Escribo: nadie pudo escribir entre el paso 1 y este.
            reserva = Reserva(cliente, id_viaje, numero, vence=time.monotonic() + config.TIEMPO_RESERVA)
            asiento.estado = EstadoAsiento.RESERVADO
            asiento.reserva = reserva
            return reserva

    def confirmar(self, reserva):
        """Cobra la reserva y emite el boleto. Devuelve (ok, motivo).

        Tres pasos, para no tener el lock tomado mientras se cobra:
            1. con lock:    verificar la reserva y marcar el asiento EN_PAGO
            2. sin lock:    cobrar (lento), de a PAGOS_SIMULTANEOS a la vez
            3. con lock:    cerrar la venta o liberar el asiento
        """
        if self.registro is not None:
            self.registro.comprobar_error()
        viaje = self.viajes[reserva.id_viaje]
        asiento = viaje.asientos[reserva.numero_asiento]
        lock = self.locks[reserva.id_viaje]

        # Paso 1
        with lock:
            if reserva.estado is not EstadoReserva.ACTIVA:
                return False, f"la reserva está {reserva.estado.value}"
            # Claim payment only once, before checking expiry or releasing the lock.
            if asiento.reserva is not reserva or asiento.estado is not EstadoAsiento.RESERVADO:
                return False, "la reserva no está disponible para pagar"
            if time.monotonic() >= reserva.vence:
                self._vencer(reserva, asiento)
                return False, "la reserva venció"
            # EN_PAGO: desde acá el vencimiento no puede liberar el asiento,
            # aunque soltemos el lock durante el cobro.
            asiento.estado = EstadoAsiento.EN_PAGO

        # Paso 2: el semáforo deja pasar hasta PAGOS_SIMULTANEOS cobros; el resto espera su turno.
        with self.semaforo_pagos:
            cobrado = self.pasarela.cobrar(viaje.precio)

        # Paso 3
        with lock:
            if not cobrado:
                reserva.estado = EstadoReserva.RECHAZADA
                asiento.estado = EstadoAsiento.LIBRE
                asiento.reserva = None
                return False, "la pasarela rechazó el cobro"

            reserva.estado = EstadoReserva.CONFIRMADA
            asiento.estado = EstadoAsiento.VENDIDO
            asiento.reserva = reserva
            viaje.boletos.append(reserva)

        # Guardar en la base también es lento: se hace afuera del lock.
        if self.registro is not None:
            self.registro.registrar(viaje, reserva)
        return True, "compra confirmada"

    def liberar_vencidas(self):
        """Libera los asientos cuya reserva venció. Lo llama el hilo de vencimientos.

        Toma el lock de un viaje por vez, nunca dos juntos."""
        ahora = time.monotonic()
        for id_viaje, viaje in self.viajes.items():
            with self.locks[id_viaje]:
                for asiento in viaje.asientos.values():
                    if asiento.estado is EstadoAsiento.RESERVADO and ahora >= asiento.reserva.vence:
                        self._vencer(asiento.reserva, asiento)

    def _vencer(self, reserva, asiento):
        """Se llama siempre con el lock del viaje ya tomado."""
        reserva.estado = EstadoReserva.VENCIDA
        if asiento.reserva is reserva:
            asiento.estado = EstadoAsiento.LIBRE
            asiento.reserva = None
