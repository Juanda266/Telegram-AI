"""Los métodos de pago son independientes: se activan los que se
configuren y el resto simplemente no se le ofrece al usuario."""

import pytest

from app.payments.base import collect_checkout_options
from app.payments.paypal import PayPalService
from app.payments.stripe_client import StripeService
from app.payments.wompi import WompiService


def _proveedores(paypal=True, wompi=False, stripe=False):
    return [
        WompiService(
            public_key="pub" if wompi else "",
            integrity_secret="sec" if wompi else "",
            events_secret="ev" if wompi else "",
            amount=20000 if wompi else 0,
        ),
        PayPalService(
            client_id="cli" if paypal else "",
            client_secret="sec" if paypal else "",
            plan_id="P-1" if paypal else "",
            webhook_id="wh" if paypal else "",
        ),
        StripeService(
            secret_key="sk" if stripe else "",
            webhook_secret="wh" if stripe else "",
            price_id="price_1" if stripe else "",
            success_url="",
            cancel_url="",
        ),
    ]


class ProveedorFalso:
    def __init__(self, name, enabled=True, error=None):
        self.name = name
        self.label = f"Pagar con {name}"
        self.enabled = enabled
        self._error = error

    async def create_checkout(self, telegram_user_id):
        if self._error:
            raise self._error
        return f"https://pago/{self.name}/{telegram_user_id}"


@pytest.mark.asyncio
async def test_solo_se_ofrecen_los_metodos_configurados():
    """Con solo PayPal configurado, el usuario ve únicamente PayPal."""
    proveedores = _proveedores(paypal=True, wompi=False, stripe=False)

    habilitados = [p.name for p in proveedores if p.enabled]

    assert habilitados == ["paypal"]


@pytest.mark.asyncio
async def test_se_pueden_combinar_varios_metodos():
    proveedores = _proveedores(paypal=True, wompi=True, stripe=True)

    assert [p.name for p in proveedores if p.enabled] == ["wompi", "paypal", "stripe"]


@pytest.mark.asyncio
async def test_sin_ningun_proveedor_no_hay_opciones():
    opciones, fallidos = await collect_checkout_options([], 1)

    assert opciones == []
    assert fallidos == []


@pytest.mark.asyncio
async def test_los_deshabilitados_se_saltan():
    opciones, _ = await collect_checkout_options(
        [ProveedorFalso("apagado", enabled=False), ProveedorFalso("activo")], 7
    )

    assert [o.provider for o in opciones] == ["activo"]


@pytest.mark.asyncio
async def test_si_un_proveedor_falla_se_ofrecen_los_demas():
    """Mejor darle al usuario los métodos que sí funcionan que dejarlo sin
    ninguno porque uno esté caído."""
    opciones, fallidos = await collect_checkout_options(
        [
            ProveedorFalso("caido", error=RuntimeError("su API no responde")),
            ProveedorFalso("bueno"),
        ],
        7,
    )

    assert [o.provider for o in opciones] == ["bueno"]
    assert fallidos == ["caido"]


@pytest.mark.asyncio
async def test_se_respeta_el_orden_configurado():
    opciones, _ = await collect_checkout_options(
        [ProveedorFalso("primero"), ProveedorFalso("segundo")], 1
    )

    assert [o.provider for o in opciones] == ["primero", "segundo"]
