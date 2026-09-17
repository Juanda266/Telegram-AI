import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un número entero, se recibió {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "si", "sí", "on")


@dataclass(frozen=True)
class BillingSettings:
    enabled: bool
    free_daily_messages: int
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    stripe_success_url: str
    stripe_cancel_url: str
    premium_price_label: str


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openrouter_api_key: str
    openrouter_models: list[str]
    openrouter_site_url: str
    openrouter_app_name: str
    billing: BillingSettings
    database_path: Path
    http_port: int
    allowed_user_ids: set[int] = field(default_factory=set)
    max_history_messages: int = 20
    max_agent_steps: int = 6
    log_level: str = "INFO"


def load_settings() -> Settings:
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Define la variable de entorno "
            "o crea un archivo .env a partir de .env.example."
        )

    openrouter_key = _env("OPENROUTER_API_KEY")
    if not openrouter_key:
        raise RuntimeError(
            "Falta OPENROUTER_API_KEY. Consigue una gratis en "
            "https://openrouter.ai/keys y defínela en tu .env."
        )

    billing = BillingSettings(
        enabled=_env_bool("BILLING_ENABLED", False),
        free_daily_messages=_env_int("FREE_DAILY_MESSAGES", 15),
        stripe_secret_key=_env("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=_env("STRIPE_WEBHOOK_SECRET"),
        stripe_price_id=_env("STRIPE_PRICE_ID"),
        stripe_success_url=_env("STRIPE_SUCCESS_URL", "https://t.me"),
        stripe_cancel_url=_env("STRIPE_CANCEL_URL", "https://t.me"),
        premium_price_label=_env("PREMIUM_PRICE_LABEL", "5 USD/mes"),
    )

    if billing.enabled:
        faltantes = [
            nombre
            for nombre, valor in (
                ("STRIPE_SECRET_KEY", billing.stripe_secret_key),
                ("STRIPE_PRICE_ID", billing.stripe_price_id),
                ("STRIPE_WEBHOOK_SECRET", billing.stripe_webhook_secret),
            )
            if not valor
        ]
        if faltantes:
            raise RuntimeError(
                f"BILLING_ENABLED=true pero falta configurar: {', '.join(faltantes)}. "
                "Complétalas en tu .env o pon BILLING_ENABLED=false."
            )
        if billing.free_daily_messages < 0:
            raise RuntimeError("FREE_DAILY_MESSAGES no puede ser negativo.")

    return Settings(
        telegram_bot_token=token,
        openrouter_api_key=openrouter_key,
        openrouter_models=_parse_models(_env("OPENROUTER_MODELS", DEFAULT_MODELS)),
        openrouter_site_url=_env(
            "OPENROUTER_SITE_URL", "https://github.com/Juanda266/Telegram-AI"
        ),
        openrouter_app_name=_env("OPENROUTER_APP_NAME", "Telegram AI Assistant"),
        billing=billing,
        database_path=Path(_env("DATABASE_PATH", "data/bot.db")),
        http_port=_env_int("PORT", 8080),
        allowed_user_ids=_parse_allowed_ids(_env("ALLOWED_TELEGRAM_USER_IDS")),
        max_history_messages=_env_int("MAX_HISTORY_MESSAGES", 20),
        max_agent_steps=_env_int("MAX_AGENT_STEPS", 6),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # Las librerías HTTP son muy verbosas en DEBUG/INFO; las bajamos un nivel.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
