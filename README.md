# Venta de pasajes de micro con concurrencia

Proyecto final de Programación Avanzada (Universidad Atlántida Argentina, segundo cuatrimestre 2026).

Simula la venta de pasajes de micro de larga distancia: muchos clientes, cada uno un hilo, intentan
comprar asientos de los mismos viajes al mismo tiempo. Los pedidos pasan por una cola que atiende una
cantidad fija de workers. Cada reserva retiene el asiento un tiempo y, si no se paga, vence y el asiento
se libera. Hay dos versiones de la boletería:

- **Insegura** (`pasajes/boleteria_insegura.py`): sin sincronización, a propósito. Vende asientos dos veces.
- **Segura** (`pasajes/boleteria_segura.py`): un `Lock` por viaje y un `BoundedSemaphore` que limita los
  cobros simultáneos. Nunca vende un asiento dos veces ni pierde ventas.

Las ventas se guardan en SQLite con un único hilo escritor.

## Requisitos

Python 3.12. El sistema usa solo la biblioteca estándar; pytest y matplotlib hacen falta para las
pruebas y para el gráfico:

```
pip install -r requirements.txt
```

## Cómo ejecutarlo

Desde la carpeta del proyecto:

| Comando | Qué hace |
| --- | --- |
| `python web.py` | Ventanilla, la web: abrí `http://127.0.0.1:8000` para comprar, ver el micro en vivo y lanzar clientes simulados |
| `python consola.py` | El sistema funcionando: ver viajes y el mapa de asientos, comprar, lanzar clientes simulados y mirar un viaje en vivo |
| `python demo_carrera.py` | La versión insegura contra la segura con la misma carga; muestra cuántos asientos se vendieron dos veces (unos 12 s) |
| `python benchmark.py` | Mide con 1 a 16 workers y el control del GIL; guarda `resultados/benchmark.csv` y `resultados/benchmark.png` (unos 2 min) |
| `python -m pytest` | Las 51 pruebas automáticas (unos 27 s) |

`web.py`, `consola.py` y `demo_carrera.py` usan la misma base de datos: no los corras al mismo tiempo.

La web atiende cada pedido HTTP en su propio hilo (`ThreadingHTTPServer`) y manda las compras a la
misma cola que la consola. Las fuentes Barlow están en `web/fuentes/` con su licencia (SIL Open Font
License), así la página funciona sin internet.

## Configuración

Los parámetros están en `pasajes/config.py` y se cambian con variables de entorno, sin tocar el código.
Por ejemplo, en PowerShell:

```
$env:WORKERS=1; python demo_carrera.py
```

| Variable | Valor por defecto | Qué controla |
| --- | --- | --- |
| `CLIENTES` | 200 | Hilos cliente en la demo |
| `WORKERS` | 8 | Hilos que atienden la cola de pedidos |
| `TIEMPO_RESERVA` | 2 s | Duración de una reserva (escala: 1 s representa 1 minuto) |
| `TIEMPO_RESERVA_CONSOLA` | 30 s | Duración de una reserva en la consola, donde compra una persona |
| `CAPACIDAD_PASARELA` | 4 | Cobros a la vez que soporta la pasarela de pago |
| `DEMORA_PAGO` | 0,05 s | Lo que tarda cada cobro |
| `REPETICIONES` | 3 | Corridas por versión en la demo y por medición en el benchmark |
| `WEB_HOST` | 127.0.0.1 | Dónde escucha la web; `0.0.0.0` la abre a otros equipos de la red |
| `WEB_PUERTO` | 8000 | Puerto de la web |

## Estructura

```
pasajes/
    config.py               parámetros
    modelo.py               Viaje, Asiento, Cliente, Reserva y sus estados
    catalogo.py             viajes de ejemplo
    pasarela.py             pago simulado con capacidad limitada
    boleteria_insegura.py   versión 1, sin sincronización
    boleteria_segura.py     versión 2, Lock por viaje y semáforo
    simulacion.py           clientes, cola de pedidos, workers, vencimientos y cierre ordenado
    persistencia.py         hilo escritor de SQLite
    sistema.py              el sistema prendido, compartido por la consola y la web
    verificacion.py         controles: duplicados, ventas perdidas, asientos colgados
tests/                      pruebas con pytest
web/                        página, estilos, JavaScript, logo y fuentes de la web
web.py                      servidor web
consola.py                  interfaz de consola
demo_carrera.py             demo de la condición de carrera
benchmark.py                mediciones y gráfico
resultados/                 base de datos, CSV y gráfico generados
```
