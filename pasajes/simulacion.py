"""Arma y corre la simulación: productores, consumidores y cierre ordenado.

    hilos cliente ──Pedido──▶ cola_pedidos (queue.Queue) ──▶ hilos worker ──▶ boletería
          ▲                                                       │
          └──────── respuesta (una Queue de 1 lugar por pedido) ──┘

    hilo de vencimientos: cada tanto libera las reservas vencidas.

Las consultas (qué asientos están libres) van directo a la boletería porque
no modifican nada. Las compras (reservar y confirmar) pasan por la cola.

Qué comparten los hilos y qué no:
    - Comparten la cola de pedidos (ya es segura entre hilos) y la boletería
      (la insegura no se protege; la segura usa un Lock por viaje).
    - Cada cliente tiene su propio generador de azar y su propio casillero
      de resultados: entre clientes no se comparte nada.

Cuando un cliente se cansa de esperar (TIMEOUT_RESPUESTA), su pedido NO se
ejecuta: el cliente y el worker se lo disputan con el lock del pedido (ver
Pedido.tomar y Pedido.abandonar). Si un worker ya lo había tomado, el cliente
espera el resultado. Antes, el pedido quedaba en la cola y se ejecutaba igual,
y el cliente podía quedarse con una compra que creía fallida.

Cierre ordenado:
    1. join() de todos los clientes: ya no entran pedidos nuevos.
    2. Un centinela (None) por worker en la cola. Como la cola es FIFO, cada
       worker procesa todo lo pendiente antes de recibir su centinela y salir.
    3. Se espera a que venzan las reservas abandonadas.
    4. evento_fin.set() y join() del hilo de vencimientos.
Ningún hilo es daemon: si alguno se colgara, el programa no terminaría y lo
veríamos (los tests usan join con timeout para detectarlo).

Funciona igual con la boletería insegura o con la segura.
"""
import queue
import random
import threading
import time
from dataclasses import dataclass, field

from pasajes import config
from pasajes.modelo import Cliente, Reserva


@dataclass
class Pedido:
    """Lo que un cliente le pide a la boletería a través de la cola."""
    tipo: str  # "reservar" o "confirmar"
    cliente: Cliente
    id_viaje: str | None = None
    numero_asiento: int | None = None
    reserva: Reserva | None = None  # la que se quiere confirmar
    # Por acá vuelve la respuesta: una cola de un solo lugar, solo para este pedido.
    respuesta: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    # Quién se queda con el pedido: el worker que lo toma o el cliente que se
    # cansa de esperar. Lo deciden con este lock, que se toma solo.
    estado: str = "pendiente"  # "pendiente", "tomado" o "abandonado"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def tomar(self):
        """Lo llama el worker. True si puede procesarlo; False si el cliente ya se fue."""
        with self._lock:
            if self.estado == "abandonado":
                return False
            self.estado = "tomado"
            return True

    def abandonar(self):
        """Lo llama el cliente que se cansó de esperar. True si llegó a tiempo:
        entonces el pedido no se va a ejecutar nunca."""
        with self._lock:
            if self.estado == "tomado":
                return False
            self.estado = "abandonado"
            return True


@dataclass
class ResultadoCliente:
    """Cómo le fue a un cliente. Solo lo escribe el hilo de ese cliente."""
    cliente: Cliente
    estado: str = "sin asiento"  # "compró", "abandonó", "sin asiento", "sin atender" o "error"
    reserva: Reserva | None = None
    intentos: int = 0
    detalle: str = ""


@dataclass
class Corrida:
    """Lo que queda después de una simulación, para verificarla."""
    boleteria: object
    resultados: list[ResultadoCliente]
    workers: int
    duracion: float  # desde que abre la venta hasta que termina el último worker


def crear_clientes(cantidad):
    return [Cliente(i, f"Pasajero {i:03d}") for i in range(1, cantidad + 1)]


# --- Lo que hace cada tipo de hilo ---------------------------------------

class PedidoAbandonado(Exception):
    """Ningún worker tomó el pedido a tiempo y el cliente lo abandonó: es seguro que no se ejecutó."""


