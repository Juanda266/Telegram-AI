import datetime as dt

import pytest

from app.billing.service import BillingService
from app.clock import from_unix, parse_utc, today_iso, utcnow
from app.storage.db import Database


def test_utcnow_tiene_zona_horaria():
    assert utcnow().tzinfo is not None


def test_parse_utc_acepta_fechas_con_zona():
    parsed = parse_utc("2026-01-01T12:00:00+00:00")
    assert parsed == dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def test_parse_utc_asume_utc_en_fechas_sin_zona():
    """Las versiones anteriores guardaban fechas sin zona; deben seguir
    funcionando y no romper las comparaciones."""
    parsed = parse_utc("2026-01-01T12:00:00")
    assert parsed == dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("valor", [None, "", "no es una fecha"])
def test_parse_utc_devuelve_none_en_valores_invalidos(valor):
    assert parse_utc(valor) is None


def test_from_unix():
    assert from_unix(0) == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    assert from_unix(None) is None


def test_today_iso_tiene_formato_fecha():
    assert len(today_iso()) == 10


@pytest.mark.asyncio
async def test_premium_guardado_sin_zona_horaria_sigue_siendo_valido(tmp_path):
    """Regresión: comparar una fecha naive con una aware lanzaba TypeError."""
    db = Database(tmp_path / "test.db")
    billing = BillingService(db, free_daily_messages=1, billing_enabled=True)
    await db.get_or_create_user(1)

    futuro_sin_zona = (dt.datetime.now() + dt.timedelta(days=10)).replace(tzinfo=None)
    await db.set_premium(1, is_premium=True, premium_until=futuro_sin_zona.isoformat())

    result = await billing.check_and_consume(1)
    assert result.allowed
    assert result.is_premium
