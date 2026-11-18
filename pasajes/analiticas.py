"""Las cuentas del panel de admin: qué se reservó, qué se compró y si hubo ventas dobles.

analizar() arma un diccionario con todo lo que muestra el panel (la web lo
manda tal cual como JSON). Mira el sistema mientras funciona, así que algunos
números se leen sin lock y pueden estar un instante desactualizados; nunca se
usan para decidir nada, solo para mostrar. Con el sistema ya cerrado, son exactos.

Qué es cada cosa:
    - reserva: un asiento que se le retuvo a un cliente. Termina en una de cuatro:
      comprada, vencida (reservó y no pagó a tiempo), rechazada (la pasarela
      saturada no aprobó el cobro) o en curso (todavía puede pagar).
    - asiento vendido dos veces: tiene más de un boleto. Solo pasa con la
      boletería insegura; es lo que el panel tiene que dejar bien a la vista.
    - cobrado de más: la plata de los boletos que sobran por esos asientos.
"""
import threading
import time
from collections import Counter
from datetime import datetime

from pasajes.estadisticas import VENTANA_ACTIVIDAD
from pasajes.modelo import EstadoAsiento, EstadoReserva

MAXIMO_HISTORIAL = 8


def _clasificar_reservas(reservas, boleteria, ahora):
    """Cuenta en qué terminó cada reserva. Devuelve un Counter con las cuatro claves."""
    cuenta = Counter()
    for reserva in reservas:
        if reserva.estado is EstadoReserva.CONFIRMADA:
            cuenta["compradas"] += 1
        elif reserva.estado is EstadoReserva.RECHAZADA:
            cuenta["rechazadas"] += 1
        elif reserva.estado is EstadoReserva.VENCIDA:
            cuenta["vencidas"] += 1
        else:  # ACTIVA
            asiento = boleteria.viajes[reserva.id_viaje].asientos[reserva.numero_asiento]
            pagando = asiento.estado is EstadoAsiento.EN_PAGO and asiento.reserva is reserva
            # Una activa pasada de hora ya no sirve: el hilo de vencimientos todavía no la marcó
            # (o la boletería insegura la dejó así, porque otra reserva pisó su asiento).
            cuenta["en_curso" if pagando or reserva.vence > ahora else "vencidas"] += 1
    return cuenta


def _analizar_viaje(viaje, boleteria):
    boletos = list(viaje.boletos)  # copia: en la boletería segura se agregan con el lock del viaje
    por_asiento = Counter(boleto.numero_asiento for boleto in boletos)
    dobles = sorted(numero for numero, cantidad in por_asiento.items() if cantidad > 1)
    de_mas = len(boletos) - len(por_asiento)  # boletos que sobran: un asiento no tiene dos pasajeros
    estados = Counter(estado for estado, _ in boleteria.foto_asientos(viaje.id).values())
    return {
        "id": viaje.id, "origen": viaje.origen, "destino": viaje.destino,
        "salida": f"{viaje.salida:%d/%m %H:%M}", "precio": viaje.precio,
        "asientos": len(viaje.asientos),
        "vendidos": estados[EstadoAsiento.VENDIDO],
        "reservados": estados[EstadoAsiento.RESERVADO] + estados[EstadoAsiento.EN_PAGO],
        "libres": estados[EstadoAsiento.LIBRE],
        "boletos": len(boletos), "dobles": dobles, "boletos_de_mas": de_mas,
        "ingresos": len(por_asiento) * viaje.precio,
        "cobrado_de_mas": de_mas * viaje.precio,
    }


def _actividad(eventos, ahora):
    """Reservas y compras por segundo, de los últimos VENTANA_ACTIVIDAD segundos (el más viejo primero)."""
    ventana = [{"reservas": 0, "compras": 0} for _ in range(VENTANA_ACTIVIDAD)]
    for instante, tipo in eventos:
        hace = int(ahora - instante)
        if 0 <= hace < VENTANA_ACTIVIDAD:
            ventana[VENTANA_ACTIVIDAD - 1 - hace]["reservas" if tipo == "reserva" else "compras"] += 1
    return ventana