def pedir(cola, pedido):
    """Encola el pedido y espera la respuesta del worker que lo atienda.

    El cliente espera como mucho TIMEOUT_RESPUESTA a que un worker TOME su
    pedido. Si nadie lo tomó, lo abandona y levanta PedidoAbandonado: el pedido
    no se va a ejecutar. Si un worker ya lo había tomado, sigue esperando el
    resultado, porque la operación está en marcha y termina en poco tiempo."""
    limite = time.monotonic() + config.TIMEOUT_RESPUESTA
    try:
        cola.put(pedido, timeout=config.TIMEOUT_RESPUESTA)  # con la cola llena, espera acá (freno)
    except queue.Full:
        raise PedidoAbandonado("la cola estuvo llena todo el tiempo") from None
    try:
        respuesta = pedido.respuesta.get(timeout=max(0, limite - time.monotonic()))
    except queue.Empty:
        if pedido.abandonar():
            raise PedidoAbandonado("ningún worker lo tomó a tiempo") from None
        # Un worker ya lo estaba procesando: esperamos el final. El worker
        # siempre responde (aunque falle), así que esta espera termina.
        respuesta = pedido.respuesta.get()
    if isinstance(respuesta, Exception):
        raise respuesta  # el worker falló con este pedido: el error le llega al cliente
    return respuesta


def buscar_asiento(boleteria, rng, origen, destino):
    """Busca el primer viaje de la ruta que tenga lugar y elige un asiento libre al azar.

    Devuelve (id_viaje, numero) o None si la ruta está agotada. Lo que ve es
    una foto: para cuando llegue el pedido, otro cliente puede habérselo ganado."""
    viajes = sorted(
        (v for v in boleteria.viajes.values() if (v.origen, v.destino) == (origen, destino)),
        key=lambda v: v.salida,
    )
    for viaje in viajes:
        libres = boleteria.asientos_libres(viaje.id)
        if libres:
            return viaje.id, rng.choice(libres)
    return None


def hilo_cliente(boleteria, cola, rutas, apertura, resultado, cancelar=None):
    """Un cliente: busca un viaje, reserva un asiento, paga y confirma.

    `cancelar` es un Event opcional: si alguien lo activa (el botón Reiniciar de
    la web), el cliente deja de comprar enseguida, aunque esté en la pausa en la
    que "carga la tarjeta". Sin él, el cliente compra hasta terminar."""
    cliente = resultado.cliente
    rng = random.Random(config.SEMILLA + cliente.id)  # azar propio: no se comparte entre hilos
    origen, destino = rng.choice(rutas)

    apertura.wait()  # todos esperan a que abra la venta y salen juntos

    for intento in range(1, config.INTENTOS_POR_CLIENTE + 1):
        if cancelar is not None and cancelar.is_set():
            resultado.estado = "sin atender"  # su pedido no se ejecutó
            resultado.detalle = "la simulación se reinició"
            return
        resultado.intentos = intento
        encontrado = buscar_asiento(boleteria, rng, origen, destino)
        if encontrado is None:
            resultado.detalle = "no quedan asientos"
            return
        id_viaje, numero = encontrado
        reserva = None

        try:
            reserva = pedir(cola, Pedido("reservar", cliente, id_viaje, numero))
            if reserva is None:
                resultado.detalle = "otro cliente ganó el asiento"
                continue

            if rng.random() < config.PROB_ABANDONO:
                resultado.estado = "abandonó"  # nunca paga: la reserva tiene que vencer sola
                resultado.reserva = reserva
                return

            pausa = rng.uniform(config.DEMORA_CLIENTE_MIN, config.DEMORA_CLIENTE_MAX)  # carga la tarjeta
            if cancelar is None:
                time.sleep(pausa)
            elif cancelar.wait(pausa):  # wait() se despierta apenas alguien activa el evento
                resultado.estado = "sin atender"
                resultado.detalle = "la simulación se reinició"
                resultado.reserva = reserva  # sin pagar: vence sola
                return
            ok, motivo = pedir(cola, Pedido("confirmar", cliente, reserva=reserva))
        except PedidoAbandonado as abandonado:
            resultado.estado = "sin atender"  # se cansó de esperar: su pedido no se ejecutó
            resultado.detalle = str(abandonado)
            resultado.reserva = reserva  # si ya tenía una reserva sin pagar, vence sola
            return
        except Exception as error:
            resultado.estado = "error"
            resultado.detalle = repr(error)
            return

        if ok:
            resultado.estado = "compró"
            resultado.reserva = reserva
            return
        resultado.detalle = motivo  # rechazada o vencida: prueba con otro asiento


