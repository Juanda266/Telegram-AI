"""Contrato común de los métodos de pago.

Cada proveedor (Telegram Stars, Wompi, PayPal, Stripe) se encarga de cobrar
a su manera, pero todos responden a lo mismo: "dame un enlace para que este
usuario pague". Así, el bot ofrece los métodos que estén configurados sin
saber cómo funciona cada uno, y añadir otro proveedor no obliga a tocar
Telegram ni la lógica de suscripciones.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class PaymentError(RuntimeError):
    """No se pudo generar el cobro con este proveedor."""


@dataclass(frozen=True)
class CheckoutOption:
    """Un método de pago listo para ofrecerle al usuario."""

    provider: str
    label: str
    url: str


@runtime_checkable
class PaymentProvider(Protocol):
    #: Identificador interno, p. ej. "wompi".
    name: str
    #: Texto del botón que ve el usuario, p. ej. "Nequi, PSE o tarjeta".
    label: str
    #: False cuando faltan credenciales: el método simplemente no se ofrece.
    enabled: bool

    async def create_checkout(self, telegram_user_id: int) -> str:
        """Devuelve la URL donde el usuario completa el pago."""


async def collect_checkout_options(
    providers: list[PaymentProvider], telegram_user_id: int
) -> tuple[list[CheckoutOption], list[str]]:
    """Genera los enlaces de todos los proveedores disponibles.

    Si uno falla no se cancela el resto: es mejor ofrecerle al usuario los
    métodos que sí funcionan que dejarlo sin ninguno. Devuelve también los
    nombres de los que fallaron, para poder registrarlos.
    """
    opciones = []
    fallidos = []

    for provider in providers:
        if not provider.enabled:
            continue
        try:
            url = await provider.create_checkout(telegram_user_id)
        except Exception:
            fallidos.append(provider.name)
            continue
        opciones.append(
            CheckoutOption(provider=provider.name, label=provider.label, url=url)
        )

    return opciones, fallidos
