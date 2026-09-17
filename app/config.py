import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODELS = (
    "meta-llama/llama-3.3-70b-instruct:free,"
    "google/gemini-2.0-flash-exp:free,"
    "deepseek/deepseek-chat:free,"
    "mistralai/mistral-small-3.1-24b-instruct:free,"
    "qwen/qwen-2.5-72b-instruct:free"
)


def _parse_allowed_ids(raw: str) -> set[int]:
    ids = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk:
            ids.add(int(chunk))
    return ids


def _parse_models(raw: str) -> list[str]:
    models = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    if not models:
        raise RuntimeError("OPENROUTER_MODELS no puede quedar vacío.")
    return models


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openrouter_api_key: str
    openrouter_models: list[str]
    openrouter_site_url: str
    openrouter_app_name: str
    allowed_user_ids: set[int] = field(default_factory=set)
    max_history_messages: int = 20
    max_agent_steps: int = 6
    log_level: str = "INFO"


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Define la variable de entorno "
            "o crea un archivo .env a partir de .env.example."
        )

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not openrouter_key:
        raise RuntimeError(
            "Falta OPENROUTER_API_KEY. Consigue una gratis en "
            "https://openrouter.ai/keys y defínela en tu .env."
        )

    return Settings(
        telegram_bot_token=token,
        openrouter_api_key=openrouter_key,
        openrouter_models=_parse_models(os.environ.get("OPENROUTER_MODELS", DEFAULT_MODELS)),
        openrouter_site_url=os.environ.get(
            "OPENROUTER_SITE_URL", "https://github.com/Juanda266/Telegram-AI"
        ).strip(),
        openrouter_app_name=os.environ.get(
            "OPENROUTER_APP_NAME", "Telegram AI Assistant"
        ).strip(),
        allowed_user_ids=_parse_allowed_ids(os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")),
        max_history_messages=int(os.environ.get("MAX_HISTORY_MESSAGES", "20")),
        max_agent_steps=int(os.environ.get("MAX_AGENT_STEPS", "6")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # Las librerías HTTP son muy verbosas en DEBUG/INFO; las bajamos un nivel.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
