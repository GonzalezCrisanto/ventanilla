"""Ventanilla: la web del sistema, atendida por HTTP.

    navegador ──HTTP──▶ ThreadingHTTPServer ──un hilo por pedido──▶ Manejador
                                                                       │
            consultas: foto_asientos() con el lock del viaje ◀─────────┤
            compras:   Pedido ──▶ la misma cola de la consola ◀─────────┘

Cada pedido HTTP lo atiende un hilo nuevo (el modelo de "un hilo por
cliente" de la clase 5). Ese hilo es un cliente más: las compras pasan por la
misma cola y los mismos workers que en la consola, así que no hay ningún
camino nuevo para tocar los asientos.

El navegador vuelve a pedir el mapa cada medio segundo. Cada consulta es
corta y libera su hilo enseguida.

Regla del vencimiento (decidida al probar la web): la reserva tiene que
seguir vigente cuando un worker ATIENDE el pago, no cuando se manda. Con mucha
carga, el pedido de pago puede esperar en la cola y la reserva vencer en el
medio: por la ley de Little, con unos 8 cobros por segundo y 26 pedidos en la
fila, se espera unos 3 s. Nadie se queda con un asiento ajeno; el asiento
vuelve a estar libre y la web le explica al cliente qué pasó.

Si ningún worker toma un pedido en TIMEOUT_RESPUESTA, el pedido se abandona y
NO se ejecuta (cancelación cooperativa, ver simulacion.pedir): la web contesta
503 sabiendo que no se reservó ni se cobró nada.

Cierre ordenado (Ctrl+C):
    1. Se cierra el servidor web y se espera a los pedidos HTTP en curso.
       Los workers siguen prendidos para contestarles.
    2 a 4. El cierre de siempre: clientes, centinelas, fin.set().
    5. El centinela del escritor de SQLite.

Uso:  python web.py                          # abrí http://127.0.0.1:8000
      $env:WEB_HOST="0.0.0.0"; python web.py  # también desde otros equipos de la red

La web, la consola y demo_carrera.py usan la misma base: no los corras a la vez.
"""
import json
import secrets
import threading
import time
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from pasajes import config
from pasajes.modelo import Cliente, EstadoAsiento, EstadoReserva
from pasajes.persistencia import ErrorRegistroVentas, RegistroVentas
from pasajes.simulacion import Pedido, PedidoAbandonado, pedir
from pasajes.sistema import Sistema

CARPETA_WEB = (config.CARPETA_PROYECTO / "web").resolve()
TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
ESTADOS = {EstadoAsiento.LIBRE: "libre", EstadoAsiento.RESERVADO: "reservado",
           EstadoAsiento.EN_PAGO: "pagando", EstadoAsiento.VENDIDO: "vendido"}
PRIMER_ID_WEB = 100_001  # los clientes simulados se numeran desde 1


