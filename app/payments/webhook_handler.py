"""Traduce eventos de Stripe en cambios sobre la base de datos de usuarios."""

import datetime as dt
import logging

from app.clock import utcnow
from app.payments.paypal import (
    EVENTOS_ACTIVAN,
    EVENTOS_COBRO,
    EVENTOS_DESACTIVAN,
    PayPalService,
)
from app.payments.stripe_client import StripeService
from app.payments.wompi import WompiService
from app.storage.db import Database

logger = logging.getLogger(__name__)

# Eventos de suscripción cuyo status consideramos "premium activo".
_ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}

# Cuánto Premium concede un pago que no es una suscripción gestionada por
# la pasarela (Wompi) o cada cobro de una que sí lo es (PayPal).
DIAS_POR_PAGO = 30


class WebhookHandler:
    def __init__(self, db: Database, stripe_service: StripeService) -> None:
        self._db = db
        self._stripe_service = stripe_service

    async def handle_event(self, event: dict) -> None:
        event_id = event.get("id")
        event_type = event.get("type", "")
        data_object = event.get("data", {}).get("object", {})

        if event_id and await self._db.was_event_processed(event_id):
            logger.info("Evento de Stripe %s ya procesado, se ignora", event_id)
            return

        if event_type == "checkout.session.completed":
            await self._handle_checkout_completed(data_object)
        elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
            await self._handle_subscription_updated(data_object)
        elif event_type == "customer.subscription.deleted":
            await self._handle_subscription_deleted(data_object)
        else:
            logger.debug("Evento de Stripe sin manejar: %s", event_type)

        if event_id:
            await self._db.mark_event_processed(event_id)

    async def handle_wompi_event(self, event: dict) -> None:
        """Wompi cobra pagos únicos, no suscripciones: cada pago aprobado
        concede un periodo de Premium a partir de ahora."""
        transaccion = event.get("data", {}).get("transaction", {})
        estado = transaccion.get("status")
        referencia = transaccion.get("reference", "")

        if estado != "APPROVED":
            logger.info("Pago de Wompi en estado %s, no se activa nada", estado)
            return

        transaccion_id = transaccion.get("id")
        if transaccion_id and await self._db.was_event_processed(transaccion_id):
            logger.info("Transacción de Wompi %s ya procesada", transaccion_id)
            return

        telegram_user_id = WompiService.parse_reference(referencia)
        if telegram_user_id is None:
            logger.warning("Pago de Wompi con referencia irreconocible: %r", referencia)
            return

        hasta = (utcnow() + dt.timedelta(days=DIAS_POR_PAGO)).isoformat()
        await self._db.get_or_create_user(telegram_user_id)
        await self._db.set_premium(telegram_user_id, is_premium=True, premium_until=hasta)
        if transaccion_id:
            await self._db.mark_event_processed(transaccion_id)
        logger.info("Usuario %s activó Premium con Wompi hasta %s", telegram_user_id, hasta)

    async def handle_paypal_event(self, event: dict) -> None:
        event_id = event.get("id")
        event_type = event.get("event_type", "")

        if event_id and await self._db.was_event_processed(event_id):
            logger.info("Evento de PayPal %s ya procesado", event_id)
            return

        if event_type not in EVENTOS_ACTIVAN | EVENTOS_DESACTIVAN | EVENTOS_COBRO:
            logger.debug("Evento de PayPal sin manejar: %s", event_type)
            return

        telegram_user_id = await self._resolver_usuario_paypal(event)
        if telegram_user_id is None:
            logger.warning("Evento de PayPal %s sin usuario identificable", event_type)
            return

        if event_type in EVENTOS_DESACTIVAN:
            await self._db.set_premium(telegram_user_id, is_premium=False, premium_until=None)
            logger.info("Usuario %s canceló su Premium de PayPal", telegram_user_id)
        else:
            # Tanto el alta como cada renovación conceden un periodo nuevo.
            await self._db.get_or_create_user(telegram_user_id)
            hasta = (utcnow() + dt.timedelta(days=DIAS_POR_PAGO)).isoformat()
            await self._db.set_premium(telegram_user_id, is_premium=True, premium_until=hasta)

            # Guardamos el ID de la suscripción al darla de alta: los cobros
            # de renovación no traen el ID de Telegram, solo este.
            suscripcion = PayPalService.extract_subscription_id(event)
            if suscripcion:
                await self._db.set_payment_subscription(telegram_user_id, suscripcion)

            logger.info(
                "Usuario %s tiene Premium con PayPal hasta %s (%s)",
                telegram_user_id,
                hasta,
                event_type,
            )

        if event_id:
            await self._db.mark_event_processed(event_id)

    async def _resolver_usuario_paypal(self, event: dict) -> int | None:
        """Identifica al usuario de un evento de PayPal.

        El alta de la suscripción trae el ID de Telegram en `custom_id`,
        pero los cobros de renovación (PAYMENT.SALE.COMPLETED) no: solo
        traen el ID de la suscripción. Sin esta búsqueda, el Premium de
        quien sigue pagando expiraría a los 30 días.
        """
        telegram_user_id = PayPalService.extract_telegram_user_id(event)
        if telegram_user_id is not None:
            return telegram_user_id

        suscripcion = PayPalService.extract_subscription_id(event)
        if not suscripcion:
            return None

        user = await self._db.find_user_by_subscription_id(suscripcion)
        return user.telegram_user_id if user else None

    async def _handle_checkout_completed(self, session: dict) -> None:
        telegram_user_id = StripeService.extract_telegram_user_id(session)
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

        if telegram_user_id is None:
            logger.warning(
                "checkout.session.completed sin telegram_user_id identificable (customer=%s)",
                customer_id,
            )
            return

        if customer_id:
            await self._db.link_stripe_customer(telegram_user_id, customer_id)

        # El estado premium "real" (con fecha de expiración) llega en el
        # evento customer.subscription.updated, pero activamos ya mismo
        # para que el usuario no espere.
        await self._db.set_premium(
            telegram_user_id,
            is_premium=True,
            premium_until=None,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
        )
        logger.info("Usuario de Telegram %s marcado como premium (checkout)", telegram_user_id)

    async def _resolve_telegram_user_id(self, subscription: dict) -> int | None:
        telegram_user_id = StripeService.extract_telegram_user_id(subscription)
        if telegram_user_id is not None:
            return telegram_user_id

        customer_id = subscription.get("customer")
        if not customer_id:
            return None
        user = await self._db.find_user_by_customer_id(customer_id)
        return user.telegram_user_id if user else None

    async def _handle_subscription_updated(self, subscription: dict) -> None:
        telegram_user_id = await self._resolve_telegram_user_id(subscription)
        if telegram_user_id is None:
            logger.warning(
                "customer.subscription.updated sin telegram_user_id identificable (customer=%s)",
                subscription.get("customer"),
            )
            return

        status = subscription.get("status")
        is_active = status in _ACTIVE_SUBSCRIPTION_STATUSES
        premium_until = StripeService.period_end_to_iso(
            subscription.get("current_period_end")
        )

        await self._db.set_premium(
            telegram_user_id,
            is_premium=is_active,
            premium_until=premium_until,
            stripe_customer_id=subscription.get("customer"),
            stripe_subscription_id=subscription.get("id"),
        )
        logger.info(
            "Suscripción de %s actualizada: status=%s premium_until=%s",
            telegram_user_id,
            status,
            premium_until,
        )

    async def _handle_subscription_deleted(self, subscription: dict) -> None:
        telegram_user_id = await self._resolve_telegram_user_id(subscription)
        if telegram_user_id is None:
            logger.warning(
                "customer.subscription.deleted sin telegram_user_id identificable (customer=%s)",
                subscription.get("customer"),
            )
            return

        await self._db.set_premium(
            telegram_user_id,
            is_premium=False,
            premium_until=None,
            stripe_customer_id=subscription.get("customer"),
            stripe_subscription_id=subscription.get("id"),
        )
        logger.info("Suscripción de %s cancelada, vuelve al plan gratuito", telegram_user_id)
