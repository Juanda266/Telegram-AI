"""Herramienta para descargar y extraer el texto principal de una URL.

Complementa a web_search: la búsqueda da títulos y fragmentos cortos, esta
herramienta permite al modelo leer el contenido completo de una página
concreta cuando lo necesita para responder con más detalle.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

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


def _es_destino_permitido(url: str) -> tuple[bool, str]:
    """Comprueba que la URL apunte a internet y no a la red interna.

    La URL la elige el modelo a partir de lo que escribe el usuario, así que
    hay que tratarla como no confiable: sin esta comprobación, alguien podría
    pedirle al bot que leyera los metadatos del servidor en la nube (donde
    suele haber credenciales) o servicios internos no expuestos, y el bot se
    los mostraría (un SSRF de manual).
    """
    try:
        partes = urlparse(url)
    except ValueError:
        return False, "La URL no es válida."

    if partes.scheme not in ("http", "https"):
        return False, "Solo se admiten direcciones http o https."

    host = partes.hostname
    if not host:
        return False, "La URL no incluye un dominio."

    try:
        # Resolvemos el nombre: un dominio público puede apuntar a una IP
        # interna, así que no basta con mirar el texto de la URL.
        infos = socket.getaddrinfo(host, partes.port or (443 if partes.scheme == "https" else 80))
    except socket.gaierror:
        return False, "No se pudo resolver el dominio."

    for info in infos:
        direccion = ipaddress.ip_address(info[4][0])
        if (
            direccion.is_private
            or direccion.is_loopback
            or direccion.is_link_local
            or direccion.is_reserved
            or direccion.is_multicast
            or direccion.is_unspecified
        ):
            return False, "Esa dirección apunta a la red interna, no se puede leer."

    return True, ""


async def web_fetch(url: str) -> str:
    permitido, motivo = await asyncio.to_thread(_es_destino_permitido, url)
    if not permitido:
        logger.warning("Descarga bloqueada de %s: %s", url, motivo)
        return f"No se pudo descargar la URL: {motivo}"

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

    # Una redirección puede acabar en la red interna aunque el destino inicial
    # fuera público, así que se revisa también la URL final.
    permitido, motivo = await asyncio.to_thread(_es_destino_permitido, str(response.url))
    if not permitido:
        logger.warning("Redirección bloqueada hacia %s: %s", response.url, motivo)
        return f"No se pudo descargar la URL: {motivo}"

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
