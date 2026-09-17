import datetime as dt

import pytest

from app.billing.service import BillingService
from app.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_sin_facturacion_todo_permitido(db):
    billing = BillingService(db, free_daily_messages=2, billing_enabled=False)

    for _ in range(10):
        result = await billing.check_and_consume(123)
        assert result.allowed


@pytest.mark.asyncio
async def test_cuota_gratuita_se_agota(db):
    billing = BillingService(db, free_daily_messages=3, billing_enabled=True)

    remaining = [(await billing.check_and_consume(1)).remaining_free_messages for _ in range(3)]
    assert remaining == [2, 1, 0]

    blocked = await billing.check_and_consume(1)
    assert not blocked.allowed
    assert not blocked.is_premium


@pytest.mark.asyncio
async def test_usuarios_tienen_cuotas_independientes(db):
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)

    assert (await billing.check_and_consume(1)).allowed
    assert not (await billing.check_and_consume(1)).allowed
    assert (await billing.check_and_consume(2)).allowed


@pytest.mark.asyncio
async def test_premium_activo_ignora_la_cuota(db):
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)
    await db.get_or_create_user(7)
    future = (dt.datetime.utcnow() + dt.timedelta(days=30)).isoformat()
    await db.set_premium(7, is_premium=True, premium_until=future)

    for _ in range(5):
        result = await billing.check_and_consume(7)
        assert result.allowed
        assert result.is_premium


@pytest.mark.asyncio
async def test_premium_expirado_vuelve_al_plan_gratuito(db):
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)
    await db.get_or_create_user(8)
    past = (dt.datetime.utcnow() - dt.timedelta(days=1)).isoformat()
    await db.set_premium(8, is_premium=True, premium_until=past)

    first = await billing.check_and_consume(8)
    assert first.allowed
    assert not first.is_premium
    assert not (await billing.check_and_consume(8)).allowed


@pytest.mark.asyncio
async def test_cuota_se_reinicia_en_un_dia_nuevo(db):
    billing = BillingService(db, free_daily_messages=2, billing_enabled=True)
    ayer = (dt.datetime.utcnow().date() - dt.timedelta(days=1)).isoformat()

    await db.get_or_create_user(9)
    await db.increment_free_usage(9, ayer)
    await db.increment_free_usage(9, ayer)

    # Aunque ayer agotó la cuota, hoy vuelve a tener mensajes.
    result = await billing.check_and_consume(9)
    assert result.allowed
    assert result.remaining_free_messages == 1


@pytest.mark.asyncio
async def test_texto_de_estado(db):
    billing = BillingService(db, free_daily_messages=5, billing_enabled=True)

    texto = await billing.get_status_text(10)
    assert "5/5" in texto

    await billing.check_and_consume(10)
    assert "4/5" in await billing.get_status_text(10)


@pytest.mark.asyncio
async def test_refund_devuelve_el_mensaje_a_la_cuota(db):
    """Si la respuesta falla por un error nuestro, el usuario no debe perder
    un mensaje de su cuota diaria."""
    billing = BillingService(db, free_daily_messages=2, billing_enabled=True)

    await billing.check_and_consume(1)
    await billing.refund(1)

    result = await billing.check_and_consume(1)
    assert result.remaining_free_messages == 1


@pytest.mark.asyncio
async def test_refund_no_deja_el_contador_en_negativo(db):
    billing = BillingService(db, free_daily_messages=2, billing_enabled=True)
    await db.get_or_create_user(1)

    await billing.refund(1)
    await billing.refund(1)

    result = await billing.check_and_consume(1)
    assert result.remaining_free_messages == 1


@pytest.mark.asyncio
async def test_refund_sin_facturacion_no_hace_nada(db):
    billing = BillingService(db, free_daily_messages=2, billing_enabled=False)
    await billing.refund(999)  # no debe fallar aunque el usuario no exista


@pytest.mark.asyncio
async def test_is_premium_detecta_suscripcion_activa(db):
    """Se usa para no ofrecerle pagar de nuevo a quien ya pagó."""
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)
    await db.get_or_create_user(1)
    assert not await billing.is_premium(1)

    futuro = (dt.datetime.now(dt.UTC) + dt.timedelta(days=5)).isoformat()
    await db.set_premium(1, is_premium=True, premium_until=futuro)
    assert await billing.is_premium(1)


@pytest.mark.asyncio
async def test_is_premium_es_falso_si_la_suscripcion_expiro(db):
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)
    await db.get_or_create_user(2)
    pasado = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    await db.set_premium(2, is_premium=True, premium_until=pasado)

    assert not await billing.is_premium(2)
