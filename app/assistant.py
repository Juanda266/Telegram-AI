"""Servicio del asistente, independiente de la plataforma.

Aquí vive todo lo que no depende de Telegram: comprobar la cuota del
usuario, mantener el historial de la conversación, llamar al agente y
decidir qué responder cuando algo falla.

Telegram (y en el futuro web, WhatsApp o Discord) son solo adaptadores:
reciben el mensaje, se lo pasan a este servicio y muestran la respuesta.
Así, añadir una plataforma nueva no obliga a reimplementar las cuotas, el
historial ni el manejo de errores.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.ai.agent import ResearchAgent
from app.ai.openrouter_client import AllModelsFailedError
from app.ai.tools.pdf_reader import PdfExtractionError, extract_text
from app.ai.vision import VisionService
from app.billing.service import BillingService
from app.storage.memory import ConversationMemory

logger = logging.getLogger(__name__)

SIN_CUOTA = "sin_cuota"

# Una consulta con varias búsquedas y lecturas puede tardar, pero pasados
# unos minutos es que algo se quedó colgado.
DEFAULT_TIMEOUT_SECONDS = 180.0

MENSAJE_MODELOS_CAIDOS = (
    "⚠️ Ahora mismo no pude contactar con ningún modelo de IA "
    "(seguramente se agotó el cupo gratuito). Este mensaje no te cuenta: "
    "prueba de nuevo en unos minutos."
)
MENSAJE_ERROR_INESPERADO = (
    "⚠️ Ocurrió un error inesperado procesando tu mensaje. "
    "No te cuenta como consumo; intenta de nuevo en unos momentos."
)
MENSAJE_SIN_VISION = (
    "⚠️ Ahora mismo no pude analizar la imagen (no hay modelos de visión "
    "gratuitos disponibles). Este mensaje no te cuenta; prueba de nuevo en "
    "unos minutos o descríbemela con palabras."
)
MENSAJE_TIMEOUT = (
    "⏱️ La consulta está tardando demasiado y la cancelé para no dejarte "
    "esperando. Este mensaje no te cuenta; prueba a preguntarlo de forma "
    "más concreta."
)
MENSAJE_IMAGEN_NO_SOPORTADA = (
    "📎 Por ahora entiendo texto, imágenes y documentos PDF. "
    "Describe con palabras lo que necesitas y te ayudo."
)


@dataclass(frozen=True)
class AssistantReply:
    """Respuesta lista para mostrar, sea cual sea la plataforma."""

    text: str
    quota_exhausted: bool = False
    remaining_free_messages: int | None = None
    is_premium: bool = False


class _ConversationLocks:
    """Un lock por conversación, para que dos mensajes seguidos del mismo
    chat no se procesen a la vez y se pisen el historial."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def acquire(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock


class Assistant:
    def __init__(
        self,
        agent: ResearchAgent,
        memory: ConversationMemory,
        billing: BillingService,
        vision: VisionService | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._agent = agent
        self._memory = memory
        self._billing = billing
        self._vision = vision
        self._timeout_seconds = timeout_seconds
        self._locks = _ConversationLocks()

    @property
    def supports_images(self) -> bool:
        return self._vision is not None

    async def handle_text(
        self, user_id: int, conversation_id: int, text: str
    ) -> AssistantReply:
        quota = await self._billing.check_and_consume(user_id)
        if not quota.allowed:
            return AssistantReply(text=SIN_CUOTA, quota_exhausted=True)

        async def producir() -> str:
            history = await self._memory.get(conversation_id)
            return await self._agent.run(history, text)

        return await self._ejecutar(user_id, conversation_id, text, producir, quota)

    async def handle_image(
        self,
        user_id: int,
        conversation_id: int,
        image_bytes: bytes,
        caption: str | None = None,
    ) -> AssistantReply:
        if self._vision is None:
            return AssistantReply(text=MENSAJE_IMAGEN_NO_SOPORTADA)

        quota = await self._billing.check_and_consume(user_id)
        if not quota.allowed:
            return AssistantReply(text=SIN_CUOTA, quota_exhausted=True)

        pregunta = caption or "¿Qué hay en esta imagen?"

        async def producir() -> str:
            descripcion = await self._vision.describe(image_bytes)
            contexto = (
                "El usuario envió una imagen. Esto es lo que se ve en ella:\n"
                f"{descripcion}\n\nSu mensaje sobre la imagen: {pregunta}"
            )
            history = await self._memory.get(conversation_id)
            return await self._agent.run(history, contexto)

        return await self._ejecutar(
            user_id,
            conversation_id,
            pregunta,
            producir,
            quota,
            mensaje_modelos_caidos=MENSAJE_SIN_VISION,
        )

    async def handle_pdf(
        self,
        user_id: int,
        conversation_id: int,
        pdf_bytes: bytes,
        filename: str = "documento.pdf",
        caption: str | None = None,
    ) -> AssistantReply:
        quota = await self._billing.check_and_consume(user_id)
        if not quota.allowed:
            return AssistantReply(text=SIN_CUOTA, quota_exhausted=True)

        try:
            texto = await asyncio.to_thread(extract_text, pdf_bytes)
        except PdfExtractionError as exc:
            # No es un fallo nuestro ni del usuario: le explicamos qué pasó y
            # no le gastamos la cuota.
            await self._billing.refund(user_id)
            return AssistantReply(text=f"📄 {exc}")

        pregunta = caption or "Resume este documento y dime lo más importante."

        async def producir() -> str:
            contexto = (
                f"El usuario envió el documento PDF «{filename}». "
                f"Este es su contenido:\n{texto}\n\n"
                f"Su mensaje sobre el documento: {pregunta}"
            )
            history = await self._memory.get(conversation_id)
            return await self._agent.run(history, contexto)

        return await self._ejecutar(user_id, conversation_id, pregunta, producir, quota)

    async def _ejecutar(
        self,
        user_id: int,
        conversation_id: int,
        texto_usuario: str,
        producir,
        quota,
        mensaje_modelos_caidos: str = MENSAJE_MODELOS_CAIDOS,
    ) -> AssistantReply:
        async with self._locks.acquire(str(conversation_id)):
            try:
                # Sin tope, una petición colgada dejaría el lock tomado para
                # siempre y la conversación entera quedaría muerta.
                async with asyncio.timeout(self._timeout_seconds):
                    respuesta = await producir()
            except TimeoutError:
                logger.warning("La respuesta tardó más de %ss", self._timeout_seconds)
                await self._billing.refund(user_id)
                return AssistantReply(text=MENSAJE_TIMEOUT)
            except AllModelsFailedError:
                logger.exception("Ningún modelo pudo responder")
                # El fallo es nuestro: no le gastamos la cuota al usuario.
                await self._billing.refund(user_id)
                return AssistantReply(text=mensaje_modelos_caidos)
            except Exception:
                logger.exception("Error inesperado generando la respuesta")
                await self._billing.refund(user_id)
                return AssistantReply(text=MENSAJE_ERROR_INESPERADO)

            await self._memory.append(
                conversation_id, {"role": "user", "content": texto_usuario}
            )
            await self._memory.append(
                conversation_id, {"role": "assistant", "content": respuesta}
            )

        return AssistantReply(
            text=respuesta,
            remaining_free_messages=quota.remaining_free_messages,
            is_premium=quota.is_premium,
        )

    async def clear_conversation(self, conversation_id: int) -> None:
        await self._memory.clear(conversation_id)
