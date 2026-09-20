import asyncio
import base64
import json

import httpx
import pytest

from app.ai import vision as module
from app.ai.openrouter_client import AllModelsFailedError
from app.ai.vision import VisionService, _to_data_url


class FakeCatalog:
    def __init__(self, vision_models):
        self._vision_models = vision_models

    async def get_free_vision_models(self):
        return self._vision_models


@pytest.fixture
def patch_vision_client(monkeypatch):
    def _patch(responder):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            return original(*args, **kwargs)

        monkeypatch.setattr("app.ai.vision.httpx.AsyncClient", factory)

    return _patch


def _ok(texto="Se ve un gato"):
    return httpx.Response(200, json={"choices": [{"message": {"content": texto}}]})


def _service(catalog):
    return VisionService(api_key="key", catalog=catalog, headers={"Authorization": "Bearer key"})


def test_data_url_codifica_en_base64():
    url = _to_data_url(b"contenido")
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"contenido"


@pytest.mark.asyncio
async def test_describe_una_imagen(patch_vision_client):
    patch_vision_client(lambda request: _ok())
    service = _service(FakeCatalog(["modelo-vision"]))

    assert await service.describe(b"imagen") == "Se ve un gato"


@pytest.mark.asyncio
async def test_la_imagen_viaja_en_el_mensaje(patch_vision_client):
    enviados = []

    def responder(request):
        enviados.append(json.loads(request.content))
        return _ok()

    patch_vision_client(responder)
    await _service(FakeCatalog(["modelo-vision"])).describe(b"imagen")

    contenido = enviados[0]["messages"][0]["content"]
    tipos = [parte["type"] for parte in contenido]
    assert tipos == ["text", "image_url"]
    assert contenido[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_prueba_el_siguiente_modelo_si_uno_falla(patch_vision_client):
    usados = []

    def responder(request):
        modelo = json.loads(request.content)["model"]
        usados.append(modelo)
        if modelo == "malo":
            return httpx.Response(429, text="rate limited")
        return _ok("descripción")

    patch_vision_client(responder)
    service = _service(FakeCatalog(["malo", "bueno"]))

    assert await service.describe(b"imagen") == "descripción"
    assert usados == ["malo", "bueno"]


@pytest.mark.asyncio
async def test_error_si_no_hay_modelos_con_vision():
    service = _service(FakeCatalog([]))

    with pytest.raises(AllModelsFailedError, match="visión"):
        await service.describe(b"imagen")


@pytest.mark.asyncio
async def test_error_si_todos_los_modelos_fallan(patch_vision_client):
    patch_vision_client(lambda request: httpx.Response(500, text="boom"))
    service = _service(FakeCatalog(["a", "b"]))

    with pytest.raises(AllModelsFailedError):
        await service.describe(b"imagen")


@pytest.mark.asyncio
async def test_respeta_el_limitador_de_peticiones(patch_vision_client):
    llamadas = []

    class SpyLimiter:
        async def acquire(self):
            llamadas.append(1)

    patch_vision_client(lambda request: _ok())
    service = VisionService(
        api_key="key",
        catalog=FakeCatalog(["modelo"]),
        headers={},
        rate_limiter=SpyLimiter(),
    )

    await service.describe(b"imagen")

    assert len(llamadas) == 1


@pytest.mark.asyncio
async def test_presupuesto_total_corta_un_modelo_que_no_suelta_respuesta(monkeypatch):
    """Mismo riesgo que en openrouter_client.py: un modelo de visión puede
    responder HTTP 200 pero tardar muchísimo sin colgar la conversación."""
    monkeypatch.setattr(module, "VISION_BUDGET_SECONDS", 0.05)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "tarde"}}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            await asyncio.sleep(10)
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    service = _service(FakeCatalog(["modelo-lento"]))

    with pytest.raises(AllModelsFailedError):
        await service.describe(b"imagen")
