"""Servidor HTTP que recibe los avisos de pago de las pasarelas.

Corre en el mismo proceso que el bot (mismo event loop), en el puerto
configurado, con una ruta por proveedor. También expone `/health`, útil
para plataformas de hosting como Render o Railway que hacen health checks
periódicos.

Regla común a todas las rutas: un evento cuya firma no se pueda verificar
se rechaza sin procesarlo. Si no, cualquiera podría regalarse el Premium
haciendo una petición a estas direcciones, que son públicas.
"""

import logging

from aiohttp import web

from app.payments.paypal import PayPalService
from app.payments.stripe_client import StripeService
from app.payments.webhook_handler import WebhookHandler
from app.payments.wompi import WompiService

logger = logging.getLogger(__name__)


def build_webhook_app(
    stripe_service: StripeService,
    handler: WebhookHandler,
    wompi_service: WompiService | None = None,
    paypal_service: PayPalService | None = None,
) -> web.Application:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def stripe_webhook(request: web.Request) -> web.Response:
        if not stripe_service.webhooks_enabled:
            return web.json_response({"error": "webhooks de stripe no configurados"}, status=503)

        payload = await request.read()
        signature = request.headers.get("Stripe-Signature", "")

        try:
            event = stripe_service.construct_event(payload, signature)
        except Exception as exc:
            logger.warning("Webhook de Stripe rechazado: %s", exc)
            return web.json_response({"error": "firma inválida"}, status=400)

        return await _procesar(handler.handle_event, dict(event), "Stripe")

    async def wompi_webhook(request: web.Request) -> web.Response:
        if wompi_service is None or not wompi_service.webhooks_enabled:
            return web.json_response({"error": "webhooks de wompi no configurados"}, status=503)

        try:
            event = await request.json()
        except Exception:
            return web.json_response({"error": "cuerpo inválido"}, status=400)

        if not wompi_service.verify_event(event):
            logger.warning("Webhook de Wompi rechazado: firma inválida")
            return web.json_response({"error": "firma inválida"}, status=400)

        return await _procesar(handler.handle_wompi_event, event, "Wompi")

    async def paypal_webhook(request: web.Request) -> web.Response:
        if paypal_service is None or not paypal_service.webhooks_enabled:
            return web.json_response({"error": "webhooks de paypal no configurados"}, status=503)

        try:
            event = await request.json()
        except Exception:
            return web.json_response({"error": "cuerpo inválido"}, status=400)

        cabeceras = {k.lower(): v for k, v in request.headers.items()}
        if not await paypal_service.verify_webhook(cabeceras, event):
            logger.warning("Webhook de PayPal rechazado: no se pudo verificar")
            return web.json_response({"error": "firma inválida"}, status=400)

        return await _procesar(handler.handle_paypal_event, event, "PayPal")

    async def _procesar(funcion, event: dict, proveedor: str) -> web.Response:
        try:
            await funcion(event)
        except Exception:
            logger.exception("Error procesando el webhook de %s", proveedor)
            # Un 500 hace que la pasarela reintente el envío más tarde.
            return web.json_response({"error": "error interno"}, status=500)
        return web.json_response({"received": True})

    app.router.add_get("/health", health)
    app.router.add_post("/stripe/webhook", stripe_webhook)
    app.router.add_post("/wompi/webhook", wompi_webhook)
    app.router.add_post("/paypal/webhook", paypal_webhook)
    return app


async def start_webhook_server(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Servidor de webhooks escuchando en el puerto %s", port)
    return runner
