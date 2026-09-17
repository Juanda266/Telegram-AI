"""Pagos con Telegram Stars (moneda XTR).

Es la forma más accesible de cobrar: no hace falta cuenta de comercio,
entidad empresarial ni que Stripe opere en tu país (Stripe, por ejemplo,
no admite cobros desde Colombia). El usuario paga con Stars dentro de la
propia app de Telegram y el bot recibe el aviso.

Flujo de Telegram para bienes digitales:
    send_invoice -> pre_checkout_query -> answer_pre_checkout_query
    -> successful_payment -> entregamos el Premium

Si se configura como suscripción, Telegram renueva el cobro solo y envía un
nuevo `successful_payment` en cada renovación, que simplemente extiende la
fecha de expiración.
"""

import datetime as dt
import logging

from telegram import LabeledPrice

from app.clock import from_unix, utcnow

logger = logging.getLogger(__name__)

CURRENCY = "XTR"
PAYLOAD_PREFIX = "premium"
# Telegram exige exactamente 30 días para las suscripciones con Stars.
SUBSCRIPTION_PERIOD_SECONDS = 2592000


class TelegramStarsService:
    def __init__(self, price_stars: int, as_subscription: bool = True) -> None:
        self.enabled = price_stars > 0
        self._price_stars = price_stars
        self._as_subscription = as_subscription

    @property
    def price_stars(self) -> int:
        return self._price_stars

    def build_payload(self, telegram_user_id: int) -> str:
        return f"{PAYLOAD_PREFIX}:{telegram_user_id}"

    @staticmethod
    def parse_payload(payload: str) -> int | None:
        if not payload.startswith(f"{PAYLOAD_PREFIX}:"):
            return None
        try:
            return int(payload.split(":", 1)[1])
        except (IndexError, ValueError):
            return None

    def invoice_kwargs(self, telegram_user_id: int) -> dict:
        """Parámetros para bot.send_invoice()."""
        kwargs = {
            "title": "Plan Premium",
            "description": (
                "Mensajes ilimitados con el asistente de IA, incluyendo "
                "búsqueda en la web. Se renueva cada 30 días."
                if self._as_subscription
                else "Mensajes ilimitados durante 30 días."
            ),
            "payload": self.build_payload(telegram_user_id),
            # Las facturas en Stars no usan proveedor externo.
            "provider_token": "",
            "currency": CURRENCY,
            "prices": [LabeledPrice("Plan Premium", self._price_stars)],
        }
        if self._as_subscription:
            kwargs["subscription_period"] = SUBSCRIPTION_PERIOD_SECONDS
        return kwargs

    @staticmethod
    def premium_until(successful_payment) -> str:
        """Hasta cuándo dura el Premium tras un pago.

        Si Telegram informa la fecha de expiración de la suscripción se usa
        esa; si no (pago único), se cuentan 30 días desde ahora.
        """
        expiration = getattr(successful_payment, "subscription_expiration_date", None)
        if isinstance(expiration, dt.datetime):
            return expiration.isoformat()

        parsed = from_unix(expiration) if isinstance(expiration, int) else None
        if parsed is not None:
            return parsed.isoformat()

        return (utcnow() + dt.timedelta(seconds=SUBSCRIPTION_PERIOD_SECONDS)).isoformat()
