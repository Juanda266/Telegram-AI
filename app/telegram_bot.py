import asyncio
import contextlib
import logging

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from app.ai.agent import ResearchAgent
from app.ai.openrouter_client import AllModelsFailedError
from app.billing.service import BillingService
from app.clock import today_iso
from app.config import Settings
from app.payments.stripe_client import StripeNotConfiguredError, StripeService
from app.payments.telegram_stars import TelegramStarsService
from app.storage.db import Database
from app.storage.memory import ConversationMemory

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096

WELCOME_MESSAGE = (
    "👋 ¡Hola! Soy tu asistente de IA con acceso a la web.\n\n"
    "Puedes preguntarme lo que sea: noticias, precios, datos actuales, "
    "explicaciones, ayuda con tareas, etc. Si necesito información "
    "reciente, la busco en internet antes de responderte.\n\n"
    "Comandos:\n"
    "/nuevo — borra el historial de esta conversación\n"
    "/estado — muestra tu plan y mensajes disponibles\n"
    "/suscribirme — activa el plan Premium (mensajes ilimitados)\n"
    "/ayuda — muestra este mensaje"
)


TYPING_REFRESH_SECONDS = 4


def _is_authorized(settings: Settings, user_id: int) -> bool:
    return not settings.allowed_user_ids or user_id in settings.allowed_user_ids


