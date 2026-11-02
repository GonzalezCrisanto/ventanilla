"""El sistema funcionando: la boletería segura con todos sus hilos prendidos.

Lo usan la consola y la web. Tiene los mismos hilos que la simulación
(simulacion.py), pero en vez de una corrida cerrada queda prendido mientras
dure el programa, y se le pueden sumar clientes simulados en cualquier momento.

En la consola, los clientes los lanza solo el hilo principal. En la web los
lanza cualquier hilo HTTP, y dos pueden hacerlo a la vez: por eso la lista de
clientes y el contador de ids están protegidos con un lock.
"""
import queue
import threading

from pasajes import config
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viajes_de_ejemplo
from pasajes.modelo import Cliente
from pasajes.pasarela import Pasarela
from pasajes.simulacion import ResultadoCliente, hilo_cliente, hilo_vencimientos, hilo_worker


class Sistema:
    def __init__(self, registro):
        self.boleteria = BoleteriaSegura(viajes_de_ejemplo(), Pasarela(), registro)
        self.cola = queue.Queue(maxsize=config.MAX_PEDIDOS_EN_COLA)
        self.fin = threading.Event()
        self.rutas = sorted({(v.origen, v.destino) for v in self.boleteria.viajes.values()})
        self.workers = [
            threading.Thread(target=hilo_worker, args=(self.boleteria, self.cola), name=f"worker-{i}")
            for i in range(1, config.WORKERS + 1)
        ]
        self.vencimientos = threading.Thread(target=hilo_vencimientos, args=(self.boleteria, self.fin),
                                             name="vencimientos")
        self._lock = threading.Lock()  # protege clientes y proximo_id
        self.clientes = []  # (hilo, resultado) de cada cliente simulado
        self.proximo_id = 1

    def iniciar(self):
        for hilo in self.workers + [self.vencimientos]:
            hilo.start()

    def lanzar_clientes(self, cantidad):
        apertura = threading.Event()
        nuevos = []
        with self._lock:
            for _ in range(cantidad):
                resultado = ResultadoCliente(Cliente(self.proximo_id, f"Pasajero {self.proximo_id:03d}"))
                self.proximo_id += 1
                hilo = threading.Thread(target=hilo_cliente,
                                        args=(self.boleteria, self.cola, self.rutas, apertura, resultado),
                                        name=f"cliente-{resultado.cliente.id}")
                self.clientes.append((hilo, resultado))
                nuevos.append(hilo)
        # Se arrancan afuera del lock: nunca se espera algo con un lock tomado.
        for hilo in nuevos:
            hilo.start()
        apertura.set()  # salen todos juntos

    def clientes_comprando(self):
        with self._lock:
            hilos = [hilo for hilo, _ in self.clientes]
        return sum(1 for hilo in hilos if hilo.is_alive())

    def detener(self, paso=1):
        """El mismo cierre ordenado que la simulación, contado paso a paso.

        Se llama cuando ya nadie lanza clientes nuevos (en la web, con el
        servidor HTTP ya cerrado)."""
        print(f"  {paso}. Espero a los clientes simulados que siguen comprando ({self.clientes_comprando()}).")
        for hilo, _ in self.clientes:
            hilo.join()
        print(f"  {paso + 1}. Pongo un centinela por worker en la cola ({len(self.workers)}) y los espero.")
        for _ in self.workers:
            self.cola.put(None)
        for hilo in self.workers:
            hilo.join()
        print(f"  {paso + 2}. Apago el hilo de vencimientos con fin.set().")
        self.fin.set()
        self.vencimientos.join()
