"""Guarda las ventas en SQLite con un único hilo escritor.

    workers ──venta (tupla)──▶ cola_ventas (queue.Queue) ──▶ hilo escritor ──▶ ventas.db

Por qué un solo hilo escribe:
    - SQLite acepta un escritor a la vez: si varios workers escribieran,
      se frenarían entre ellos esperando el lock de la base.
    - Por defecto, una conexión de sqlite3 no se puede usar desde otro hilo.
      La conexión se abre adentro del hilo escritor y solo él la usa.
    - El worker no espera al disco: deja la venta en la cola y sigue.

A la cola se manda una tupla con datos simples, no la Reserva: la tupla es
inmutable (clase 7), así el escritor nunca lee un objeto que otro hilo
podría estar cambiando.

Cierre: un centinela (None) al final de la cola. Como es FIFO, el escritor
guarda todo lo pendiente antes de verlo. Se usa con with:

    with RegistroVentas(ruta) as registro:
        ...                     # los workers llaman a registro.registrar(...)
    # acá el escritor ya guardó todo y terminó

Cada vez que arranca, la tabla empieza vacía: guarda una sola corrida.
"""
import queue
import sqlite3
import threading
from datetime import datetime

CREAR_TABLA = """
    CREATE TABLE ventas (
        id_viaje       TEXT    NOT NULL,
        numero_asiento INTEGER NOT NULL,
        id_cliente     INTEGER NOT NULL,
        nombre_cliente TEXT    NOT NULL,
        precio         INTEGER NOT NULL,
        vendido_en     TEXT    NOT NULL,
        -- la base tampoco acepta dos boletos para el mismo asiento
        PRIMARY KEY (id_viaje, numero_asiento)
    )
"""


class RegistroVentas:
    def __init__(self, ruta):
        self.ruta = ruta
        self.cola = queue.Queue()  # sin límite: registrar() nunca bloquea al worker
        self.hilo = threading.Thread(target=self._escribir, name="escritor-sqlite")
        self.rechazadas_por_duplicado = 0

    def __enter__(self):
        self.hilo.start()
        return self

    def __exit__(self, *error):
        self.cola.put(None)  # centinela: no hay más ventas
        self.hilo.join()

    def registrar(self, viaje, reserva):
        """Lo llaman los workers después de confirmar una compra."""
        venta = (viaje.id, reserva.numero_asiento, reserva.cliente.id, reserva.cliente.nombre,
                 viaje.precio, datetime.now().isoformat(timespec="seconds"))
        self.cola.put(venta)

    def _escribir(self):
        """El hilo escritor: el único que toca la base."""
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        conexion = sqlite3.connect(self.ruta)
        try:
            conexion.execute("DROP TABLE IF EXISTS ventas")
            conexion.execute(CREAR_TABLA)
            while True:
                venta = self.cola.get()
                if venta is None:  # centinela
                    break
                try:
                    conexion.execute("INSERT INTO ventas VALUES (?, ?, ?, ?, ?, ?)", venta)
                    conexion.commit()
                except sqlite3.IntegrityError:
                    # Solo pasaría si se vendiera un asiento dos veces.
                    self.rechazadas_por_duplicado += 1
        finally:
            conexion.close()


def ultimas_ventas(ruta, cantidad=10):
    """Las últimas ventas guardadas, la más nueva primero.

    Abre su propia conexión: SQLite deja leer mientras el escritor trabaja."""
    conexion = sqlite3.connect(ruta)
    try:
        return conexion.execute(
            "SELECT id_viaje, numero_asiento, nombre_cliente, precio, vendido_en "
            "FROM ventas ORDER BY rowid DESC LIMIT ?", (cantidad,)
        ).fetchall()
    finally:
        conexion.close()


def contar_ventas(ruta):
    """Cuántas ventas quedaron guardadas. Se llama con el escritor ya cerrado."""
    conexion = sqlite3.connect(ruta)
    try:
        (cantidad,) = conexion.execute("SELECT COUNT(*) FROM ventas").fetchone()
        return cantidad
    finally:
        conexion.close()
