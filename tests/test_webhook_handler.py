import datetime as dt

import pytest

from app.payments.stripe_client import StripeService
from app.payments.webhook_handler import WebhookHandler
from app.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def handler(db):
    stripe_service = StripeService(
        secret_key="", webhook_secret="", price_id="", success_url="", cancel_url=""
    )
    return WebhookHandler(db=db, stripe_service=stripe_service)


def _checkout_event(telegram_user_id: int, event_id: str = "evt_1") -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(telegram_user_id),
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"telegram_user_id": str(telegram_user_id)},
            }
        },
    }


@pytest.mark.asyncio
async def test_checkout_completado_activa_premium(db, handler):
    await db.get_or_create_user(55)

    await handler.handle_event(_checkout_event(55))

    user = await db.get_or_create_user(55)
    assert user.is_premium
    assert user.stripe_customer_id == "cus_123"


@pytest.mark.asyncio
async def test_evento_duplicado_se_ignora(db, handler):
    await db.get_or_create_user(56)
    await handler.handle_event(_checkout_event(56))

    # Cancelamos a mano y reenviamos el MISMO evento: no debe reactivarse.
    await db.set_premium(56, is_premium=False, premium_until=None)
    await handler.handle_event(_checkout_event(56))

    assert not (await db.get_or_create_user(56)).is_premium


@pytest.mark.asyncio
async def test_suscripcion_actualizada_guarda_fecha_de_fin(db, handler):
    await db.get_or_create_user(57)
    period_end = int((dt.datetime.utcnow() + dt.timedelta(days=30)).timestamp())

    await handler.handle_event(
        {
            "id": "evt_2",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_9",
                    "customer": "cus_9",
                    "status": "active",
                    "current_period_end": period_end,
                    "metadata": {"telegram_user_id": "57"},
                }
            },
        }
    )

    user = await db.get_or_create_user(57)
    assert user.is_premium
    assert user.premium_until is not None


@pytest.mark.asyncio
async def test_suscripcion_cancelada_quita_premium(db, handler):
    await db.get_or_create_user(58)
    await handler.handle_event(_checkout_event(58, event_id="evt_3"))
    assert (await db.get_or_create_user(58)).is_premium

    await handler.handle_event(
        {
            "id": "evt_4",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_123",
                    "customer": "cus_123",
                    "status": "canceled",
                    "metadata": {"telegram_user_id": "58"},
                }
            },
        }
    )

    assert not (await db.get_or_create_user(58)).is_premium


@pytest.mark.asyncio
async def test_suscripcion_se_resuelve_por_customer_id_sin_metadata(db, handler):
    """Stripe no siempre propaga la metadata: debemos poder identificar al
    usuario por el customer_id guardado durante el checkout."""
    await db.get_or_create_user(59)
    await db.link_stripe_customer(59, "cus_sin_meta")

    await handler.handle_event(
        {
            "id": "evt_5",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_x",
                    "customer": "cus_sin_meta",
                    "status": "active",
                    "current_period_end": None,
                    "metadata": {},
                }
            },
        }
    )

    assert (await db.get_or_create_user(59)).is_premium


@pytest.mark.asyncio
async def test_status_impagado_desactiva_premium(db, handler):
    await db.get_or_create_user(60)
    await db.link_stripe_customer(60, "cus_60")
    await db.set_premium(60, is_premium=True, premium_until=None)

    await handler.handle_event(
        {
            "id": "evt_6",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_60",
                    "customer": "cus_60",
                    "status": "past_due",
                    "metadata": {},
                }
            },
        }
    )

    assert not (await db.get_or_create_user(60)).is_premium
