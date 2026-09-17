import httpx
import pytest

from app.ai.tools import web_search as module
from app.ai.tools.web_search import web_search


@pytest.fixture
def patch_wikipedia(monkeypatch):
    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    return _patch


def _wikipedia_ok(request):
    return httpx.Response(
        200,
        json={
            "query": {
                "search": [
                    {"title": "Gato doméstico", "snippet": "El <b>gato</b> es un felino"}
                ]
            }
        },
    )


@pytest.mark.asyncio
async def test_usa_duckduckgo_cuando_funciona(monkeypatch, patch_wikipedia):
    monkeypatch.setattr(
        module,
        "_search_sync",
        lambda query, max_results: [
            {"title": "T", "url": "https://x.com", "snippet": "s"}
        ],
    )
    patch_wikipedia(_wikipedia_ok)

    resultados = await web_search("gatos")

    assert resultados[0]["url"] == "https://x.com"


@pytest.mark.asyncio
async def test_cae_a_wikipedia_si_duckduckgo_falla(monkeypatch, patch_wikipedia):
    def boom(query, max_results):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(module, "_search_sync", boom)
    patch_wikipedia(_wikipedia_ok)

    resultados = await web_search("gatos")

    assert len(resultados) == 1
    assert resultados[0]["title"] == "Gato doméstico"
    assert "wikipedia.org/wiki/Gato_dom" in resultados[0]["url"]
    # El extracto debe venir sin etiquetas HTML.
    assert "<b>" not in resultados[0]["snippet"]


@pytest.mark.asyncio
async def test_cae_a_wikipedia_si_no_hay_resultados(monkeypatch, patch_wikipedia):
    monkeypatch.setattr(module, "_search_sync", lambda query, max_results: [])
    patch_wikipedia(_wikipedia_ok)

    assert len(await web_search("gatos")) == 1


@pytest.mark.asyncio
async def test_si_todo_falla_devuelve_lista_vacia(monkeypatch, patch_wikipedia):
    monkeypatch.setattr(module, "_search_sync", lambda query, max_results: [])

    def wikipedia_caida(request):
        raise httpx.ConnectError("sin red", request=request)

    patch_wikipedia(wikipedia_caida)

    assert await web_search("gatos") == []


@pytest.mark.asyncio
async def test_limita_el_numero_de_resultados(monkeypatch, patch_wikipedia):
    pedidos = []

    def spy(query, max_results):
        pedidos.append(max_results)
        return [{"title": "T", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(module, "_search_sync", spy)
    patch_wikipedia(_wikipedia_ok)

    await web_search("x", max_results=999)
    await web_search("x", max_results=0)

    assert pedidos == [10, 1]
