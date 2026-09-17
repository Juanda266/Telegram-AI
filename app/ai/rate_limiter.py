"""Limitador de peticiones para no superar la cuota de OpenRouter.

El nivel gratuito permite unas 20 peticiones por minuto **por API key**, no
por usuario. Como una sola pregunta puede consumir varias peticiones (el
agente busca, lee y luego responde), basta con dos o tres personas
escribiendo a la vez para empezar a recibir 429 y que el bot falle.

Este limitador hace esperar a las peticiones que excederían la cuota, en
vez de dejar que fallen.
"""

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_calls: int, period_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._period = period_seconds
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Espera lo necesario para no exceder la cuota y registra la llamada."""
        while True:
            async with self._lock:
                now = time.monotonic()
                self._descartar_antiguas(now)

                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return

                espera = self._period - (now - self._calls[0])

            # Se espera fuera del lock para no bloquear a los demás.
            logger.info("Límite de peticiones alcanzado, esperando %.1fs", espera)
            await asyncio.sleep(max(espera, 0.01))

    def _descartar_antiguas(self, now: float) -> None:
        limite = now - self._period
        while self._calls and self._calls[0] <= limite:
            self._calls.popleft()
