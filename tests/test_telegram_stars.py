import datetime as dt

from app.clock import parse_utc, utcnow
from app.payments.telegram_stars import (
    SUBSCRIPTION_PERIOD_SECONDS,
    TelegramStarsService,
)


class FakePayment:
    def __init__(self, invoice_payload="premium:7", subscription_expiration_date=None):
        self.invoice_payload = invoice_payload
        self.subscription_expiration_date = subscription_expiration_date


def test_desactivado_si_el_precio_es_cero():
    assert not TelegramStarsService(price_stars=0).enabled
    assert TelegramStarsService(price_stars=150).enabled


def test_payload_identifica_al_usuario():
    service = TelegramStarsService(price_stars=150)
    payload = service.build_payload(12345)
    assert TelegramStarsService.parse_payload(payload) == 12345


def test_payload_invalido_devuelve_none():
    assert TelegramStarsService.parse_payload("otra-cosa:1") is None
    assert TelegramStarsService.parse_payload("premium:no-es-numero") is None
    assert TelegramStarsService.parse_payload("") is None


def test_factura_usa_la_moneda_de_stars():
    kwargs = TelegramStarsService(price_stars=150).invoice_kwargs(1)
    assert kwargs["currency"] == "XTR"
    # Las facturas en Stars no llevan proveedor externo.
    assert kwargs["provider_token"] == ""
    assert len(kwargs["prices"]) == 1
    assert kwargs["prices"][0].amount == 150


def test_factura_de_suscripcion_incluye_el_periodo():
    kwargs = TelegramStarsService(price_stars=150, as_subscription=True).invoice_kwargs(1)
    assert kwargs["subscription_period"] == SUBSCRIPTION_PERIOD_SECONDS


def test_factura_de_pago_unico_no_incluye_periodo():
    kwargs = TelegramStarsService(price_stars=150, as_subscription=False).invoice_kwargs(1)
    assert "subscription_period" not in kwargs


def test_premium_usa_la_fecha_que_informa_telegram():
    expiracion = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    resultado = TelegramStarsService.premium_until(
        FakePayment(subscription_expiration_date=expiracion)
    )
    assert parse_utc(resultado) == expiracion


def test_premium_acepta_fecha_como_timestamp_unix():
    resultado = TelegramStarsService.premium_until(
        FakePayment(subscription_expiration_date=1893456000)
    )
    assert parse_utc(resultado).year == 2030


def test_premium_por_defecto_dura_30_dias():
    resultado = parse_utc(TelegramStarsService.premium_until(FakePayment()))
    esperado = utcnow() + dt.timedelta(seconds=SUBSCRIPTION_PERIOD_SECONDS)
    assert abs((resultado - esperado).total_seconds()) < 5
