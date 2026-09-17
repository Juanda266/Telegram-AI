"""Historial de conversación en memoria, por chat de Telegram.

Es intencionalmente simple (un diccionario en RAM). Si el proceso se reinicia
se pierde el historial, lo cual es un compromiso aceptable para un asistente
personal de un solo proceso. Si más adelante se necesita persistencia real
(reinicios, múltiples workers), este es el punto para cambiar a Redis/SQLite
sin tocar el resto de la aplicación.
"""

from collections import defaultdict, deque
from typing import Any


class ConversationMemory:
    def __init__(self, max_messages: int) -> None:
        self._max_messages = max_messages
        self._history: dict[int, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def get(self, chat_id: int) -> list[dict[str, Any]]:
        return list(self._history[chat_id])

    def append(self, chat_id: int, message: dict[str, Any]) -> None:
        self._history[chat_id].append(message)

    def clear(self, chat_id: int) -> None:
        self._history.pop(chat_id, None)
