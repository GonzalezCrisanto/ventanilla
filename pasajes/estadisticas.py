"""Lo que pasó en una corrida, anotado para el panel de admin.

Las anota el worker después de cada pedido (hilo_worker(observador=...)), no
la boletería. Así funcionan igual con la segura y con la insegura: la insegura
no tiene locks, y un contador metido adentro tendría las mismas carreras que
queremos mostrar.

Tiene su propio Lock, que se toma solo para anotar o para copiar los datos:
nunca con otro lock tomado y nunca esperando algo (ni cola, ni pasarela).
El panel trabaja con la copia, ya sin lock.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass

VENTANA_ACTIVIDAD = 60  # segundos que muestra el gráfico de actividad


@dataclass(frozen=True)
class Foto:
    """Copia de las estadísticas en un instante: se puede leer sin lock."""
    inicio: float
    reservas: list        # todas las Reserva que se concedieron
    eventos: list         # (instante, "reserva" | "compra")
    compras: list         # últimas compras: (instante, id_viaje, asiento, cliente)
    reservas_perdidas: int    # pedidos de reserva a un asiento que ya no estaba libre
    pagos_tarde: int          # pagos que llegaron con la reserva vencida
    pagos_rechazados: int     # cobros que la pasarela saturada rechazó
    errores: int              # pedidos que terminaron en error


class Estadisticas:
    def __init__(self):
        self.inicio = time.monotonic()
        self._lock = threading.Lock()
        self._reservas = []
        self._eventos = []
        self._compras = deque(maxlen=5)
        self._reservas_perdidas = 0
        self._pagos_tarde = 0
        self._pagos_rechazados = 0
        self._errores = 0

    def registrar(self, pedido, respuesta):
        """Lo llama el worker con cada pedido atendido y lo que se le contestó."""
        ahora = time.monotonic()
        with self._lock:
            if isinstance(respuesta, Exception):
                self._errores += 1
            elif pedido.tipo == "reservar":
                if respuesta is None:
                    self._reservas_perdidas += 1
                else:
                    self._reservas.append(respuesta)
                    self._eventos.append((ahora, "reserva"))
            else:
                ok, motivo = respuesta
                if ok:
                    reserva = pedido.reserva
                    self._eventos.append((ahora, "compra"))
                    self._compras.append((ahora, reserva.id_viaje, reserva.numero_asiento, reserva.cliente.nombre))
                elif "pasarela" in motivo:
                    self._pagos_rechazados += 1
                else:
                    self._pagos_tarde += 1

    def foto(self):
        with self._lock:
            return Foto(
                inicio=self.inicio,
                reservas=list(self._reservas),
                eventos=list(self._eventos),
                compras=list(self._compras),
                reservas_perdidas=self._reservas_perdidas,
                pagos_tarde=self._pagos_tarde,
                pagos_rechazados=self._pagos_rechazados,
                errores=self._errores,
            )
