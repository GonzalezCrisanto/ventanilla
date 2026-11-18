"""Parámetros de la simulación en un solo lugar.

Igual que el config de la cursada: cada valor tiene un default y se puede
cambiar con una variable de entorno, sin tocar el código. Por ejemplo, en
PowerShell:

    $env:CLIENTES=300; $env:WORKERS=8; python demo_carrera.py

Escala de tiempo: 1 segundo de simulación representa 1 minuto real. Así la
reserva de "2 minutos" dura 2 segundos y la demo no se hace eterna.

El resto del código lee estos valores como config.NOMBRE en el momento de
usarlos, así los tests pueden cambiarlos sin tocar este archivo.
"""
import os
from pathlib import Path

CARPETA_PROYECTO = Path(__file__).resolve().parent.parent


def _entero(nombre, defecto):
    return int(os.environ.get(nombre, defecto))


def _decimal(nombre, defecto):
    return float(os.environ.get(nombre, defecto))


# --- Carga de la simulación ---
CLIENTES = _entero("CLIENTES", 200)                       # hilos cliente
WORKERS = _entero("WORKERS", 8)                           # hilos que atienden la cola de pedidos
MAX_PEDIDOS_EN_COLA = _entero("MAX_PEDIDOS_EN_COLA", 50)  # freno: con la cola llena, el cliente espera
REPETICIONES = _entero("REPETICIONES", 3)                 # corridas por versión en la demo

# --- Reservas ---
TIEMPO_RESERVA = _decimal("TIEMPO_RESERVA", 2.0)                  # los "2 minutos" de la reserva
TIEMPO_RESERVA_CONSOLA = _decimal("TIEMPO_RESERVA_CONSOLA", 30.0)  # en la consola reserva una persona
INTERVALO_VENCIMIENTOS = _decimal("INTERVALO_VENCIMIENTOS", 0.1)  # cada cuánto se buscan reservas vencidas
VENTANA_CARRERA = _decimal("VENTANA_CARRERA", 0.001)              # trabajo entre leer y escribir un asiento

# --- Pasarela de pago ---
CAPACIDAD_PASARELA = _entero("CAPACIDAD_PASARELA", 4)                 # cobros a la vez que soporta
PAGOS_SIMULTANEOS = _entero("PAGOS_SIMULTANEOS", CAPACIDAD_PASARELA)  # permisos del semáforo (versión segura)
DEMORA_PAGO = _decimal("DEMORA_PAGO", 0.05)                           # lo que tarda en aprobar un cobro

# --- Comportamiento de los clientes ---
INTENTOS_POR_CLIENTE = _entero("INTENTOS_POR_CLIENTE", 3)  # asientos que prueba antes de rendirse
DEMORA_CLIENTE_MIN = _decimal("DEMORA_CLIENTE_MIN", 0.05)  # lo que tarda en cargar los datos de pago
DEMORA_CLIENTE_MAX = _decimal("DEMORA_CLIENTE_MAX", 0.15)
PROB_ABANDONO = _decimal("PROB_ABANDONO", 0.1)             # reserva y nunca paga
TIMEOUT_RESPUESTA = _decimal("TIMEOUT_RESPUESTA", 10.0)    # cuánto espera el cliente una respuesta

# --- Web ---
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")  # "0.0.0.0" para abrirla desde otros equipos de la red
WEB_PUERTO = _entero("WEB_PUERTO", 8000)

# La web arranca con esta boletería; en la página se cambia con un botón.
MODO_INICIAL = os.environ.get("MODO", "segura")  # "segura" o "insegura"

# --- Persistencia ---
RUTA_BASE = Path(os.environ.get("RUTA_BASE", CARPETA_PROYECTO / "resultados" / "ventas.db"))

SEMILLA = _entero("SEMILLA", 42)