def hilo_worker(boleteria, cola, observador=None):
    """Un worker: saca pedidos de la cola y los procesa hasta recibir el centinela.

    `observador(pedido, respuesta)` es opcional: se llama con cada resultado
    antes de contestarle al cliente. Lo usa la web para sus estadísticas. Si
    fallara, no debe frenar las ventas: el worker sigue igual."""
    while True:
        pedido = cola.get()  # bloquea sin gastar CPU mientras la cola está vacía
        if pedido is None:   # centinela: no hay más trabajo
            return
        if not pedido.tomar():  # el cliente se cansó de esperar antes: no se ejecuta
            continue
        try:
            if pedido.tipo == "reservar":
                respuesta = boleteria.reservar(pedido.cliente, pedido.id_viaje, pedido.numero_asiento)
            else:
                respuesta = boleteria.confirmar(pedido.reserva)
        except Exception as error:
            # Igual respondemos: si no, el cliente esperaría hasta el timeout.
            respuesta = error
        if observador is not None:
            try:
                observador(pedido, respuesta)
            except Exception:
                pass  # las estadísticas nunca pueden dejar a un cliente sin respuesta
        pedido.respuesta.put(respuesta)


def hilo_vencimientos(boleteria, fin):
    """Libera reservas vencidas cada config.INTERVALO_VENCIMIENTOS segundos."""
    while True:
        # wait() duerme hasta el próximo control, pero se despierta apenas
        # alguien hace fin.set(); en ese caso devuelve True.
        terminar = fin.wait(config.INTERVALO_VENCIMIENTOS)
        boleteria.liberar_vencidas()
        if terminar:  # ya hicimos la última pasada
            return


# --- La simulación completa -------------------------------------------------

def correr_simulacion(boleteria, clientes, workers):
    cola = queue.Queue(maxsize=config.MAX_PEDIDOS_EN_COLA)
    apertura = threading.Event()  # se abre la venta
    fin = threading.Event()       # se apaga el hilo de vencimientos
    rutas = sorted({(v.origen, v.destino) for v in boleteria.viajes.values()})
    resultados = [ResultadoCliente(cliente) for cliente in clientes]

    hilos_worker = [
        threading.Thread(target=hilo_worker, args=(boleteria, cola), name=f"worker-{i}")
        for i in range(1, workers + 1)
    ]
    vencimientos = threading.Thread(target=hilo_vencimientos, args=(boleteria, fin), name="vencimientos")
    hilos_cliente = [
        threading.Thread(target=hilo_cliente, args=(boleteria, cola, rutas, apertura, resultado),
                         name=f"cliente-{resultado.cliente.id}")
        for resultado in resultados
    ]
    for hilo in hilos_worker + [vencimientos] + hilos_cliente:
        hilo.start()

    inicio = time.perf_counter()
    apertura.set()  # abre la venta: todos los clientes salen a la vez

    # 1. Esperamos a todos los clientes: después de esto no entran pedidos nuevos.
    for hilo in hilos_cliente:
        hilo.join()

    # 2. Un centinela por worker. La cola es FIFO: cada uno termina lo
    #    pendiente antes de encontrar su centinela.
    for _ in hilos_worker:
        cola.put(None)
    for hilo in hilos_worker:
        hilo.join()
    duracion = time.perf_counter() - inicio

    # 3. Las reservas abandonadas vencen solas. Esperamos al último vencimiento
    #    para que el mapa termine limpio: solo asientos libres o vendidos.
    #    Se espera mirando el mismo reloj que usan las reservas (monotonic):
    #    en Windows con Python 3.12 avanza de a saltos de 15,6 ms, y un solo
    #    sleep() puede terminar cuando ese reloj todavía no llegó al vencimiento.
    #    Así quedaban reservas sin liberar; lo detectó
    #    test_las_reservas_abandonadas_vencen_y_se_liberan.
    #    También las de los clientes que se cansaron de esperar el pago.
    abandonadas = [r.reserva for r in resultados
                   if r.estado in ("abandonó", "sin atender") and r.reserva is not None]
    if abandonadas:
        ultimo_vencimiento = max(reserva.vence for reserva in abandonadas)
        while time.monotonic() < ultimo_vencimiento:
            time.sleep(max(0, ultimo_vencimiento - time.monotonic()) + 0.001)

    # 4. Apagamos el hilo de vencimientos: hace una última pasada y sale.
    fin.set()
    vencimientos.join()

    return Corrida(boleteria, resultados, workers, duracion)
