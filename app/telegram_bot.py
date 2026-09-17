import logging

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.ai.agent import ResearchAgent
from app.config import Settings
from app.storage.memory import ConversationMemory

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 ¡Hola! Soy tu asistente de IA con acceso a la web.\n\n"
    "Puedes preguntarme lo que sea: noticias, precios, datos actuales, "
    "explicaciones, ayuda con tareas, etc. Si necesito información "
    "reciente, la busco en internet antes de responderte.\n\n"
    "Comandos:\n"
    "/nuevo — borra el historial de esta conversación\n"
    "/ayuda — muestra este mensaje"
)


def _is_authorized(settings: Settings, user_id: int) -> bool:
    return not settings.allowed_user_ids or user_id in settings.allowed_user_ids


def build_application(
    settings: Settings, agent: ResearchAgent, memory: ConversationMemory
) -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(WELCOME_MESSAGE)

    async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        memory.clear(update.effective_chat.id)
        await update.message.reply_text("🧹 Historial borrado. Empecemos de nuevo.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None or not message.text:
            return

        user = update.effective_user
        if user is None or not _is_authorized(settings, user.id):
            await message.reply_text("🚫 No tienes autorización para usar este bot.")
            return

        chat_id = update.effective_chat.id
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        history = memory.get(chat_id)
        try:
            reply_text = await agent.run(history, message.text)
        except Exception:
            logger.exception("Error inesperado procesando el mensaje")
            reply_text = (
                "⚠️ Ocurrió un error inesperado procesando tu mensaje. "
                "Intenta de nuevo en unos momentos."
            )
        else:
            memory.append(chat_id, {"role": "user", "content": message.text})
            memory.append(chat_id, {"role": "assistant", "content": reply_text})

        await _reply_safely(message, reply_text)

    application.add_handler(CommandHandler(["start", "ayuda", "help"], start_command))
    application.add_handler(CommandHandler(["nuevo", "reset"], reset_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return application


TELEGRAM_MESSAGE_LIMIT = 4096


async def _reply_safely(message, text: str) -> None:
    """Telegram limita los mensajes a 4096 caracteres; los partimos si hace falta."""
    if not text:
        text = "No tengo una respuesta para eso."

    for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[start : start + TELEGRAM_MESSAGE_LIMIT]
        try:
            await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            # Si el markdown quedó mal formado por el modelo, mandamos texto plano.
            await message.reply_text(chunk)
