import json

import pytest

from app.ai.agent import ResearchAgent, _extract_json


class FakeClient:
    """Cliente falso que devuelve respuestas pregrabadas, una por turno."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    async def chat(self, messages, temperature: float = 0.4) -> str:
        self.calls.append(list(messages))
        return self._replies.pop(0)


def test_extract_json_plano():
    assert _extract_json('{"action": "final", "content": "hola"}') == {
        "action": "final",
        "content": "hola",
    }


def test_extract_json_en_bloque_de_codigo():
    raw = 'Claro:\n```json\n{"action": "final", "content": "hola"}\n```\n'
    assert _extract_json(raw)["content"] == "hola"


def test_extract_json_con_texto_alrededor():
    raw = 'Pienso que... {"action": "final", "content": "hola"} listo'
    assert _extract_json(raw)["content"] == "hola"


def test_extract_json_invalido_lanza_error():
    with pytest.raises(ValueError):
        _extract_json("esto no es json en absoluto")


@pytest.mark.asyncio
async def test_agente_responde_directo_sin_buscar():
    client = FakeClient(['{"action": "final", "content": "¡Hola!"}'])
    agent = ResearchAgent(client=client, max_steps=3)

    result = await agent.run([], "hola")

    assert result == "¡Hola!"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_agente_busca_y_luego_responde(monkeypatch):
    async def fake_search(query, max_results=5):
        return [{"title": "T", "url": "https://x.com", "snippet": "dato"}]

    monkeypatch.setattr("app.ai.agent.web_search", fake_search)

    client = FakeClient(
        [
            '{"action": "web_search", "input": {"query": "clima hoy"}}',
            '{"action": "final", "content": "Hace sol."}',
        ]
    )
    agent = ResearchAgent(client=client, max_steps=3)

    result = await agent.run([], "¿qué clima hace?")

    assert result == "Hace sol."
    # El segundo turno debe incluir el resultado de la herramienta.
    assert "[Resultado de la herramienta]" in client.calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_agente_respeta_max_steps(monkeypatch):
    async def fake_search(query, max_results=5):
        return []

    monkeypatch.setattr("app.ai.agent.web_search", fake_search)

    client = FakeClient(['{"action": "web_search", "input": {"query": "x"}}'] * 5)
    agent = ResearchAgent(client=client, max_steps=2)

    result = await agent.run([], "busca algo")

    assert "respuesta definitiva" in result
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_agente_incluye_historial():
    client = FakeClient(['{"action": "final", "content": "ok"}'])
    agent = ResearchAgent(client=client, max_steps=3)
    history = [{"role": "user", "content": "anterior"}]

    await agent.run(history, "nuevo")

    roles = [m["role"] for m in client.calls[0]]
    assert roles == ["system", "user", "user"]
    assert client.calls[0][-1]["content"] == "nuevo"


@pytest.mark.asyncio
async def test_respuesta_no_json_se_devuelve_tal_cual():
    client = FakeClient(["Simplemente texto plano sin json"])
    agent = ResearchAgent(client=client, max_steps=3)

    assert await agent.run([], "hola") == "Simplemente texto plano sin json"


@pytest.mark.asyncio
async def test_final_vacio_devuelve_mensaje_por_defecto():
    client = FakeClient([json.dumps({"action": "final", "content": "   "})])
    agent = ResearchAgent(client=client, max_steps=3)

    assert await agent.run([], "hola") == "No tengo una respuesta para eso."
