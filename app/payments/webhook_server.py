"""Servidor HTTP mínimo para recibir los webhooks de Stripe.

Corre en el mismo proceso que el bot (mismo event loop), en el puerto
configurado. También expone `/health`, útil para plataformas de hosting
como Render o Railway que hacen health checks periódicos.
"""

import logging

from aiohttp import web

from app.payments.stripe_client import StripeService
from app.payments.webhook_handler import WebhookHandler

logger = logging.getLogger(__name__)


def build_webhook_app(
    stripe_service: StripeService, handler: WebhookHandler
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
            # Firma inválida o payload manipulado: nunca procesamos el evento.
            logger.warning("Webhook de Stripe rechazado: %s", exc)
            return web.json_response({"error": "firma inválida"}, status=400)

        try:
            await handler.handle_event(dict(event))
        except Exception:
            logger.exception("Error procesando el webhook de Stripe")
            # Devolvemos 500 para que Stripe reintente el envío.
            return web.json_response({"error": "error interno"}, status=500)

        return web.json_response({"received": True})

    app.router.add_get("/health", health)
    app.router.add_post("/stripe/webhook", stripe_webhook)
    return app


async def start_webhook_server(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Servidor de webhooks escuchando en el puerto %s", port)
    return runner
