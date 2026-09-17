import httpx
import pytest

from app.ai.model_catalog import FreeModelCatalog
from app.ai.openrouter_client import OpenRouterClient


def _model(model_id, prompt="0", completion="0", context=8000, modalities=None):
    return {
        "id": model_id,
        "pricing": {"prompt": prompt, "completion": completion},
        "context_length": context,
        "architecture": {"input_modalities": modalities or ["text"]},
    }


@pytest.fixture
def patch_catalog_client(monkeypatch):
    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr("app.ai.model_catalog.httpx.AsyncClient", factory)

    return _patch


def _responder(models):
    return lambda request: httpx.Response(200, json={"data": models})


@pytest.mark.asyncio
async def test_descubre_solo_modelos_gratuitos(patch_catalog_client):
    patch_catalog_client(
        _responder(
            [
                _model("gratis/a"),
                _model("pago/b", prompt="0.5", completion="1.5"),
                _model("gratis/c"),
            ]
        )
    )

    assert await FreeModelCatalog().get_free_models() == ["gratis/a", "gratis/c"]


@pytest.mark.asyncio
async def test_ordena_por_contexto_descendente(patch_catalog_client):
    patch_catalog_client(
        _responder(
            [
                _model("chico", context=4000),
                _model("grande", context=200000),
                _model("mediano", context=32000),
            ]
        )
    )

    assert await FreeModelCatalog().get_free_models() == ["grande", "mediano", "chico"]


@pytest.mark.asyncio
async def test_excluye_modelos_no_conversacionales(patch_catalog_client):
    patch_catalog_client(
        _responder([_model("proveedor/text-embed-3"), _model("proveedor/chat")])
    )

    assert await FreeModelCatalog().get_free_models() == ["proveedor/chat"]


@pytest.mark.asyncio
async def test_precio_invalido_no_se_considera_gratis(patch_catalog_client):
    patch_catalog_client(
        _responder([_model("raro", prompt="gratis", completion="gratis"), _model("ok")])
    )

    assert await FreeModelCatalog().get_free_models() == ["ok"]


@pytest.mark.asyncio
async def test_error_de_red_devuelve_lista_vacia(patch_catalog_client):
    def responder(request):
        raise httpx.ConnectError("sin red", request=request)

    patch_catalog_client(responder)

    assert await FreeModelCatalog().get_free_models() == []


@pytest.mark.asyncio
async def test_usa_cache_y_no_repite_la_consulta(patch_catalog_client):
    llamadas = []

    def responder(request):
        llamadas.append(1)
        return httpx.Response(200, json={"data": [_model("gratis/a")]})

    patch_catalog_client(responder)
    catalog = FreeModelCatalog()

    await catalog.get_free_models()
    await catalog.get_free_models()

    assert len(llamadas) == 1


@pytest.mark.asyncio
async def test_cliente_añade_modelos_descubiertos_como_respaldo():
    class FakeCatalog:
        async def get_free_models(self):
            return ["configurado", "descubierto-1", "descubierto-2"]

    client = OpenRouterClient("key", ["configurado"], catalog=FakeCatalog())

    # El configurado va primero y no se duplica.
    assert await client._candidate_models() == [
        "configurado",
        "descubierto-1",
        "descubierto-2",
    ]


@pytest.mark.asyncio
async def test_catalogo_roto_no_rompe_el_cliente():
    class BrokenCatalog:
        async def get_free_models(self):
            raise RuntimeError("boom")

    client = OpenRouterClient("key", ["configurado"], catalog=BrokenCatalog())

    assert await client._candidate_models() == ["configurado"]