def _veredicto(modo, boletos, dobles, de_mas, cobrado_de_mas):
    if dobles:
        pasajeros = "pasajero con boleto" if de_mas == 1 else "pasajeros con boleto"
        dinero = f"${cobrado_de_mas:,}".replace(",", ".")
        return {"nivel": "mal", "titulo": "Se vendieron asientos dos veces",
                "detalle": f"Hay {de_mas} {pasajeros} para un asiento que ya era de otro. "
                           f"Se cobraron {dinero} de más y hay que devolverlos."}
    if modo == "segura":
        if not boletos:
            return {"nivel": "neutro", "titulo": "Sin ventas todavía",
                    "detalle": "Cuando haya compras, acá se controla que cada asiento tenga un solo pasajero."}
        return {"nivel": "ok", "titulo": "Ninguna venta doble",
                "detalle": "Cada asiento vendido tiene un solo pasajero: el Lock de cada viaje convierte "
                           "\"leer y reservar\" en un solo paso."}
    if not boletos:
        return {"nivel": "aviso", "titulo": "Modo inseguro, sin ventas todavía",
                "detalle": "Esta boletería no tiene Lock. Soltá clientes simulados para que compitan "
                           "por los mismos asientos."}
    return {"nivel": "aviso", "titulo": "Todavía no se vendió ningún asiento dos veces",
            "detalle": "Esta boletería no tiene Lock: cuantos más clientes compitan a la vez, "
                       "más probable es que dos se queden con el mismo asiento."}


def analizar(sistema, historial=()):
    """Todo lo que muestra el panel de admin, en un diccionario listo para JSON."""
    ahora = time.monotonic()
    foto = sistema.estadisticas.foto()
    boleteria = sistema.boleteria

    reservas = _clasificar_reservas(foto.reservas, boleteria, ahora)
    viajes = [_analizar_viaje(viaje, boleteria) for viaje in boleteria.viajes.values()]
    boletos = sum(v["boletos"] for v in viajes)
    dobles = sum(len(v["dobles"]) for v in viajes)
    de_mas = sum(v["boletos_de_mas"] for v in viajes)
    cobrado_de_mas = sum(v["cobrado_de_mas"] for v in viajes)
    ingresos = sum(v["ingresos"] for v in viajes)
    resueltas = reservas["compradas"] + reservas["vencidas"] + reservas["rechazadas"]
    pasarela = boleteria.pasarela
    precios = {v["id"]: v["precio"] for v in viajes}

    return {
        "modo": sistema.modo,
        "nombre_modo": boleteria.nombre,
        "segundos": round(sistema.segundos_encendido(), 1),
        "veredicto": _veredicto(sistema.modo, boletos, dobles, de_mas, cobrado_de_mas),
        "integridad": {"boletos": boletos, "asientos_dobles": dobles, "boletos_de_mas": de_mas,
                       "cobrado_de_mas": cobrado_de_mas},
        "reservas": {
            "total": len(foto.reservas),
            "compradas": reservas["compradas"], "vencidas": reservas["vencidas"],
            "rechazadas": reservas["rechazadas"], "en_curso": reservas["en_curso"],
            # De las que ya terminaron, qué parte terminó en compra. None si todavía no terminó ninguna.
            "conversion": round(reservas["compradas"] / resueltas, 3) if resueltas else None,
            "perdidas": foto.reservas_perdidas,      # pedidos que perdieron la carrera por el asiento
            "pagos_tarde": foto.pagos_tarde,         # intentaron pagar con la reserva ya vencida
        },
        "ingresos": {"validos": ingresos, "cobrado_de_mas": cobrado_de_mas, "cobrado": ingresos + cobrado_de_mas},
        "viajes": viajes,
        "pasarela": {"capacidad": pasarela.capacidad, "en_curso": pasarela.en_curso,
                     "pico": pasarela.maximo_en_curso, "rechazos": pasarela.rechazos_por_saturacion,
                     "rechazados_en_ventas": foto.pagos_rechazados},
        "sistema": {"hilos": threading.active_count(), "workers": len(sistema.workers),
                    "cola": sistema.cola.qsize(), "clientes_lanzados": sistema.clientes_lanzados(),
                    "clientes_comprando": sistema.clientes_comprando(), "errores": foto.errores},
        "actividad": _actividad(foto.eventos, ahora),
        "ultimas_compras": [
            {"hace": round(ahora - instante, 1), "viaje": id_viaje, "asiento": asiento, "cliente": cliente,
             "precio": precios[id_viaje]}
            for instante, id_viaje, asiento, cliente in reversed(foto.compras)
        ],
        "historial": list(historial),
    }


def resumen_corrida(analisis):
    """Lo que se guarda de una corrida cuando se la reinicia, para compararla con la siguiente."""
    reservas, integridad = analisis["reservas"], analisis["integridad"]
    return {
        "hora": datetime.now().strftime("%H:%M:%S"),
        "modo": analisis["modo"],
        "duracion": analisis["segundos"],
        "reservas": reservas["total"], "compradas": reservas["compradas"], "vencidas": reservas["vencidas"],
        "rechazadas": reservas["rechazadas"],
        "asientos_dobles": integridad["asientos_dobles"], "cobrado_de_mas": integridad["cobrado_de_mas"],
    }
