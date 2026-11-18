"""El sistema funcionando: la boletería con todos sus hilos prendidos.

Lo usan la consola y la web. Tiene los mismos hilos que la simulación
(simulacion.py), pero en vez de una corrida cerrada queda prendido mientras
dure el programa, y se le pueden sumar clientes simulados en cualquier momento.

La boletería puede ser la segura o la insegura (`modo`). La web las cambia con
un botón: cambiar de modo, o reiniciar, es cerrar este sistema y abrir otro con
todos los asientos libres.

En la consola, los clientes los lanza solo el hilo principal. En la web los
lanza cualquier hilo HTTP, y dos pueden hacerlo a la vez: por eso la lista de
clientes y el contador de ids están protegidos con un lock.
"""
import queue
import threading
import time

from pasajes import config
from pasajes.boleteria_insegura import BoleteriaInsegura
from pasajes.boleteria_segura import BoleteriaSegura
from pasajes.catalogo import viajes_de_ejemplo
from pasajes.estadisticas import Estadisticas
from pasajes.modelo import Cliente
from pasajes.pasarela import Pasarela
from pasajes.persistencia import RegistroVentas
from pasajes.simulacion import (PedidoAbandonado, ResultadoCliente, hilo_cliente, hilo_vencimientos,
                                hilo_worker)

MODOS = ("segura", "insegura")


class Sistema:
    def __init__(self, registro, modo="segura"):
        if modo not in MODOS:
            raise ValueError(f"modo desconocido: {modo!r} (tiene que ser uno de {MODOS})")
        self.modo = modo
        viajes, pasarela = viajes_de_ejemplo(), Pasarela()
        if modo == "segura":
            self.boleteria = BoleteriaSegura(viajes, pasarela, registro)
        else:
            self.boleteria = BoleteriaInsegura(viajes, pasarela)  # sin sincronización y sin registro
        self.estadisticas = Estadisticas()
        self.cola = queue.Queue(maxsize=config.MAX_PEDIDOS_EN_COLA)
        self.fin = threading.Event()
        self.cancelado = threading.Event()  # los clientes simulados dejan de comprar (Reiniciar)
        self.rutas = sorted({(v.origen, v.destino) for v in self.boleteria.viajes.values()})
        self.workers = [
            threading.Thread(target=hilo_worker, args=(self.boleteria, self.cola, self.estadisticas.registrar),
                             name=f"worker-{i}")
            for i in range(1, config.WORKERS + 1)
        ]
        self.vencimientos = threading.Thread(target=hilo_vencimientos, args=(self.boleteria, self.fin),
                                             name="vencimientos")
        self._lock = threading.Lock()  # protege clientes, proximo_id y cerrado
        self.clientes = []  # (hilo, resultado) de cada cliente simulado
        self.proximo_id = 1
        self.cerrado = False
        self._registro_propio = None  # el escritor de SQLite que abrió abrir(), si lo hay

    @classmethod
    def abrir(cls, modo, ruta_base):
        """Un sistema listo para usar, con su propio escritor de SQLite si es el seguro.

        Es lo que usa la web para cada reinicio. La boletería insegura no guarda
        ventas: no tiene escritor. Se cierra con cerrar()."""
        registro = RegistroVentas(ruta_base).__enter__() if modo == "segura" else None
        try:
            sistema = cls(registro, modo)
        except BaseException:
            if registro is not None:
                registro.__exit__(None, None, None)
            raise
        sistema._registro_propio = registro
        sistema.iniciar()
        return sistema

    def iniciar(self):
        for hilo in self.workers + [self.vencimientos]:
            hilo.start()

    def lanzar_clientes(self, cantidad):
        """Suelta clientes simulados a comprar. Devuelve cuántos salieron (0 si el sistema ya se cerró)."""
        apertura = threading.Event()
        nuevos = []
        with self._lock:
            if self.cerrado:
                return 0
            for _ in range(cantidad):
                resultado = ResultadoCliente(Cliente(self.proximo_id, f"Pasajero {self.proximo_id:03d}"))
                self.proximo_id += 1
                hilo = threading.Thread(target=hilo_cliente,
                                        args=(self.boleteria, self.cola, self.rutas, apertura, resultado,
                                              self.cancelado),
                                        name=f"cliente-{resultado.cliente.id}")
                self.clientes.append((hilo, resultado))
                nuevos.append(hilo)
        # Se arrancan afuera del lock: nunca se espera algo con un lock tomado.
        for hilo in nuevos:
            hilo.start()
        apertura.set()  # salen todos juntos
        return len(nuevos)

    def clientes_lanzados(self):
        with self._lock:
            return len(self.clientes)

    def clientes_comprando(self):
        with self._lock:
            hilos = [hilo for hilo, _ in self.clientes]
        return sum(1 for hilo in hilos if hilo.is_alive())

    def segundos_encendido(self):
        return time.monotonic() - self.estadisticas.inicio

    def detener(self, paso=1, apurar=False):
        """El cierre ordenado de la simulación, contado paso a paso.

        Se llama cuando ya nadie lanza clientes nuevos (en la web, con el
        servidor HTTP ya cerrado). Con apurar=True (el botón Reiniciar) no se
        espera a que los clientes terminen de comprar: dejan de comprar ya y los
        pedidos que esperan en la cola se cancelan en vez de atenderse. Los que
        un worker ya estaba atendiendo sí terminan, y con eso no queda nada colgado."""
        with self._lock:
            self.cerrado = True  # desde acá lanzar_clientes() no lanza nada
            hilos_cliente = [hilo for hilo, _ in self.clientes]
        if apurar:
            self.cancelado.set()
        else:
            print(f"  {paso}. Espero a los clientes simulados que siguen comprando ({self.clientes_comprando()}).")
        for hilo in hilos_cliente:
            while hilo.is_alive():
                if apurar:
                    self._cancelar_pedidos_en_espera()  # si no, el cliente esperaría su turno en la cola
                hilo.join(0.05 if apurar else None)
        if apurar:
            self._cancelar_pedidos_en_espera()  # lo último que quedó, de los hilos HTTP
        else:
            print(f"  {paso + 1}. Pongo un centinela por worker en la cola ({len(self.workers)}) y los espero.")
        for _ in self.workers:
            self.cola.put(None)
        for hilo in self.workers:
            hilo.join()
        if not apurar:
            print(f"  {paso + 2}. Apago el hilo de vencimientos con fin.set().")
        self.fin.set()
        self.vencimientos.join()

    def cerrar(self, paso=1, apurar=False):
        """detener() y, si lo abrió abrir(), cierra también el escritor de SQLite."""
        self.detener(paso, apurar)
        if self._registro_propio is not None:
            self._registro_propio.__exit__(None, None, None)

    def _cancelar_pedidos_en_espera(self):
        """Saca los pedidos que nadie atendió todavía y les avisa que no se van a ejecutar.

        pedido.tomar() decide, con el lock del pedido, quién se lo queda: si un
        worker ya lo tomó, este código no lo toca. Los pedidos cancelados le
        llegan al cliente como PedidoAbandonado: es seguro que no se hicieron."""
        while True:
            try:
                pedido = self.cola.get_nowait()
            except queue.Empty:
                return
            if pedido is not None and pedido.tomar():
                pedido.respuesta.put(PedidoAbandonado("la simulación se reinició"))
