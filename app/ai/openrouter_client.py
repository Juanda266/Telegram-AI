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

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 60.0

# Errores que indican "prueba con otro modelo": cuota agotada, modelo
# saturado/caído, o el proveedor rechaza la petición. Un 4xx por request mal
# formado (400) no se reintenta porque fallaría igual en cualquier modelo.
RETRYABLE_STATUS_CODES = {402, 404, 408, 409, 429, 500, 502, 503, 504}


class AllModelsFailedError(RuntimeError):
    """Ninguno de los modelos configurados pudo responder."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        models: list[str],
        site_url: str = "",
        app_name: str = "",
    ) -> None:
        self._api_key = api_key
        self._models = models
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if site_url:
            self._headers["HTTP-Referer"] = site_url
        if app_name:
            self._headers["X-Title"] = app_name

    async def chat(self, messages: list[dict], temperature: float = 0.4) -> str:
        """Envía la conversación al primer modelo disponible y devuelve el texto.

        Recorre self._models en orden hasta obtener una respuesta válida.
        """
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            for model in self._models:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
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
                        return content
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
