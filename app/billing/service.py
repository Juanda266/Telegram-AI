"""Lógica de negocio de facturación: cuotas gratis y estado premium.

Se mantiene separada de Telegram y de Stripe a propósito: los handlers de
Telegram solo preguntan "¿puede este usuario mandar un mensaje más?" y los
webhooks de Stripe solo avisan "este usuario pagó / canceló". Toda la
decisión vive aquí, en un solo lugar.
"""

import datetime as dt
from dataclasses import dataclass

from app.storage.db import Database, UserRecord


def _today_utc() -> str:
    return dt.datetime.utcnow().date().isoformat()


def _is_premium_active(user: UserRecord) -> bool:
    if not user.is_premium:
        return False
    if user.premium_until is None:
        # Premium sin fecha de expiración registrada (ej. otorgado a mano).
        return True
    try:
        until = dt.datetime.fromisoformat(user.premium_until)
    except ValueError:
        return True
    return dt.datetime.utcnow() <= until


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    is_premium: bool
    remaining_free_messages: int | None = None


class BillingService:
    def __init__(self, db: Database, free_daily_messages: int, billing_enabled: bool) -> None:
        self._db = db
        self._free_daily_messages = free_daily_messages
        self._billing_enabled = billing_enabled

    async def check_and_consume(self, telegram_user_id: int) -> QuotaResult:
        """Verifica si el usuario puede enviar un mensaje más y, si puede
        (y no es premium), consume una unidad de su cuota gratuita diaria.
        """
        if not self._billing_enabled:
            return QuotaResult(allowed=True, is_premium=True)

        user = await self._db.get_or_create_user(telegram_user_id)

        if _is_premium_active(user):
            return QuotaResult(allowed=True, is_premium=True)

        today = _today_utc()
        used_today = user.free_used_today if user.free_used_date == today else 0

        if used_today >= self._free_daily_messages:
            return QuotaResult(allowed=False, is_premium=False, remaining_free_messages=0)

        new_used = await self._db.increment_free_usage(telegram_user_id, today)
        remaining = max(self._free_daily_messages - new_used, 0)
        return QuotaResult(allowed=True, is_premium=False, remaining_free_messages=remaining)

    async def get_status_text(self, telegram_user_id: int) -> str:
        if not self._billing_enabled:
            return "💚 Este bot no tiene límites de uso configurados."

        user = await self._db.get_or_create_user(telegram_user_id)
        if _is_premium_active(user):
            if user.premium_until:
                return f"✨ Tienes suscripción Premium activa hasta {user.premium_until[:10]}."
            return "✨ Tienes suscripción Premium activa (sin fecha de expiración)."

        today = _today_utc()
        used_today = user.free_used_today if user.free_used_date == today else 0
        remaining = max(self._free_daily_messages - used_today, 0)
        return (
            f"🆓 Plan gratuito: {remaining}/{self._free_daily_messages} "
            "mensajes disponibles hoy.\nUsa /suscribirme para tener mensajes "
            "ilimitados."
        )
