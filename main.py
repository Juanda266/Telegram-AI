import asyncio
import contextlib
import logging
import signal

from app.ai.agent import ResearchAgent
from app.ai.model_catalog import FreeModelCatalog
from app.ai.openrouter_client import OpenRouterClient
from app.billing.service import BillingService
from app.config import configure_logging, load_settings
from app.payments.stripe_client import StripeService
from app.payments.webhook_handler import WebhookHandler
from app.payments.webhook_server import build_webhook_app, start_webhook_server
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

    client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        models=settings.openrouter_models,
        site_url=settings.openrouter_site_url,
        app_name=settings.openrouter_app_name,
        catalog=FreeModelCatalog(api_key=settings.openrouter_api_key),
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
    billing = BillingService(
        db=db,
        free_daily_messages=settings.billing.free_daily_messages,
        billing_enabled=settings.billing.enabled,
    )

    if settings.billing.enabled:
        logger.info(
            "Facturación activa: %s mensajes gratis al día, Premium %s",
            settings.billing.free_daily_messages,
            settings.billing.premium_price_label,
        )
    else:
        logger.info("Facturación desactivada: uso ilimitado para todos los usuarios")

    webhook_app = build_webhook_app(
        stripe_service, WebhookHandler(db=db, stripe_service=stripe_service)
    )
    application = build_application(settings, agent, memory, billing, stripe_service, db)

    runner = await start_webhook_server(webhook_app, settings.http_port)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=["message"])
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
