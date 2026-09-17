import httpx
import pytest

from app.ai.tools import web_fetch as module
from app.ai.tools.web_fetch import MAX_CHARS, _es_destino_permitido, web_fetch


@pytest.fixture
def patch_dns(monkeypatch):
    """Simula la resolución DNS para no depender de la red en los tests."""

    def _patch(ip: str):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(2, 1, 6, "", (ip, port or 80))]

        monkeypatch.setattr(module.socket, "getaddrinfo", fake_getaddrinfo)

    return _patch


@pytest.fixture
def patch_http(monkeypatch):
    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    return _patch


def _html(cuerpo, url="https://ejemplo.com"):
    def responder(request):
        return httpx.Response(
            200, text=cuerpo, headers={"content-type": "text/html; charset=utf-8"}
        )

    return responder


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # red privada
        "192.168.1.1",  # red privada
        "172.16.0.1",  # red privada
        "169.254.169.254",  # metadatos de la nube: aquí viven las credenciales
        "0.0.0.0",  # sin especificar
    ],
)
def test_bloquea_direcciones_internas(ip, patch_dns):
    """La URL la elige el modelo a partir de lo que escribe el usuario: sin
    esto, alguien podría hacer que el bot leyera servicios internos."""
    patch_dns(ip)
    permitido, motivo = _es_destino_permitido("http://loquesea.com")
    assert not permitido
    assert "interna" in motivo


def test_permite_una_direccion_publica(patch_dns):
    patch_dns("93.184.216.34")
    permitido, _ = _es_destino_permitido("https://ejemplo.com")
    assert permitido


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://servidor/archivo", "gopher://x", "javascript:alert(1)"],
)
def test_bloquea_esquemas_no_http(url):
    permitido, motivo = _es_destino_permitido(url)
    assert not permitido
    assert "http" in motivo


def test_url_sin_dominio_se_rechaza():
    permitido, motivo = _es_destino_permitido("http://")
    assert not permitido


@pytest.mark.asyncio
async def test_extrae_el_texto_de_la_pagina(patch_dns, patch_http):
    patch_dns("93.184.216.34")
    patch_http(_html("<html><body><p>Hola</p><script>ignorar()</script></body></html>"))

    texto = await web_fetch("https://ejemplo.com")

    assert "Hola" in texto
    assert "ignorar" not in texto


@pytest.mark.asyncio
async def test_trunca_paginas_muy_largas(patch_dns, patch_http):
    patch_dns("93.184.216.34")
    patch_http(_html("<html><body>" + ("palabra " * 5000) + "</body></html>"))

    texto = await web_fetch("https://ejemplo.com")

    assert len(texto) <= MAX_CHARS + 50
    assert texto.endswith("[contenido truncado]")


@pytest.mark.asyncio
async def test_rechaza_contenido_que_no_es_html(patch_dns, patch_http):
    patch_dns("93.184.216.34")
    patch_http(
        lambda request: httpx.Response(
            200, content=b"binario", headers={"content-type": "application/pdf"}
        )
    )

    assert "no es HTML" in await web_fetch("https://ejemplo.com")


@pytest.mark.asyncio
async def test_no_descarga_si_el_destino_es_interno(patch_dns, patch_http):
    patch_dns("169.254.169.254")
    llamadas = []

    def responder(request):
        llamadas.append(1)
        return httpx.Response(200, text="secreto")

    patch_http(responder)

    resultado = await web_fetch("http://169.254.169.254/latest/meta-data/")

    assert "interna" in resultado
    # Lo importante: nunca se llegó a hacer la petición.
    assert llamadas == []


@pytest.mark.asyncio
async def test_error_de_red_no_revienta(patch_dns, patch_http):
    patch_dns("93.184.216.34")

    def responder(request):
        raise httpx.ConnectError("sin conexión", request=request)

    patch_http(responder)

    assert "No se pudo descargar" in await web_fetch("https://ejemplo.com")
