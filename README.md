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
| `python web.py` | Ventanilla, la web: abrí `http://127.0.0.1:8000` para comprar, ver el micro en vivo, lanzar clientes simulados, cambiar de boletería y mirar el panel de admin (ver [La demo en la web](#la-demo-en-la-web)) |
| `python consola.py` | El sistema funcionando: ver viajes y el mapa de asientos, comprar, lanzar clientes simulados y mirar un viaje en vivo |
| `python demo_carrera.py` | La versión insegura contra la segura con la misma carga; muestra cuántos asientos se vendieron dos veces (unos 12 s) |
| `python benchmark.py` | Mide con 1 a 16 workers y el control del GIL; guarda `resultados/benchmark.csv` y `resultados/benchmark.png` (unos 2 min) |
| `python -m pytest` | Las 71 pruebas automáticas (unos 35 s) |
| `node --test tests/frontend.test.cjs tests/frontend_demo.test.cjs` | Las 20 pruebas del JavaScript de la web (opcional, hace falta Node) |

`web.py`, `consola.py` y `demo_carrera.py` usan la misma base de datos: no los corras al mismo tiempo.

La web atiende cada pedido HTTP en su propio hilo (`ThreadingHTTPServer`) y manda las compras a la
misma cola que la consola. Las fuentes Barlow están en `web/fuentes/` con su licencia (SIL Open Font
License), así la página funciona sin internet.

## La demo en la web

Todo está en una misma página, pensada para presentarla:

- **Boletería segura / insegura.** El selector de arriba cambia la boletería que atiende los pedidos. La
  pantalla se pone roja en modo inseguro, para que se note a la distancia. Cambiar de modo reinicia la simulación.
- **Reiniciar.** Cierra el sistema, abre uno nuevo con todos los asientos libres y deja la corrida anterior
  guardada para compararla. Tarda una fracción de segundo aunque haya 150 clientes comprando.
- **Panel de admin** (botón de arriba a la derecha, o `/#/admin`). Se actualiza solo cada segundo:
  - un veredicto con la cifra clave: cuántos asientos se vendieron dos veces y cuánto dinero se cobró de más;
  - qué pasó con cada reserva: compradas, vencidas sin pagar (reservaron y no compraron), rechazadas por la
    pasarela y en curso, más los pedidos que perdieron la carrera por un asiento;
  - ocupación e ingresos por viaje, la pasarela de pago (pico de cobros simultáneos y rechazos por saturación),
    la actividad del último minuto, las últimas compras y una tabla para comparar corridas.

Guion sugerido para la presentación: en **insegura**, lanzar 150 clientes y mirar el panel (aparecen asientos con
`2×` en el mapa y plata cobrada de más); **Reiniciar** con **segura**, lanzar los mismos 150 y comparar en
"Corridas anteriores".

En la boletería insegura no se guardan ventas en SQLite; en la segura sí (las guarda el hilo escritor).

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
| `MODO` | segura | Boletería con la que arranca la web (`segura` o `insegura`); en la página se cambia con un botón |

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
    sistema.py              el sistema prendido (segura o insegura), compartido por la consola y la web
    estadisticas.py         lo que pasó en la corrida, anotado por los workers
    analiticas.py           las cuentas del panel de admin
    verificacion.py         controles: duplicados, ventas perdidas, asientos colgados
tests/                      pruebas con pytest
web/                        página, estilos, JavaScript (app.js la venta, admin.js el panel), logo y fuentes
web.py                      servidor web
consola.py                  interfaz de consola
demo_carrera.py             demo de la condición de carrera
benchmark.py                mediciones y gráfico
resultados/                 base de datos, CSV y gráfico generados
```
