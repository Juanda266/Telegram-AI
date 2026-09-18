import asyncio
import contextlib
import logging
import signal

from app.ai.agent import ResearchAgent
from app.ai.model_catalog import FreeModelCatalog
from app.ai.openrouter_client import OpenRouterClient
from app.ai.rate_limiter import RateLimiter
from app.ai.vision import VisionService
from app.assistant import Assistant
from app.billing.service import BillingService
from app.config import configure_logging, load_settings
from app.payments.paypal import PayPalService
from app.payments.stripe_client import StripeService
from app.payments.telegram_stars import TelegramStarsService
from app.payments.webhook_handler import WebhookHandler
from app.payments.webhook_server import build_webhook_app, start_webhook_server
from app.payments.wompi import WompiService
from app.storage.db import Database
from app.storage.memory import ConversationMemory
from app.telegram_bot import build_application

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    logger.info(
        "Iniciando bot con modelos (en orden de prioridad): %s",
        ", ".join(settings.openrouter_models),
    )

    catalog = FreeModelCatalog(api_key=settings.openrouter_api_key)
    # El limitador se comparte entre texto y visión: la cuota de OpenRouter
    # es de la API key, no de cada servicio por separado.
    rate_limiter = RateLimiter(max_calls=settings.openrouter_requests_per_minute)

    client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        models=settings.openrouter_models,
        site_url=settings.openrouter_site_url,
        app_name=settings.openrouter_app_name,
        catalog=catalog,
        rate_limiter=rate_limiter,
    )
    vision = VisionService(
        api_key=settings.openrouter_api_key,
        catalog=catalog,
        headers=client.headers,
        rate_limiter=rate_limiter,
    )
    agent = ResearchAgent(client=client, max_steps=settings.max_agent_steps)
    db = Database(settings.database_path)
    memory = ConversationMemory(db=db, max_messages=settings.max_history_messages)
    stripe_service = StripeService(
        secret_key=settings.billing.stripe_secret_key,
        webhook_secret=settings.billing.stripe_webhook_secret,
        price_id=settings.billing.stripe_price_id,
        success_url=settings.billing.stripe_success_url,
        cancel_url=settings.billing.stripe_cancel_url,
    )
    wompi_service = WompiService(
        public_key=settings.billing.wompi_public_key,
        integrity_secret=settings.billing.wompi_integrity_secret,
        events_secret=settings.billing.wompi_events_secret,
        amount=settings.billing.wompi_amount,
        currency=settings.billing.wompi_currency,
        redirect_url=settings.billing.return_url,
    )
    paypal_service = PayPalService(
        client_id=settings.billing.paypal_client_id,
        client_secret=settings.billing.paypal_client_secret,
        plan_id=settings.billing.paypal_plan_id,
        webhook_id=settings.billing.paypal_webhook_id,
        return_url=settings.billing.return_url,
        cancel_url=settings.billing.return_url,
        sandbox=settings.billing.paypal_sandbox,
    )
    # El orden es el que verá el usuario: primero lo más cómodo en Colombia.
    payment_providers = [wompi_service, paypal_service, stripe_service]

    stars_service = TelegramStarsService(
        price_stars=settings.billing.stars_price,
        as_subscription=settings.billing.stars_as_subscription,
    )
    billing = BillingService(
        db=db,
        free_daily_messages=settings.billing.free_daily_messages,
        billing_enabled=settings.billing.enabled,
    )

    if settings.billing.enabled:
        metodos = ["Telegram Stars"] if stars_service.enabled else []
        metodos += [p.name for p in payment_providers if p.enabled]
        logger.info(
            "Facturación activa: %s mensajes gratis al día, Premium %s. "
            "Métodos de pago: %s",
            settings.billing.free_daily_messages,
            settings.billing.premium_price_label,
            ", ".join(metodos),
        )
    else:
        logger.info("Facturación desactivada: uso ilimitado para todos los usuarios")

    webhook_app = build_webhook_app(
        stripe_service,
        WebhookHandler(db=db, stripe_service=stripe_service),
        wompi_service=wompi_service,
        paypal_service=paypal_service,
    )
    assistant = Assistant(agent=agent, memory=memory, billing=billing, vision=vision)
    application = build_application(
        settings, assistant, billing, stripe_service, db, stars_service, payment_providers
    )

    runner = await start_webhook_server(webhook_app, settings.http_port)

    await application.initialize()
    await application.start()
    # "pre_checkout_query" es imprescindible: si no llega, el bot no puede
    # confirmar los pagos con Telegram Stars y ninguna compra se completa.
    await application.updater.start_polling(
        allowed_updates=["message", "pre_checkout_query"]
    )
    logger.info("Bot listo, escuchando mensajes de Telegram...")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows no soporta add_signal_handler para todas las señales.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        logger.info("Apagando...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
