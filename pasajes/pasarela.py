"""Pasarela de pago simulada (un servicio externo).

Tarda en responder (time.sleep, que suelta el GIL) y tiene una capacidad:
si recibe más cobros a la vez de los que soporta, rechaza los que sobran,
como el servidor saturado de la clase 5.

Respetar ese límite es responsabilidad de quien la usa. La boletería segura
lo hace con un semáforo; la insegura no, y por eso pierde cobros con carga.

Ojo: el Lock de esta clase es interno del servicio simulado (lleva la cuenta
de cuántos cobros está atendiendo). No protege nada de la boletería.
"""
import threading
import time

from pasajes import config


class Pasarela:
    def __init__(self):
        self.capacidad = config.CAPACIDAD_PASARELA
        self._lock = threading.Lock()
        self.en_curso = 0                # cobros atendiéndose ahora (la consola lo muestra)
        self.maximo_en_curso = 0         # el pico de cobros simultáneos que recibió
        self.rechazos_por_saturacion = 0

    def cobrar(self, monto):
        """Devuelve True si aprobó el cobro y False si estaba saturada."""
        with self._lock:
            if self.en_curso >= self.capacidad:
                self.rechazos_por_saturacion += 1
                return False
            self.en_curso += 1
            self.maximo_en_curso = max(self.maximo_en_curso, self.en_curso)
        try:
            time.sleep(config.DEMORA_PAGO)  # el banco tarda en contestar
        finally:
            with self._lock:
                self.en_curso -= 1
        return True
