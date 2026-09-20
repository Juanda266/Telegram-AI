"""Descubrimiento automático de los modelos gratuitos vigentes en OpenRouter.

La lista de modelos gratuitos de OpenRouter cambia constantemente: modelos
que hoy son gratis mañana pasan a ser de pago y desaparecen. Una lista fija
en el .env queda obsoleta en semanas y el bot deja de responder.

Por eso consultamos el catálogo de OpenRouter y filtramos los modelos cuyo
precio es 0, quedándonos con una cadena de respaldo siempre actualizada.
Si la consulta falla, simplemente se usan los modelos configurados a mano.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT_SECONDS = 20.0
CACHE_TTL_SECONDS = 6 * 60 * 60
MAX_DISCOVERED_MODELS = 10

# Modelos que no sirven como asistente conversacional general, aunque sean
# gratis (embeddings, moderación, imagen).
_EXCLUDED_KEYWORDS = ("embed", "moderation", "whisper", "tts", "image-gen")


def _is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    try:
        prompt_price = float(pricing.get("prompt", "1"))
        completion_price = float(pricing.get("completion", "1"))
    except (TypeError, ValueError):
        return False
    return prompt_price == 0 and completion_price == 0


def _is_usable(model: dict) -> bool:
    model_id = (model.get("id") or "").lower()
    if not model_id or any(word in model_id for word in _EXCLUDED_KEYWORDS):
        return False
    modalities = (model.get("architecture") or {}).get("input_modalities") or ["text"]
    return "text" in modalities


def _supports_images(model: dict) -> bool:
    modalities = (model.get("architecture") or {}).get("input_modalities") or ["text"]
    return "image" in modalities


class FreeModelCatalog:
    """Cachea la lista de modelos gratuitos, refrescándola cada pocas horas."""

    def __init__(self, api_key: str = "", ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self._api_key = api_key
        self._ttl_seconds = ttl_seconds
        # Todos los modelos usables descubiertos, ordenados de mayor a menor
        # contexto (no solo los primeros N): así se puede servir tanto la
        # cola de los "grandes" como la de los "ligeros" sin dos consultas.
        self._cached_all: list[str] = []
        self._cached_vision: list[str] = []
        self._fetched_at: float = 0.0

    async def get_free_models(self, prefer_light: bool = False) -> list[str]:
        """Modelos gratuitos de texto, en el orden en que conviene probarlos.

        `prefer_light` prioriza los de menor contexto (normalmente más
        pequeños y rápidos): tiene sentido para mensajes cortos o charla
        casual, donde no hace falta el modelo más grande disponible. Es solo
        el orden de intento; si el elegido no sirve, el resto de la cadena
        de respaldo sigue funcionando igual.
        """
        await self._refrescar_si_hace_falta()
        if prefer_light:
            return list(reversed(self._cached_all[-MAX_DISCOVERED_MODELS:]))
        return self._cached_all[:MAX_DISCOVERED_MODELS]

    async def get_free_vision_models(self) -> list[str]:
        """Modelos gratuitos que además aceptan imágenes como entrada."""
        await self._refrescar_si_hace_falta()
        return self._cached_vision

    async def _refrescar_si_hace_falta(self) -> None:
        if self._cached_all and (time.monotonic() - self._fetched_at) < self._ttl_seconds:
            return

        models, vision_models = await self._fetch()
        if models:
            self._cached_all = models
            self._cached_vision = vision_models
            self._fetched_at = time.monotonic()

    async def _fetch(self) -> tuple[list[str], list[str]]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.get(MODELS_URL, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning(
                "No se pudo consultar el catálogo de modelos de OpenRouter: %s", exc
            )
            return [], []

        candidates = [
            model
            for model in payload.get("data", [])
            if _is_free(model) and _is_usable(model)
        ]
        # Más contexto = puede manejar conversaciones e investigaciones más
        # largas (y suele ser un modelo más grande y lento); se guardan
        # todos para poder servir también la punta de los más pequeños.
        candidates.sort(key=lambda m: m.get("context_length") or 0, reverse=True)

        discovered = [model["id"] for model in candidates]
        vision = [
            model["id"] for model in candidates if _supports_images(model)
        ][:MAX_DISCOVERED_MODELS]

        logger.info(
            "Modelos gratuitos descubiertos en OpenRouter: %s (con visión: %s)",
            ", ".join(discovered[:MAX_DISCOVERED_MODELS]) or "(ninguno)",
            ", ".join(vision) or "(ninguno)",
        )
        return discovered, vision
