"""Cobros con PayPal (suscripciones recurrentes).

Útil para cobrarle a gente de fuera de Colombia, o a quien ya tiene saldo
en PayPal. Ojo con las comisiones: recibir dinero en Colombia por PayPal
cuesta bastante más que Wompi (comisión de recepción, margen de cambio a
pesos y comisión de retiro), así que conviene ofrecerlo como alternativa,
no como método principal.

Flujo: se crea una suscripción contra el plan configurado, con el ID de
Telegram en `custom_id`, y se le da al usuario el enlace de aprobación.
Cuando la suscripción se activa o se cancela, PayPal avisa por webhook.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

API_LIVE = "https://api-m.paypal.com"
API_SANDBOX = "https://api-m.sandbox.paypal.com"
TIMEOUT_SECONDS = 30.0

EVENTOS_ACTIVAN = {
    "BILLING.SUBSCRIPTION.ACTIVATED",
    "BILLING.SUBSCRIPTION.RE-ACTIVATED",
}
EVENTOS_DESACTIVAN = {
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.EXPIRED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
}


class PayPalService:
    name = "paypal"
    label = "🅿️ PayPal"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        plan_id: str,
        webhook_id: str,
        return_url: str = "",
        cancel_url: str = "",
        sandbox: bool = False,
    ) -> None:
        self.enabled = bool(client_id and client_secret and plan_id)
        self._client_id = client_id
        self._client_secret = client_secret
        self._plan_id = plan_id
        self._webhook_id = webhook_id
        self._return_url = return_url
        self._cancel_url = cancel_url
        self._api = API_SANDBOX if sandbox else API_LIVE

    @property
    def webhooks_enabled(self) -> bool:
        return bool(self._webhook_id and self._client_id and self._client_secret)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self._api}/v1/oauth2/token",
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()["access_token"]

    async def create_checkout(self, telegram_user_id: int) -> str:
        payload = {
            "plan_id": self._plan_id,
            # custom_id viaja de vuelta en el webhook: así sabemos a quién
            # activarle el Premium sin pedirle ningún dato al usuario.
            "custom_id": str(telegram_user_id),
            "application_context": {
                "brand_name": "Asistente IA",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": self._return_url or "https://t.me",
                "cancel_url": self._cancel_url or "https://t.me",
            },
        }

        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            token = await self._access_token(client)
            response = await client.post(
                f"{self._api}/v1/billing/subscriptions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            datos = response.json()

        for enlace in datos.get("links", []):
            if enlace.get("rel") == "approve":
                return enlace["href"]

        raise RuntimeError("PayPal no devolvió el enlace de aprobación")

    async def verify_webhook(self, headers: dict, body: dict) -> bool:
        """Pregunta a PayPal si el evento es auténtico.

        A diferencia de Stripe o Wompi, PayPal no permite validar la firma
        en local: hay que consultarle. Un evento que no se pueda verificar se
        descarta, porque si no cualquiera podría regalarse el Premium
        llamando a nuestro webhook.
        """
        if not self.webhooks_enabled:
            return False

        payload = {
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "webhook_id": self._webhook_id,
            "webhook_event": body,
        }
        if not all(payload[campo] for campo in list(payload)[:5]):
            logger.warning("Webhook de PayPal sin las cabeceras de firma")
            return False

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                token = await self._access_token(client)
                response = await client.post(
                    f"{self._api}/v1/notifications/verify-webhook-signature",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json().get("verification_status") == "SUCCESS"
        except Exception:
            logger.exception("No se pudo verificar el webhook de PayPal")
            return False

    @staticmethod
    def extract_telegram_user_id(event: dict) -> int | None:
        recurso = event.get("resource") or {}
        candidato = recurso.get("custom_id") or recurso.get("custom")
        try:
            return int(candidato)
        except (TypeError, ValueError):
            return None
