import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.payments.wompi import WompiService


def _service(events_secret="eventos_secreto", amount=20000):
    return WompiService(
        public_key="pub_test_123",
        integrity_secret="integridad_secreto",
        events_secret=events_secret,
        amount=amount,
        currency="COP",
        redirect_url="https://t.me/mibot",
    )


def test_desactivado_si_faltan_credenciales():
    assert not WompiService("", "", "", 20000).enabled
    assert not _service(amount=0).enabled
    assert _service().enabled


def test_la_referencia_identifica_al_usuario():
    referencia = _service().build_reference(12345)
    assert WompiService.parse_reference(referencia) == 12345


def test_la_referencia_no_se_repite_entre_pagos(monkeypatch):
    """Wompi rechaza referencias repetidas: sin variarlas, un usuario que
    intentara pagar dos veces vería un error."""
    service = _service()
    primera = service.build_reference(1)

    import datetime as dt

    import app.payments.wompi as modulo

    monkeypatch.setattr(modulo, "utcnow", lambda: dt.datetime(2030, 1, 1, tzinfo=dt.UTC))
    segunda = service.build_reference(1)

    assert primera != segunda


@pytest.mark.parametrize(
    "referencia", ["otracosa-1-x", "premium-abc-x", "", "premium"]
)
def test_referencias_irreconocibles(referencia):
    assert WompiService.parse_reference(referencia) is None


@pytest.mark.asyncio
async def test_el_checkout_lleva_los_datos_del_cobro():
    url = await _service(amount=20000).create_checkout(777)
    params = parse_qs(urlparse(url).query)

    assert params["public-key"] == ["pub_test_123"]
    assert params["currency"] == ["COP"]
    # Wompi trabaja en centavos: 20.000 COP son 2.000.000 de centavos.
    assert params["amount-in-cents"] == ["2000000"]
    assert WompiService.parse_reference(params["reference"][0]) == 777


@pytest.mark.asyncio
async def test_la_firma_de_integridad_es_correcta():
    """Sin firma válida, cualquiera podría cambiar el importe en la URL."""
    service = _service(amount=20000)
    url = await service.create_checkout(1)
    params = parse_qs(urlparse(url).query)

    referencia = params["reference"][0]
    esperada = hashlib.sha256(
        f"{referencia}2000000COPintegridad_secreto".encode()
    ).hexdigest()

    assert params["signature:integrity"] == [esperada]


def _evento_firmado(secreto="eventos_secreto", estado="APPROVED", timestamp=1700000000):
    transaccion = {"id": "tx-123", "status": estado, "amount_in_cents": 2000000}
    propiedades = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    cadena = (
        f"{transaccion['id']}{transaccion['status']}{transaccion['amount_in_cents']}"
        f"{timestamp}{secreto}"
    )
    return {
        "event": "transaction.updated",
        "data": {"transaction": transaccion},
        "timestamp": timestamp,
        "signature": {
            "properties": propiedades,
            "checksum": hashlib.sha256(cadena.encode()).hexdigest(),
        },
    }


def test_acepta_un_evento_con_firma_valida():
    assert _service().verify_event(_evento_firmado())


def test_rechaza_un_evento_con_firma_falsa():
    """Nuestro webhook es público: sin verificar, cualquiera podría
    regalarse el Premium con una simple petición."""
    evento = _evento_firmado()
    evento["signature"]["checksum"] = "a" * 64

    assert not _service().verify_event(evento)


def test_rechaza_si_manipulan_el_importe():
    evento = _evento_firmado()
    evento["data"]["transaction"]["amount_in_cents"] = 100

    assert not _service().verify_event(evento)


def test_rechaza_un_evento_firmado_con_otro_secreto():
    assert not _service().verify_event(_evento_firmado(secreto="secreto_del_atacante"))


@pytest.mark.parametrize(
    "quitar", ["signature", "timestamp"]
)
def test_rechaza_eventos_incompletos(quitar):
    evento = _evento_firmado()
    del evento[quitar]

    assert not _service().verify_event(evento)


def test_sin_secreto_de_eventos_no_se_acepta_nada():
    assert not _service(events_secret="").verify_event(_evento_firmado())
    assert not _service(events_secret="").webhooks_enabled


def test_propiedad_inexistente_no_revienta():
    evento = _evento_firmado()
    evento["signature"]["properties"] = ["transaction.campo_que_no_existe"]

    assert not _service().verify_event(evento)
