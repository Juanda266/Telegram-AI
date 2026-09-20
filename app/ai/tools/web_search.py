"""Herramienta de búsqueda web usada por el agente de IA.

Usa DuckDuckGo porque no requiere API key, lo que facilita levantar el
proyecto sin configuración extra. Está aislada detrás de una función simple
para poder cambiar de proveedor (Tavily, Brave, SerpAPI, etc.) más adelante
sin tocar el resto del agente.
"""

import asyncio
import logging
import re
from urllib.parse import quote

import httpx
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://es.wikipedia.org/w/api.php"
# La política de Wikimedia exige un User-Agent descriptivo con forma de
# contacto; sin él (o con el genérico de httpx) responde 403 Forbidden.
# https://meta.wikimedia.org/wiki/User-Agent_policy
WIKIPEDIA_USER_AGENT = (
    "TelegramAIAssistant/1.0 (https://github.com/Juanda266/Telegram-AI)"
)


def _search_sync(query: str, max_results: int) -> list[dict[str, str]]:
    with DDGS() as ddgs:
        raw_results = ddgs.text(query, max_results=max_results)
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", ""),
            }
            for item in raw_results
        ]


async def _wikipedia_search(query: str, max_results: int) -> list[dict[str, str]]:
    """Respaldo cuando DuckDuckGo falla o limita las peticiones.

    No cubre noticias ni datos muy recientes, pero evita que el agente se
    quede completamente a ciegas en preguntas de conocimiento general.
    """
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": max_results,
        "format": "json",
    }
    try:
        headers = {"User-Agent": WIKIPEDIA_USER_AGENT}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(WIKIPEDIA_API_URL, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("El respaldo de Wikipedia también falló: %s", exc)
        return []

    results = []
    for item in payload.get("query", {}).get("search", []):
        titulo = item.get("title", "")
        results.append(
            {
                "title": titulo,
                "url": f"https://es.wikipedia.org/wiki/{quote(titulo.replace(' ', '_'))}",
                # El extracto viene con etiquetas HTML de resaltado.
                "snippet": re.sub(r"<[^>]+>", "", item.get("snippet", "")),
            }
        )
    return results


async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    max_results = max(1, min(max_results, 10))
    try:
        results = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception:
        logger.exception("Falló la búsqueda web para query=%r", query)
        results = []

    if results:
        return results

    logger.info("Sin resultados de DuckDuckGo, se prueba con Wikipedia")
    return await _wikipedia_search(query, max_results)