class MemoriaWeb:
    """Lo que la web recuerda entre un pedido HTTP y el siguiente.

    La reserva se hace en un pedido y se paga en otro, que puede atender otro
    hilo: por eso se guarda acá con un código. Lo comparten todos los hilos
    HTTP, así que tiene su propio lock. Ese lock se toma solo para leer o
    escribir estos diccionarios, nunca junto con el lock de un viaje."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clientes = {}  # id -> Cliente
        self._reservas = {}  # código -> Reserva
        self._proximo_id = PRIMER_ID_WEB

    def nuevo_cliente(self):
        with self._lock:
            cliente = Cliente(self._proximo_id, f"Web {self._proximo_id - PRIMER_ID_WEB + 1}")
            self._clientes[cliente.id] = cliente
            self._proximo_id += 1
        return cliente

    def cliente(self, id_cliente):
        if not isinstance(id_cliente, int):  # viene del navegador: puede ser cualquier cosa
            return None
        with self._lock:
            return self._clientes.get(id_cliente)

    def guardar_reserva(self, reserva):
        with self._lock:
            codigo = secrets.token_hex(4)
            while codigo in self._reservas:
                codigo = secrets.token_hex(4)
            self._reservas[codigo] = reserva
        return codigo

    def reserva(self, codigo):
        with self._lock:
            return self._reservas.get(codigo)


class ServidorVentanilla(ThreadingHTTPServer):
    # Por defecto ThreadingHTTPServer crea sus hilos como daemon: el programa
    # podría terminar con una compra a medio atender. Los queremos normales,
    # y que server_close() los espere a todos (block_on_close).
    daemon_threads = False
    block_on_close = True
    # Conexiones que el sistema operativo deja esperando mientras el servidor
    # las acepta. El valor por defecto (5) se llena cuando llegan muchos
    # navegadores juntos y Linux corta los que sobran con "connection reset".
    request_queue_size = 64

    def __init__(self, direccion, sistema):
        super().__init__(direccion, Manejador)
        self.sistema = sistema
        self.memoria = MemoriaWeb()


def describir_viaje(viaje, foto):
    estados = Counter(estado for estado, _ in foto.values())
    return {
        "id": viaje.id, "origen": viaje.origen, "destino": viaje.destino,
        "salida": f"{viaje.salida:%d/%m %H:%M}", "precio": viaje.precio,
        "libres": estados[EstadoAsiento.LIBRE], "reservados": estados[EstadoAsiento.RESERVADO],
        "pagando": estados[EstadoAsiento.EN_PAGO], "vendidos": estados[EstadoAsiento.VENDIDO],
    }


class Manejador(BaseHTTPRequestHandler):
    """Atiende UN pedido HTTP. El servidor crea un hilo nuevo por cada pedido."""

    server_version = "Ventanilla/1.0"

    # --- Respuestas ---------------------------------------------------------

    def responder_json(self, estado, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def responder_error(self, estado, mensaje):
        self.responder_json(estado, {"error": mensaje})

    def responder_archivo(self, nombre):
        archivo = (CARPETA_WEB / nombre).resolve()
        # Solo archivos de la carpeta web/: "../" no puede escaparse de ahí.
        if not archivo.is_relative_to(CARPETA_WEB) or not archivo.is_file() or archivo.suffix not in TIPOS:
            return self.responder_error(HTTPStatus.NOT_FOUND, "No existe ese archivo.")
        cuerpo = archivo.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", TIPOS[archivo.suffix])
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def leer_json(self):
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return None
        return datos if isinstance(datos, dict) else None

    def log_request(self, code="-", size="-"):
        # El mapa se pide cada medio segundo: solo se anotan las compras.
        if self.command == "POST":
            super().log_request(code, size)

    # --- Rutas ----------------------------------------------------------------

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            return self.responder_archivo("index.html")
        if url.path.startswith("/estaticos/"):
            return self.responder_archivo(url.path.removeprefix("/estaticos/"))
        if url.path == "/api/viajes":
            return self.ver_viajes()
        if url.path.startswith("/api/viajes/"):
            id_cliente = parse_qs(url.query).get("cliente", ["0"])[0]
            return self.ver_mapa(url.path.removeprefix("/api/viajes/"), id_cliente)
        self.responder_error(HTTPStatus.NOT_FOUND, "No existe esa dirección.")

    def do_HEAD(self):
        # Algunas herramientas lo usan para ver si el servidor está prendido.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_POST(self):
        ruta = urlsplit(self.path).path
        datos = self.leer_json()
        if datos is None:
            return self.responder_error(HTTPStatus.BAD_REQUEST, "El pedido no es un JSON válido.")
        if ruta == "/api/sesion":
            cliente = self.server.memoria.nuevo_cliente()
            return self.responder_json(HTTPStatus.CREATED, {"cliente": cliente.id, "nombre": cliente.nombre})
        if ruta == "/api/reservas":
            return self.reservar(datos)
        if ruta.startswith("/api/reservas/") and ruta.endswith("/pago"):
            return self.pagar(ruta.removeprefix("/api/reservas/").removesuffix("/pago"), datos)
        if ruta == "/api/simulacion":
            return self.lanzar_clientes(datos)
        self.responder_error(HTTPStatus.NOT_FOUND, "No existe esa dirección.")

    # --- Consultas: leen con el lock del viaje, no pasan por la cola ----------

    def ver_viajes(self):
        boleteria = self.server.sistema.boleteria
        viajes = [describir_viaje(v, boleteria.foto_asientos(v.id)) for v in boleteria.viajes.values()]
        self.responder_json(HTTPStatus.OK, viajes)

    def ver_mapa(self, id_viaje, id_cliente):
        sistema = self.server.sistema
        if id_viaje not in sistema.boleteria.viajes:
            return self.responder_error(HTTPStatus.NOT_FOUND, "No existe ese viaje.")
        foto = sistema.boleteria.foto_asientos(id_viaje)  # una sola lectura, con el lock del viaje
        asientos = [
            {"numero": numero, "estado": ESTADOS[estado],
             "tuyo": cliente is not None and str(cliente.id) == id_cliente}
            for numero, (estado, cliente) in sorted(foto.items())
        ]
        vendidos = sum(describir_viaje(v, sistema.boleteria.foto_asientos(v.id))["vendidos"]
                       for v in sistema.boleteria.viajes.values())
        pasarela = sistema.boleteria.pasarela
        self.responder_json(HTTPStatus.OK, {
            "viaje": describir_viaje(sistema.boleteria.viajes[id_viaje], foto),
            "asientos": asientos,
            # Números para mirar: algunos se leen sin lock y pueden estar un
            # instante desactualizados, pero nunca se usan para decidir nada.
            "sistema": {
                "hilos": threading.active_count(), "cola": sistema.cola.qsize(),
                "cobros": pasarela.en_curso, "capacidad": pasarela.capacidad,
                "comprando": sistema.clientes_comprando(), "vendidos": vendidos,
            },
        })

    # --- Compras: el hilo HTTP es un cliente más y usa la misma cola ---------

    def reservar(self, datos):
        sistema = self.server.sistema
        cliente = self.server.memoria.cliente(datos.get("cliente"))
        id_viaje, numero = datos.get("viaje"), datos.get("asiento")
        if cliente is None:
            return self.responder_error(HTTPStatus.UNAUTHORIZED, "Tu sesión no existe: recargá la página.")
        if id_viaje not in sistema.boleteria.viajes or numero not in sistema.boleteria.viajes[id_viaje].asientos:
            return self.responder_error(HTTPStatus.BAD_REQUEST, "Ese asiento no existe.")
        try:
            reserva = pedir(sistema.cola, Pedido("reservar", cliente, id_viaje, numero))
        except PedidoAbandonado:  # ningún worker lo tomó a tiempo: es seguro que no se hizo
            return self.responder_error(HTTPStatus.SERVICE_UNAVAILABLE,
                                        "Nadie atendió tu pedido a tiempo: no se hizo nada. Probá de nuevo.")
        if reserva is None:
            return self.responder_error(HTTPStatus.CONFLICT, "Ese asiento ya no está libre: otro cliente lo ganó.")
        codigo = self.server.memoria.guardar_reserva(reserva)
        self.responder_json(HTTPStatus.CREATED, {
            "codigo": codigo, "viaje": id_viaje, "asiento": numero,
            "precio": sistema.boleteria.viajes[id_viaje].precio,
            "vence_en": max(0.0, reserva.vence - time.monotonic()),
        })

    def pagar(self, codigo, datos):
        reserva = self.server.memoria.reserva(codigo)
        if reserva is None:
            return self.responder_error(HTTPStatus.NOT_FOUND, "No existe esa reserva.")
        if reserva.cliente.id != datos.get("cliente"):
            return self.responder_error(HTTPStatus.FORBIDDEN, "Esa reserva no es tuya.")
        enviado = time.monotonic()  # el mismo reloj que usan las reservas
        try:
            ok, motivo = pedir(self.server.sistema.cola, Pedido("confirmar", reserva.cliente, reserva=reserva))
        except PedidoAbandonado:  # ningún worker lo tomó a tiempo: es seguro que no se hizo
            return self.responder_error(HTTPStatus.SERVICE_UNAVAILABLE,
                                        "Nadie atendió tu pedido a tiempo: no se hizo nada. Probá de nuevo.")
        except ErrorRegistroVentas:
            return self.responder_error(HTTPStatus.INTERNAL_SERVER_ERROR,
                                        "Falló el registro de ventas. Consultá el estado de tu asiento antes de reintentar.")
        # Una reserva VENCIDA ya no cambia más: se puede leer sin el lock del viaje.
        if not ok and reserva.estado is EstadoReserva.VENCIDA and enviado < reserva.vence:
            return self.responder_error(HTTPStatus.CONFLICT, "Tu reserva venció mientras el pago esperaba en la fila. "
                                                             "El asiento volvió a quedar libre.")
        if not ok and reserva.estado is EstadoReserva.VENCIDA:
            return self.responder_error(HTTPStatus.CONFLICT, "Tu reserva ya había vencido cuando mandaste el pago.")
        if not ok:
            return self.responder_error(HTTPStatus.CONFLICT, f"No se pudo: {motivo}.")
        self.responder_json(HTTPStatus.OK, {"mensaje": f"Listo: {motivo}.", "asiento": reserva.numero_asiento})

    def lanzar_clientes(self, datos):
        cantidad = datos.get("cantidad")
        if not isinstance(cantidad, int) or not 1 <= cantidad <= 300:
            return self.responder_error(HTTPStatus.BAD_REQUEST, "La cantidad tiene que ser un número entre 1 y 300.")
        self.server.sistema.lanzar_clientes(cantidad)
        self.responder_json(HTTPStatus.ACCEPTED, {"lanzados": cantidad})


def main():
    # Igual que en la consola: compran personas, así que los tiempos son más
    # largos, y el cobro tarda medio segundo para que se vean los asientos pagando.
    config.TIEMPO_RESERVA = config.TIEMPO_RESERVA_CONSOLA
    config.DEMORA_CLIENTE_MIN, config.DEMORA_CLIENTE_MAX = 0.5, 4.0
    config.DEMORA_PAGO = 0.5

    with RegistroVentas(config.RUTA_BASE) as registro:
        sistema = Sistema(registro)
        sistema.iniciar()
        servidor = ServidorVentanilla((config.WEB_HOST, config.WEB_PUERTO), sistema)
        print(f"Ventanilla funcionando en http://{config.WEB_HOST}:{config.WEB_PUERTO}  (Ctrl+C para cerrar)")
        print(f"{config.WORKERS} workers, hilo de vencimientos y escritor de SQLite. "
              f"Las reservas duran {config.TIEMPO_RESERVA:.0f} s.")
        try:
            servidor.serve_forever()  # atiende pedidos hasta el Ctrl+C
        except KeyboardInterrupt:
            print("\n  Cortaste con Ctrl+C.")
        finally:
            print("\nCierre ordenado:")
            print("  1. Cierro el servidor web y espero los pedidos HTTP en curso (los workers siguen atendiendo).")
            servidor.server_close()
            sistema.detener(paso=2)
            print("  5. Pongo el centinela en la cola del escritor de SQLite y lo espero.")
    print(f"Listo. Hilos vivos: {threading.active_count()} (solo el principal).")


if __name__ == "__main__":
    main()
