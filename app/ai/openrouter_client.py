"""Cliente para la API de OpenRouter (compatible con el formato de OpenAI).

OpenRouter permite acceder a muchos modelos distintos (Gemini, Llama,
DeepSeek, Qwen, Mistral, etc.) con una sola API key, incluyendo variantes
"...:free" sin costo. Este cliente recibe una lista de modelos en orden de
preferencia y, si uno falla (por límite de cuota, error del proveedor,
timeout, etc.), prueba automáticamente con el siguiente. Así el bot sigue
funcionando aunque se agote el cupo gratuito de un modelo en particular.
"""

import logging

import httpx

from app.ai.model_catalog import FreeModelCatalog
from app.ai.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 60.0

# Errores que indican "prueba con otro modelo": cuota agotada, modelo
# saturado/caído, o el proveedor rechaza la petición. Un 4xx por request mal
# formado (400) no se reintenta porque fallaría igual en cualquier modelo.
# 403 se incluye porque en OpenRouter suele significar que ESE modelo o
# proveedor en particular rechazó la petición (moderación, política de
# datos), no que la petición esté mal formada: el siguiente modelo de la
# lista puede aceptarla sin problema.
RETRYABLE_STATUS_CODES = {402, 403, 404, 408, 409, 429, 500, 502, 503, 504}


class AllModelsFailedError(RuntimeError):
    """Ninguno de los modelos configurados pudo responder."""


class ChatReply(str):
    """El texto de la respuesta, más el modelo que la generó.

    Se comporta como un `str` normal (nada de lo que ya compara o usa el
    resultado de `chat()` como texto se rompe), pero además expone
    `.model`, para que quien llama pueda excluir ese modelo si resulta que
    su respuesta no sirve (por ejemplo, un modelo que no es de chat de
    verdad y nunca respeta el formato que se le pide) sin arriesgarse a
    volver a toparse con el mismo modelo en un reintento.
    """

    def __new__(cls, content: str, model: str) -> "ChatReply":
        obj = super().__new__(cls, content)
        obj.model = model
        return obj


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        models: list[str],
        site_url: str = "",
        app_name: str = "",
        catalog: FreeModelCatalog | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._api_key = api_key
        self._models = models
        self._catalog = catalog
        self._rate_limiter = rate_limiter
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if site_url:
            self._headers["HTTP-Referer"] = site_url
        if app_name:
            self._headers["X-Title"] = app_name

    @property
    def headers(self) -> dict[str, str]:
        """Cabeceras de autenticación, reutilizables por otros servicios que
        llamen a la misma API (por ejemplo, el de visión)."""
        return dict(self._headers)

    async def _candidate_models(self) -> list[str]:
        """Modelos configurados primero y, detrás, los gratuitos descubiertos
        automáticamente (sin repetir), como red de seguridad extra."""
        candidates = list(self._models)
        if self._catalog is None:
            return candidates

        try:
            discovered = await self._catalog.get_free_models()
        except Exception:
            logger.exception("Fallo al obtener el catálogo de modelos gratuitos")
            return candidates

        ya_incluidos = set(candidates)
        candidates.extend(m for m in discovered if m not in ya_incluidos)
        return candidates

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.4,
        exclude_models: frozenset[str] = frozenset(),
    ) -> str:
        """Envía la conversación al primer modelo disponible y devuelve el texto.

        Recorre los modelos candidatos en orden hasta obtener una respuesta
        válida. `exclude_models` permite saltarse modelos que quien llama ya
        sabe que no sirvieron (por ejemplo, en un reintento tras una
        respuesta inválida), para no volver a toparse con ellos.
        """
        last_error: Exception | None = None
        models = [m for m in await self._candidate_models() if m not in exclude_models]

        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            for model in models:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if self._rate_limiter is not None:
                    await self._rate_limiter.acquire()

                try:
                    response = await client.post(
                        API_URL, headers=self._headers, json=payload
                    )
                except httpx.HTTPError as exc:
                    logger.warning("Modelo %s no disponible (red): %s", model, exc)
                    last_error = exc
                    continue

                if response.status_code == 200:
                    data = response.json()
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content")
                    if content:
                        logger.debug("Respuesta obtenida del modelo %s", model)
                        return ChatReply(content, model)
                    logger.warning("Modelo %s devolvió una respuesta vacía", model)
                    last_error = RuntimeError(f"Respuesta vacía de {model}")
                    continue

                if response.status_code in RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "Modelo %s falló (HTTP %s), probando el siguiente: %s",
                        model,
                        response.status_code,
                        response.text[:300],
                    )
                    last_error = RuntimeError(
                        f"HTTP {response.status_code} de {model}: {response.text[:300]}"
                    )
                    continue

                # Error no recuperable (ej. 400 por payload inválido): no
                # tiene sentido reintentar con otro modelo.
                response.raise_for_status()

        raise AllModelsFailedError(
            "Ningún modelo de OpenRouter respondió correctamente"
        ) from last_error
