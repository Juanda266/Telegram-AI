"""Herramienta de búsqueda web usada por el agente de IA.

Usa DuckDuckGo porque no requiere API key, lo que facilita levantar el
proyecto sin configuración extra. Está aislada detrás de una función simple
para poder cambiar de proveedor (Tavily, Brave, SerpAPI, etc.) más adelante
sin tocar el resto del agente.
"""

import asyncio
import logging

from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

TOOL_DEFINITION = {
    "name": "web_search",
    "description": (
        "Busca en la web información actual (noticias, hechos recientes, "
        "precios, datos que puedan haber cambiado después del entrenamiento "
        "del modelo, etc). Devuelve una lista de resultados con título, "
        "URL y un fragmento de texto. Úsala cuando el usuario pregunte algo "
        "que requiera información actualizada o que no sepas con certeza."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Términos de búsqueda, en el idioma más útil para el tema.",
            },
            "max_results": {
                "type": "integer",
                "description": "Cantidad de resultados a devolver (por defecto 5, máximo 10).",
            },
        },
        "required": ["query"],
    },
}


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


async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    max_results = max(1, min(max_results, 10))
    try:
        return await asyncio.to_thread(_search_sync, query, max_results)
    except Exception:
        logger.exception("Falló la búsqueda web para query=%r", query)
        return []
