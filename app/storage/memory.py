"""Historial de conversación por chat, persistido en SQLite.

Antes vivía solo en RAM y cada reinicio (o cada redespliegue) borraba el
contexto de todas las conversaciones. Ahora se guarda en la misma base de
datos que los usuarios, de modo que el asistente recuerda de qué se estaba
hablando aunque el proceso se reinicie.

Se conservan solo los últimos `max_messages` mensajes por chat: es lo que
cabe razonablemente en el contexto del modelo y evita que la base crezca
sin control.
"""

import asyncio
import sqlite3
from typing import Any

from app.clock import utcnow_iso
from app.storage.db import Database


class ConversationMemory:
    def __init__(self, db: Database, max_messages: int) -> None:
        self._db = db
        self._max_messages = max_messages

    async def get(self, chat_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_sync, chat_id)

    async def append(self, chat_id: int, message: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self._append_sync, chat_id, message["role"], message["content"]
        )

    async def clear(self, chat_id: int) -> None:
        await asyncio.to_thread(self._clear_sync, chat_id)

    # -- Implementación síncrona -------------------------------------------

    def _get_sync(self, chat_id: int) -> list[dict[str, Any]]:
        with self._db.cursor() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (chat_id, self._max_messages),
            ).fetchall()
        # Se piden en orden inverso para quedarnos con los más recientes,
        # pero el modelo necesita la conversación en orden cronológico.
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def _append_sync(self, chat_id: int, role: str, content: str) -> None:
        with self._db.cursor() as conn:
            conn.execute(
                "INSERT INTO messages (chat_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, role, content, utcnow_iso()),
            )
            self._prune(conn, chat_id)

    def _prune(self, conn: sqlite3.Connection, chat_id: int) -> None:
        conn.execute(
            "DELETE FROM messages WHERE chat_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
            (chat_id, chat_id, self._max_messages),
        )

    def _clear_sync(self, chat_id: int) -> None:
        with self._db.cursor() as conn:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
