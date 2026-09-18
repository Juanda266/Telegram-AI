"""Cobros con Wompi (Colombia), la pasarela de Bancolombia.

Es la opción más cómoda para cobrarle a gente en Colombia: acepta Nequi,
PSE, Daviplata, botón Bancolombia, tarjetas y pago en efectivo (Efecty,
Baloto), que es como paga la mayoría, sin necesidad de tarjeta
internacional.

El cobro se hace con el Checkout Web: se arma una URL firmada y el usuario
la abre para pagar. Wompi avisa del resultado con un evento (webhook)
firmado, que se verifica antes de dar el Premium.
"""

import hashlib
import hmac
import logging
from urllib.parse import urlencode

from app.clock import utcnow

logger = logging.getLogger(__name__)

CHECKOUT_URL = "https://checkout.wompi.co/p/"
# Wompi maneja los importes en centavos, sin decimales.
CENTAVOS_POR_UNIDAD = 100
REFERENCIA_PREFIJO = "premium"


class WompiService:
    name = "wompi"
    label = "💳 Nequi, PSE, tarjeta o efectivo"

    def __init__(
        self,
        public_key: str,
        integrity_secret: str,
        events_secret: str,
        amount: int,
        currency: str = "COP",
        redirect_url: str = "",
    ) -> None:
        self.enabled = bool(public_key and integrity_secret and amount > 0)
        self._public_key = public_key
        self._integrity_secret = integrity_secret
        self._events_secret = events_secret
        self._amount_in_cents = amount * CENTAVOS_POR_UNIDAD
        self._currency = currency
        self._redirect_url = redirect_url

    @property
    def webhooks_enabled(self) -> bool:
        return bool(self._events_secret)

    def build_reference(self, telegram_user_id: int) -> str:
        """Referencia única del pago.

        Lleva el ID de Telegram para saber a quién activarle el Premium, y
        una marca de tiempo porque Wompi rechaza referencias repetidas: sin
        ella, un usuario que intentara pagar dos veces vería un error.
        """
        marca = utcnow().strftime("%Y%m%d%H%M%S")
        return f"{REFERENCIA_PREFIJO}-{telegram_user_id}-{marca}"

    @staticmethod
    def parse_reference(reference: str) -> int | None:
        partes = (reference or "").split("-")
        if len(partes) < 2 or partes[0] != REFERENCIA_PREFIJO:
            return None
        try:
            return int(partes[1])
        except ValueError:
            return None

    def _integrity_signature(self, reference: str) -> str:
        """SHA256 de referencia + monto + moneda + secreto de integridad.

        Wompi lo exige para asegurarse de que nadie manipule el importe
        cambiando la URL en el navegador.
        """
        cadena = (
            f"{reference}{self._amount_in_cents}{self._currency}{self._integrity_secret}"
        )
        return hashlib.sha256(cadena.encode()).hexdigest()

    async def create_checkout(self, telegram_user_id: int) -> str:
        referencia = self.build_reference(telegram_user_id)
        parametros = {
            "public-key": self._public_key,
            "currency": self._currency,
            "amount-in-cents": str(self._amount_in_cents),
            "reference": referencia,
            "signature:integrity": self._integrity_signature(referencia),
        }
        if self._redirect_url:
            parametros["redirect-url"] = self._redirect_url

        return f"{CHECKOUT_URL}?{urlencode(parametros)}"

    def verify_event(self, event: dict) -> bool:
        """Comprueba la firma del evento antes de darle crédito.

        Wompi indica en el propio evento qué campos hay que concatenar; a
        eso se le añade la marca de tiempo y el secreto de eventos, y el
        SHA256 debe coincidir con el checksum que envía. Un evento sin firma
        válida se descarta: si no, cualquiera podría regalarse el Premium
        haciendo una petición a nuestro webhook.
        """
        if not self._events_secret:
            return False

        firma = event.get("signature") or {}
        checksum = firma.get("checksum")
        propiedades = firma.get("properties") or []
        timestamp = event.get("timestamp")

        if not checksum or not propiedades or timestamp is None:
            return False

        partes = []
        for ruta in propiedades:
            valor = event.get("data")
            for tramo in ruta.split("."):
                if not isinstance(valor, dict):
                    return False
                valor = valor.get(tramo)
            if valor is None:
                return False
            partes.append(str(valor))

        cadena = "".join(partes) + str(timestamp) + self._events_secret
        calculado = hashlib.sha256(cadena.encode()).hexdigest()

        # compare_digest evita filtrar información por el tiempo de comparación.
        return hmac.compare_digest(calculado, checksum)
