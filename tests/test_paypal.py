import httpx
import pytest

from app.payments.paypal import API_SANDBOX, PayPalService


def _service(webhook_id="wh-1", sandbox=True):
    return PayPalService(
        client_id="cliente",
        client_secret="secreto",
        plan_id="P-PLAN123",
        webhook_id=webhook_id,
        sandbox=sandbox,
    )


@pytest.fixture
def patch_http(monkeypatch):
    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr("app.payments.paypal.httpx.AsyncClient", factory)

    return _patch


def test_desactivado_si_faltan_credenciales():
    assert not PayPalService("", "", "", "").enabled
    assert _service().enabled


def test_usa_el_entorno_de_pruebas_cuando_se_pide():
    assert _service(sandbox=True)._api == API_SANDBOX
    assert "sandbox" not in _service(sandbox=False)._api


@pytest.mark.asyncio
async def test_devuelve_el_enlace_de_aprobacion(patch_http):
    recibido = {}

    def responder(request):
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-abc"})
        import json

        recibido.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "id": "I-SUB1",
                "links": [
                    {"rel": "self", "href": "https://api/self"},
                    {"rel": "approve", "href": "https://paypal.com/aprobar/123"},
                ],
            },
        )

    patch_http(responder)

    url = await _service().create_checkout(999)

    assert url == "https://paypal.com/aprobar/123"
    assert recibido["plan_id"] == "P-PLAN123"
    # custom_id es lo que permite saber a quién activarle el Premium al
    # recibir el webhook, sin pedirle ningún dato al usuario.
    assert recibido["custom_id"] == "999"


@pytest.mark.asyncio
async def test_error_si_paypal_no_devuelve_enlace(patch_http):
    def responder(request):
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(201, json={"id": "I-SUB1", "links": []})

    patch_http(responder)

    with pytest.raises(RuntimeError, match="enlace de aprobación"):
        await _service().create_checkout(1)


def test_identifica_al_usuario_desde_el_evento():
    evento = {"resource": {"custom_id": "4242"}}
    assert PayPalService.extract_telegram_user_id(evento) == 4242


def test_evento_sin_custom_id():
    assert PayPalService.extract_telegram_user_id({"resource": {}}) is None
    assert PayPalService.extract_telegram_user_id({}) is None


CABECERAS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.paypal.com/cert",
    "paypal-transmission-id": "tid",
    "paypal-transmission-sig": "sig",
    "paypal-transmission-time": "2026-01-01T00:00:00Z",
}


@pytest.mark.asyncio
async def test_acepta_un_webhook_que_paypal_confirma(patch_http):
    def responder(request):
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    patch_http(responder)

    assert await _service().verify_webhook(CABECERAS, {"id": "evt"})


@pytest.mark.asyncio
async def test_rechaza_un_webhook_que_paypal_no_confirma(patch_http):
    """Nuestro webhook es público: sin verificar, cualquiera podría
    regalarse el Premium con una simple petición."""

    def responder(request):
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"verification_status": "FAILURE"})

    patch_http(responder)

    assert not await _service().verify_webhook(CABECERAS, {"id": "evt"})


@pytest.mark.asyncio
async def test_rechaza_si_faltan_las_cabeceras_de_firma(patch_http):
    patch_http(lambda request: httpx.Response(200, json={"access_token": "t"}))

    assert not await _service().verify_webhook({}, {"id": "evt"})


@pytest.mark.asyncio
async def test_si_paypal_no_responde_se_rechaza(patch_http):
    """Ante la duda, no se concede nada."""

    def responder(request):
        raise httpx.ConnectError("sin red", request=request)

    patch_http(responder)

    assert not await _service().verify_webhook(CABECERAS, {"id": "evt"})


@pytest.mark.asyncio
async def test_sin_webhook_id_no_se_verifica_nada():
    assert not _service(webhook_id="").webhooks_enabled
    assert not await _service(webhook_id="").verify_webhook(CABECERAS, {})


def test_id_de_suscripcion_en_el_alta():
    evento = {"resource": {"id": "I-SUB1", "custom_id": "1"}}
    assert PayPalService.extract_subscription_id(evento) == "I-SUB1"


def test_id_de_suscripcion_en_una_renovacion():
    """En los cobros el ID del recurso es el del pago, no el de la
    suscripción: esta viene en billing_agreement_id."""
    evento = {"resource": {"id": "PAY-9", "billing_agreement_id": "I-SUB1"}}
    assert PayPalService.extract_subscription_id(evento) == "I-SUB1"


def test_un_pago_suelto_no_tiene_suscripcion():
    assert PayPalService.extract_subscription_id({"resource": {"id": "PAY-9"}}) is None
    assert PayPalService.extract_subscription_id({}) is None
