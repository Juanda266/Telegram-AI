import asyncio
import time

import pytest

from app.ai.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_permite_las_llamadas_dentro_de_la_cuota():
    limiter = RateLimiter(max_calls=3, period_seconds=10)
    inicio = time.monotonic()

    for _ in range(3):
        await limiter.acquire()

    assert time.monotonic() - inicio < 0.1


@pytest.mark.asyncio
async def test_hace_esperar_al_superar_la_cuota():
    limiter = RateLimiter(max_calls=2, period_seconds=0.2)
    inicio = time.monotonic()

    for _ in range(3):
        await limiter.acquire()

    # La tercera llamada tuvo que esperar a que expirara la ventana.
    assert time.monotonic() - inicio >= 0.2


@pytest.mark.asyncio
async def test_la_ventana_se_libera_con_el_tiempo():
    limiter = RateLimiter(max_calls=1, period_seconds=0.1)
    await limiter.acquire()
    await asyncio.sleep(0.15)

    inicio = time.monotonic()
    await limiter.acquire()

    assert time.monotonic() - inicio < 0.05


@pytest.mark.asyncio
async def test_varias_corrutinas_comparten_la_cuota():
    limiter = RateLimiter(max_calls=2, period_seconds=0.2)
    inicio = time.monotonic()

    await asyncio.gather(*(limiter.acquire() for _ in range(4)))

    # 4 llamadas con cuota de 2 por ventana: al menos una ventana de espera.
    assert time.monotonic() - inicio >= 0.2


@pytest.mark.asyncio
async def test_el_cliente_usa_el_limitador():
    import httpx

    from app.ai.openrouter_client import OpenRouterClient

    llamadas = []

    class SpyLimiter:
        async def acquire(self):
            llamadas.append(1)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
    )
    client = OpenRouterClient("key", ["a"], rate_limiter=SpyLimiter())

    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    import app.ai.openrouter_client as module

    module.httpx.AsyncClient = factory
    try:
        await client.chat([{"role": "user", "content": "hola"}])
    finally:
        module.httpx.AsyncClient = original

    assert len(llamadas) == 1
