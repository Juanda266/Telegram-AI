"""Persistencia en SQLite para usuarios y su estado de suscripción.

Se usa SQLite (en vez de solo memoria) porque el estado de facturación
(quién pagó, hasta cuándo) tiene que sobrevivir a reinicios del proceso.
El acceso es síncrono pero muy rápido (es un archivo local); se ejecuta en
un hilo aparte (`asyncio.to_thread`) para no bloquear el event loop.
"""

import asyncio
import contextlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from app.clock import utcnow_iso

DEFAULT_DB_PATH = Path("data") / "bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    is_premium INTEGER NOT NULL DEFAULT 0,
    premium_until TEXT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    free_used_today INTEGER NOT NULL DEFAULT 0,
    free_used_date TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_stripe_events (
    event_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, id);
"""


@dataclass
class UserRecord:
    telegram_user_id: int
    is_premium: bool
    premium_until: str | None
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    free_used_today: int
    free_used_date: str | None


class Database:
    """Wrapper fino sobre sqlite3, con un lock porque sqlite3 no es
    thread-safe si se comparte la misma conexión entre hilos."""

    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    @contextlib.contextmanager
    def cursor(self):
        """Acceso a la conexión para otros módulos de storage, con el lock
        y la transacción ya gestionados."""
        with self._lock, self._conn:
            yield self._conn

    # -- API asíncrona (usada por el resto de la app) ---------------------

    async def get_or_create_user(self, telegram_user_id: int) -> UserRecord:
        return await asyncio.to_thread(self._get_or_create_user_sync, telegram_user_id)

    async def increment_free_usage(self, telegram_user_id: int, today: str) -> int:
        return await asyncio.to_thread(
            self._increment_free_usage_sync, telegram_user_id, today
        )

    async def set_premium(
        self,
        telegram_user_id: int,
        is_premium: bool,
        premium_until: str | None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._set_premium_sync,
            telegram_user_id,
            is_premium,
            premium_until,
            stripe_customer_id,
            stripe_subscription_id,
        )

    async def find_user_by_customer_id(self, stripe_customer_id: str) -> UserRecord | None:
        return await asyncio.to_thread(self._find_user_by_customer_id_sync, stripe_customer_id)

    async def link_stripe_customer(
        self, telegram_user_id: int, stripe_customer_id: str
    ) -> None:
        await asyncio.to_thread(
            self._link_stripe_customer_sync, telegram_user_id, stripe_customer_id
        )

    async def get_stats(self, today: str) -> dict[str, int]:
        return await asyncio.to_thread(self._get_stats_sync, today)

    async def was_event_processed(self, event_id: str) -> bool:
        return await asyncio.to_thread(self._was_event_processed_sync, event_id)

    async def mark_event_processed(self, event_id: str) -> None:
        await asyncio.to_thread(self._mark_event_processed_sync, event_id)

    # -- Implementación síncrona -------------------------------------------

    def _row_to_record(self, row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            telegram_user_id=row["telegram_user_id"],
            is_premium=bool(row["is_premium"]),
            premium_until=row["premium_until"],
            stripe_customer_id=row["stripe_customer_id"],
            stripe_subscription_id=row["stripe_subscription_id"],
            free_used_today=row["free_used_today"],
            free_used_date=row["free_used_date"],
        )

    def _get_or_create_user_sync(self, telegram_user_id: int) -> UserRecord:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO users (telegram_user_id, created_at) VALUES (?, ?)",
                    (telegram_user_id, utcnow_iso()),
                )
                row = self._conn.execute(
                    "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
                ).fetchone()
            return self._row_to_record(row)

    def _increment_free_usage_sync(self, telegram_user_id: int, today: str) -> int:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT free_used_today, free_used_date FROM users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if row is None or row["free_used_date"] != today:
                new_count = 1
            else:
                new_count = row["free_used_today"] + 1
            self._conn.execute(
                "UPDATE users SET free_used_today = ?, free_used_date = ? "
                "WHERE telegram_user_id = ?",
                (new_count, today, telegram_user_id),
            )
            return new_count

    def _set_premium_sync(
        self,
        telegram_user_id: int,
        is_premium: bool,
        premium_until: str | None,
        stripe_customer_id: str | None,
        stripe_subscription_id: str | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET is_premium = ?, premium_until = ?, "
                "stripe_customer_id = COALESCE(?, stripe_customer_id), "
                "stripe_subscription_id = COALESCE(?, stripe_subscription_id) "
                "WHERE telegram_user_id = ?",
                (
                    int(is_premium),
                    premium_until,
                    stripe_customer_id,
                    stripe_subscription_id,
                    telegram_user_id,
                ),
            )

    def _find_user_by_customer_id_sync(self, stripe_customer_id: str) -> UserRecord | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,)
            ).fetchone()
            return self._row_to_record(row) if row else None

    def _link_stripe_customer_sync(self, telegram_user_id: int, stripe_customer_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET stripe_customer_id = ? WHERE telegram_user_id = ?",
                (stripe_customer_id, telegram_user_id),
            )

    def _get_stats_sync(self, today: str) -> dict[str, int]:
        with self._lock, self._conn:
            usuarios = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            premium = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE is_premium = 1"
            ).fetchone()["n"]
            activos_hoy = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE free_used_date = ?", (today,)
            ).fetchone()["n"]
            nuevos_hoy = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE created_at LIKE ?", (f"{today}%",)
            ).fetchone()["n"]
            mensajes_hoy = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE created_at LIKE ? AND role = 'user'",
                (f"{today}%",),
            ).fetchone()["n"]
        return {
            "usuarios": usuarios,
            "premium": premium,
            "activos_hoy": activos_hoy,
            "nuevos_hoy": nuevos_hoy,
            "mensajes_hoy": mensajes_hoy,
        }

    def _was_event_processed_sync(self, event_id: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT 1 FROM processed_stripe_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

    def _mark_event_processed_sync(self, event_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_stripe_events (event_id, processed_at) "
                "VALUES (?, ?)",
                (event_id, utcnow_iso()),
            )
