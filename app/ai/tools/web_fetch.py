"""Herramienta para descargar y extraer el texto principal de una URL.

Complementa a web_search: la búsqueda da títulos y fragmentos cortos, esta
herramienta permite al modelo leer el contenido completo de una página
concreta cuando lo necesita para responder con más detalle.
"""

import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_CHARS = 6000
TIMEOUT_SECONDS = 15.0
USER_AGENT = "Mozilla/5.0 (compatible; TelegramAIAssistant/1.0)"

TOOL_DEFINITION = {
    "name": "web_fetch",
    "description": (
        "Descarga una página web y devuelve su texto principal (sin HTML). "
        "Úsala después de web_search cuando necesites leer el contenido "
        "completo de un resultado concreto para dar una respuesta más "
        "precisa o citar detalles."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL completa de la página a leer (incluyendo http/https).",
            }
        },
        "required": ["url"],
    },
}


async def web_fetch(url: str) -> str:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Falló la descarga de %s: %s", url, exc)
        return f"No se pudo descargar la URL: {exc}"

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        return f"El contenido no es HTML (content-type: {content_type}), no se puede extraer texto."

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "... [contenido truncado]"
    return text or "La página no tiene texto extraíble."