class ChatLocks:
    """Un lock por chat, para serializar los mensajes de una misma conversación."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def acquire(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock


async def _keep_typing(bot, chat_id: int) -> None:
    """Mantiene el indicador de 'escribiendo...' mientras el agente investiga.

    Telegram lo apaga solo a los ~5 segundos, y una búsqueda web puede tardar
    bastante más; sin esto el usuario cree que el bot se colgó.
    """
    try:
        while True:
            with contextlib.suppress(TelegramError):
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(TYPING_REFRESH_SECONDS)
    except asyncio.CancelledError:
        pass


def build_application(
    settings: Settings,
    agent: ResearchAgent,
    memory: ConversationMemory,
    billing: BillingService,
    stripe_service: StripeService,
    db: Database,
    stars_service: TelegramStarsService,
) -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()
    chat_locks = ChatLocks()

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(WELCOME_MESSAGE)

    async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await memory.clear(update.effective_chat.id)
        await update.message.reply_text("🧹 Historial borrado. Empecemos de nuevo.")

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = await billing.get_status_text(update.effective_user.id)
        await update.message.reply_text(text)

    async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user.id not in settings.admin_user_ids:
            await update.message.reply_text("🚫 Este comando es solo para administradores.")
            return

        stats = await db.get_stats(today_iso())
        await update.message.reply_text(
            "📊 *Estadísticas*\n\n"
            f"Usuarios totales: {stats['usuarios']}\n"
            f"Suscriptores Premium: {stats['premium']}\n"
            f"Nuevos hoy: {stats['nuevos_hoy']}\n"
            f"Activos hoy: {stats['activos_hoy']}\n"
            f"Mensajes hoy: {stats['mensajes_hoy']}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "📎 Por ahora solo entiendo mensajes de texto. "
            "Describe con palabras lo que necesitas y te ayudo."
        )

    async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not settings.billing.enabled or not (
            stripe_service.enabled or stars_service.enabled
        ):
            await update.message.reply_text(
                "Este bot todavía no tiene pagos habilitados: puedes usarlo gratis. 🙂"
            )
            return

        user_id = update.effective_user.id

        # Nunca ofrecer pagar a quien ya pagó: cobrarle dos veces sería un
        # problema serio, no una molestia.
        if await billing.is_premium(user_id):
            await update.message.reply_text(
                "✨ Ya tienes el plan Premium activo, no necesitas pagar de nuevo.\n"
                "Usa /estado para ver hasta cuándo."
            )
            return

        # Telegram Stars es el método preferido: se paga dentro de Telegram,
        # sin salir a un navegador ni necesitar cuenta de comercio.
        if stars_service.enabled:
            await context.bot.send_invoice(
                chat_id=update.effective_chat.id,
                **stars_service.invoice_kwargs(user_id),
            )
            if not stripe_service.enabled:
                return

        try:
            checkout_url = await asyncio.to_thread(
                stripe_service.create_checkout_url, user_id
            )
        except StripeNotConfiguredError:
            await update.message.reply_text(
                "⚠️ Los pagos no están configurados correctamente todavía."
            )
            return
        except Exception:
            logger.exception("No se pudo crear la sesión de pago para %s", user_id)
            await update.message.reply_text(
                "⚠️ No pude generar el link de pago. Intenta de nuevo en unos minutos."
            )
            return

        await update.message.reply_text(
            f"💳 Plan Premium ({settings.billing.premium_price_label}): mensajes "
            "ilimitados y prioridad de respuesta.\n\n"
            f"Paga de forma segura aquí:\n{checkout_url}\n\n"
            "El pago se procesa con Stripe; tu suscripción se activa sola en "
            "cuanto se confirme.",
            disable_web_page_preview=True,
        )

    async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Telegram pregunta si aceptamos el pago justo antes de cobrarlo."""
        query = update.pre_checkout_query
        if TelegramStarsService.parse_payload(query.invoice_payload) is None:
            await query.answer(ok=False, error_message="Este pago ya no es válido.")
            return
        await query.answer(ok=True)

    async def successful_payment_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        payment = update.message.successful_payment
        user_id = TelegramStarsService.parse_payload(payment.invoice_payload)
        if user_id is None:
            logger.warning("Pago recibido con payload desconocido: %s", payment.invoice_payload)
            return

        premium_until = TelegramStarsService.premium_until(payment)
        await db.get_or_create_user(user_id)
        await db.set_premium(user_id, is_premium=True, premium_until=premium_until)
        logger.info("Usuario %s activó Premium con Stars hasta %s", user_id, premium_until)

        await update.message.reply_text(
            "✨ ¡Gracias! Tu plan Premium está activo: ya tienes mensajes "
            f"ilimitados hasta el {premium_until[:10]}.\n\n"
            "Puedes revisarlo cuando quieras con /estado."
        )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None or not message.text:
            return

        user = update.effective_user
        if user is None or not _is_authorized(settings, user.id):
            await message.reply_text("🚫 No tienes autorización para usar este bot.")
            return

        quota = await billing.check_and_consume(user.id)
        if not quota.allowed:
            await message.reply_text(
                "🚦 Se te acabaron los mensajes gratuitos de hoy.\n\n"
                f"Con el plan Premium ({settings.billing.premium_price_label}) tienes "
                "mensajes ilimitados: usa /suscribirme.\n"
                "O vuelve mañana, tu cuota gratuita se renueva cada día."
            )
            return

        chat_id = update.effective_chat.id

        # Sin este lock, dos mensajes seguidos del mismo chat se procesarían
        # en paralelo y ambos leerían el historial antes de que el otro lo
        # actualice, perdiendo contexto y mezclando respuestas.
        async with chat_locks.acquire(chat_id):
            typing = asyncio.create_task(_keep_typing(context.bot, chat_id))
            try:
                history = await memory.get(chat_id)
                reply_text = await agent.run(history, message.text)
            except AllModelsFailedError:
                logger.exception("Ningún modelo de OpenRouter respondió")
                # El fallo es nuestro, así que le devolvemos el mensaje a su cuota.
                await billing.refund(user.id)
                reply_text = (
                    "⚠️ Ahora mismo no pude contactar con ningún modelo de IA "
                    "(seguramente se agotó el cupo gratuito). Este mensaje no "
                    "te cuenta: prueba de nuevo en unos minutos."
                )
            except Exception:
                logger.exception("Error inesperado procesando el mensaje")
                await billing.refund(user.id)
                reply_text = (
                    "⚠️ Ocurrió un error inesperado procesando tu mensaje. "
                    "No te cuenta como consumo; intenta de nuevo en unos momentos."
                )
            else:
                await memory.append(chat_id, {"role": "user", "content": message.text})
                await memory.append(chat_id, {"role": "assistant", "content": reply_text})
            finally:
                typing.cancel()

        await _reply_safely(message, reply_text)

        if not quota.is_premium and quota.remaining_free_messages == 3:
            await message.reply_text(
                "ℹ️ Te quedan 3 mensajes gratuitos hoy. Con /suscribirme los tienes ilimitados."
            )

    application.add_handler(CommandHandler(["start", "ayuda", "help"], start_command))
    application.add_handler(CommandHandler(["nuevo", "reset"], reset_command))
    application.add_handler(CommandHandler(["estado", "status"], status_command))
    application.add_handler(
        CommandHandler(["suscribirme", "premium", "subscribe"], subscribe_command)
    )
    application.add_handler(CommandHandler(["stats", "estadisticas"], stats_command))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL | filters.VIDEO,
            handle_unsupported,
        )
    )

    return application


async def _reply_safely(message, text: str) -> None:
    """Telegram limita los mensajes a 4096 caracteres; los partimos si hace falta."""
    if not text:
        text = "No tengo una respuesta para eso."

    for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[start : start + TELEGRAM_MESSAGE_LIMIT]
        try:
            await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            # Si el markdown quedó mal formado por el modelo, mandamos texto plano.
            await message.reply_text(chunk)
