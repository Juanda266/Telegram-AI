"""Integración con Stripe: crear links de pago y procesar webhooks.

Se usa Stripe Checkout (hosted, no manejamos tarjetas nosotros) en modo
suscripción. El flujo es:

1. El usuario manda /suscribirme en Telegram.
2. Creamos una Checkout Session con `client_reference_id` = su ID de
   Telegram, para poder identificarlo cuando llegue el webhook.
3. Stripe redirige a `success_url` tras el pago.
4. Stripe manda un webhook (`checkout.session.completed`,
   `customer.subscription.updated/deleted`) a nuestro servidor, que
   actualiza el estado premium del usuario en la base de datos.
"""

import logging

import stripe

from app.clock import from_unix

logger = logging.getLogger(__name__)


class StripeNotConfiguredError(RuntimeError):
    pass


class StripeService:
    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> None:
        # Cobrar y recibir webhooks son capacidades independientes: se puede
        # tener el webhook configurado sin precio (o al revés) durante el setup.
        self.enabled = bool(secret_key and price_id)
        self.webhooks_enabled = bool(webhook_secret)
        self._webhook_secret = webhook_secret
        self._price_id = price_id
        self._success_url = success_url
        self._cancel_url = cancel_url
        if secret_key:
            stripe.api_key = secret_key

    def create_checkout_url(self, telegram_user_id: int) -> str:
        if not self.enabled:
            raise StripeNotConfiguredError(
                "Stripe no está configurado (falta STRIPE_SECRET_KEY o STRIPE_PRICE_ID)."
            )

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": self._price_id, "quantity": 1}],
            client_reference_id=str(telegram_user_id),
            success_url=self._success_url,
            cancel_url=self._cancel_url,
            metadata={"telegram_user_id": str(telegram_user_id)},
            subscription_data={"metadata": {"telegram_user_id": str(telegram_user_id)}},
        )
        return session.url

    def construct_event(self, payload: bytes, signature_header: str) -> stripe.Event:
        if not self._webhook_secret:
            raise StripeNotConfiguredError("Falta STRIPE_WEBHOOK_SECRET.")
        return stripe.Webhook.construct_event(payload, signature_header, self._webhook_secret)

    @staticmethod
    def extract_telegram_user_id(obj: dict) -> int | None:
        """Busca el ID de Telegram en los distintos lugares donde Stripe
        puede devolverlo según el tipo de evento."""
        metadata = obj.get("metadata") or {}
        candidate = metadata.get("telegram_user_id") or obj.get("client_reference_id")
        if candidate is None:
            return None
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def period_end_to_iso(period_end_unix: int | None) -> str | None:
        if not period_end_unix:
            return None
        moment = from_unix(period_end_unix)
        return moment.isoformat() if moment else None
