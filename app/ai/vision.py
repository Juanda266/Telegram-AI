"""Comprensión de imágenes enviadas por el usuario.

Los modelos con visión reciben la imagen en el propio mensaje, codificada
como data URL. Aquí se resuelve qué modelo usar (los que el catálogo marca
como gratuitos y capaces de leer imágenes) y se obtiene una descripción,
que luego se le pasa al agente como contexto para que pueda razonar,
buscar en la web o responder sobre lo que aparece en la foto.
"""

import asyncio
import base64
import logging

import httpx

from app.ai.model_catalog import FreeModelCatalog
from app.ai.openrouter_client import API_URL, AllModelsFailedError
from app.ai.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Tope por PETICIÓN individual, bastante menor que VISION_BUDGET_SECONDS:
# igual que en openrouter_client.py, si un modelo se cuelga necesita fallar
# rápido para dejarle tiempo al resto de candidatos dentro del presupuesto
# total, en vez de agotarlo él solo.
TIMEOUT_SECONDS = 20.0
# Mismo motivo que CHAT_BUDGET_SECONDS en openrouter_client.py: sin un tope
# total, un modelo de visión "descubierto" que responde con 200 pero tarda
# muchísimo podía colgar la conversación entera.
VISION_BUDGET_SECONDS = 60.0
DEFAULT_PROMPT = (
    "Describe detalladamente esta imagen. Si contiene texto, transcríbelo "
    "literalmente. Si es un gráfico, tabla o documento, explica los datos "
    "que muestra. Sé concreto y no inventes nada que no se vea."
)


class VisionService:
    def __init__(
        self,
        api_key: str,
        catalog: FreeModelCatalog,
        headers: dict[str, str],
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._api_key = api_key
        self._catalog = catalog
        self._headers = headers
        self._rate_limiter = rate_limiter

    async def describe(self, image_bytes: bytes, prompt: str = DEFAULT_PROMPT) -> str:
        """Devuelve una descripción de la imagen, probando varios modelos."""
        models = await self._catalog.get_free_vision_models()
        if not models:
            raise AllModelsFailedError(
                "No hay ningún modelo gratuito con visión disponible ahora mismo"
            )

        data_url = _to_data_url(image_bytes)
        payload_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

        last_error: Exception | None = None
        try:
            async with asyncio.timeout(VISION_BUDGET_SECONDS):
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    for model in models:
                        if self._rate_limiter is not None:
                            await self._rate_limiter.acquire()

                        try:
                            response = await client.post(
                                API_URL,
                                headers=self._headers,
                                json={"model": model, "messages": payload_messages},
                            )
                        except httpx.HTTPError as exc:
                            logger.warning("Modelo de visión %s no disponible: %s", model, exc)
                            last_error = exc
                            continue

                        if response.status_code != 200:
                            logger.warning(
                                "Modelo de visión %s falló (HTTP %s)",
                                model,
                                response.status_code,
                            )
                            last_error = RuntimeError(f"HTTP {response.status_code} de {model}")
                            continue

                        content = (
                            response.json()
                            .get("choices", [{}])[0]
                            .get("message", {})
                            .get("content")
                        )
                        if content:
                            logger.info("Imagen descrita con el modelo %s", model)
                            return content

                        last_error = RuntimeError(f"Respuesta vacía de {model}")
        except TimeoutError as exc:
            logger.warning(
                "Se agotó el presupuesto de %ss probando modelos de visión",
                VISION_BUDGET_SECONDS,
            )
            last_error = exc

        raise AllModelsFailedError("Ningún modelo con visión pudo leer la imagen") from last_error


def _to_data_url(image_bytes: bytes) -> str:
    codificada = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{codificada}"
