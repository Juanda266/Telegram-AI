import logging

from app.ai.agent import ResearchAgent
from app.ai.openrouter_client import OpenRouterClient
from app.config import configure_logging, load_settings
from app.storage.memory import ConversationMemory
from app.telegram_bot import build_application

logger = logging.getLogger(__name__)


def main() -> None:
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
    )
    agent = ResearchAgent(client=client, max_steps=settings.max_agent_steps)
    memory = ConversationMemory(max_messages=settings.max_history_messages)

    application = build_application(settings, agent, memory)

    logger.info("Bot listo, escuchando mensajes de Telegram...")
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
