import httpx
import pytest

from app.ai.openrouter_client import AllModelsFailedError, OpenRouterClient


def _mock_transport(responder):
    return httpx.MockTransport(responder)


@pytest.fixture
def patch_async_client(monkeypatch):
    """Sustituye httpx.AsyncClient por uno con un transporte simulado."""

    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = _mock_transport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr("app.ai.openrouter_client.httpx.AsyncClient", factory)

    return _patch


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_usa_el_primer_modelo_que_responde(patch_async_client):
    used_models = []

    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        used_models.append(model)
        return _ok("respuesta")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["modelo-a", "modelo-b"])

    assert await client.chat([{"role": "user", "content": "hola"}]) == "respuesta"
    assert used_models == ["modelo-a"]


@pytest.mark.asyncio
async def test_chat_devuelve_el_modelo_que_respondio(patch_async_client):
    """Quien llama necesita saber qué modelo contestó para poder excluirlo
    en un reintento si esa respuesta resulta no servir."""
    patch_async_client(lambda request: _ok("respuesta"))
    client = OpenRouterClient("key", ["modelo-a", "modelo-b"])

    result = await client.chat([{"role": "user", "content": "hola"}])

    assert result.model == "modelo-a"


@pytest.mark.asyncio
async def test_exclude_models_salta_al_siguiente_modelo(patch_async_client):
    used_models = []

    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        used_models.append(model)
        return _ok("respuesta")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["modelo-a", "modelo-b"])

    result = await client.chat(
        [{"role": "user", "content": "hola"}],
        exclude_models=frozenset({"modelo-a"}),
    )

    assert result.model == "modelo-b"
    assert used_models == ["modelo-b"]


@pytest.mark.asyncio
async def test_cambia_de_modelo_cuando_se_agota_la_cuota(patch_async_client):
    used_models = []

    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        used_models.append(model)
        if model == "modelo-a":
            return httpx.Response(429, text="rate limited")
        return _ok("desde el segundo modelo")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["modelo-a", "modelo-b"])

    result = await client.chat([{"role": "user", "content": "hola"}])
    assert result == "desde el segundo modelo"
    assert used_models == ["modelo-a", "modelo-b"]


@pytest.mark.asyncio
async def test_cambia_de_modelo_si_uno_devuelve_403(patch_async_client):
    """En OpenRouter, un 403 suele significar que ESE modelo rechazó la
    petición (moderación, política de datos), no que esté mal formada:
    debe probarse el siguiente modelo, no reventar la respuesta entera."""
    used_models = []

    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        used_models.append(model)
        if model == "modelo-a":
            return httpx.Response(403, text="forbidden")
        return _ok("desde el segundo modelo")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["modelo-a", "modelo-b"])

    result = await client.chat([{"role": "user", "content": "hola"}])
    assert result == "desde el segundo modelo"
    assert used_models == ["modelo-a", "modelo-b"]


@pytest.mark.asyncio
async def test_error_si_todos_los_modelos_fallan(patch_async_client):
    patch_async_client(lambda request: httpx.Response(429, text="rate limited"))
    client = OpenRouterClient("key", ["a", "b", "c"])

    with pytest.raises(AllModelsFailedError):
        await client.chat([{"role": "user", "content": "hola"}])


@pytest.mark.asyncio
async def test_respuesta_vacia_hace_probar_el_siguiente_modelo(patch_async_client):
    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        if model == "a":
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        return _ok("contenido real")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["a", "b"])

    assert await client.chat([{"role": "user", "content": "x"}]) == "contenido real"


@pytest.mark.asyncio
async def test_error_de_red_hace_probar_el_siguiente_modelo(patch_async_client):
    def responder(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        if model == "a":
            raise httpx.ConnectError("sin conexión", request=request)
        return _ok("ok")

    patch_async_client(responder)
    client = OpenRouterClient("key", ["a", "b"])

    assert await client.chat([{"role": "user", "content": "x"}]) == "ok"


@pytest.mark.asyncio
async def test_cabeceras_opcionales_se_incluyen():
    client = OpenRouterClient("key", ["a"], site_url="https://x.com", app_name="App")
    assert client._headers["HTTP-Referer"] == "https://x.com"
    assert client._headers["X-Title"] == "App"

    minimal = OpenRouterClient("key", ["a"])
    assert "HTTP-Referer" not in minimal._headers
