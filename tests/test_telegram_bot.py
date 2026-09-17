import asyncio

import pytest
from telegram.error import BadRequest

from app.telegram_bot import (
    TELEGRAM_MESSAGE_LIMIT,
    ChatLocks,
    _is_authorized,
    _keep_typing,
    _reply_safely,
)


class FakeMessage:
    def __init__(self, fail_markdown: bool = False) -> None:
        self.sent: list[tuple[str, str | None]] = []
        self._fail_markdown = fail_markdown

    async def reply_text(self, text, parse_mode=None, **kwargs):
        if self._fail_markdown and parse_mode is not None:
            raise BadRequest("Can't parse entities")
        self.sent.append((text, parse_mode))


class FakeSettings:
    def __init__(self, allowed_user_ids: set[int]) -> None:
        self.allowed_user_ids = allowed_user_ids


def test_autorizacion_abierta_si_no_hay_lista():
    assert _is_authorized(FakeSettings(set()), 999)


def test_autorizacion_restringida():
    settings = FakeSettings({1, 2})
    assert _is_authorized(settings, 1)
    assert not _is_authorized(settings, 3)


@pytest.mark.asyncio
async def test_respuesta_corta_se_envia_entera():
    message = FakeMessage()
    await _reply_safely(message, "hola")
    assert message.sent == [("hola", "Markdown")]


@pytest.mark.asyncio
async def test_respuesta_larga_se_parte_en_varios_mensajes():
    message = FakeMessage()
    texto = "x" * (TELEGRAM_MESSAGE_LIMIT * 2 + 10)

    await _reply_safely(message, texto)

    assert len(message.sent) == 3
    assert sum(len(t) for t, _ in message.sent) == len(texto)


@pytest.mark.asyncio
async def test_markdown_invalido_cae_a_texto_plano():
    message = FakeMessage(fail_markdown=True)
    await _reply_safely(message, "**roto*")
    assert message.sent == [("**roto*", None)]


@pytest.mark.asyncio
async def test_respuesta_vacia_usa_texto_por_defecto():
    message = FakeMessage()
    await _reply_safely(message, "")
    assert message.sent[0][0] == "No tengo una respuesta para eso."


@pytest.mark.asyncio
async def test_chat_locks_serializa_el_mismo_chat():
    locks = ChatLocks()
    orden: list[str] = []

    async def tarea(nombre: str, espera: float):
        async with locks.acquire(1):
            orden.append(f"inicio-{nombre}")
            await asyncio.sleep(espera)
            orden.append(f"fin-{nombre}")

    await asyncio.gather(tarea("a", 0.02), tarea("b", 0))

    # Si el lock funciona, 'a' termina antes de que 'b' empiece.
    assert orden == ["inicio-a", "fin-a", "inicio-b", "fin-b"]


@pytest.mark.asyncio
async def test_chat_locks_no_bloquea_entre_chats_distintos():
    locks = ChatLocks()
    assert locks.acquire(1) is not locks.acquire(2)
    assert locks.acquire(1) is locks.acquire(1)


@pytest.mark.asyncio
async def test_keep_typing_reenvia_hasta_cancelarse(monkeypatch):
    monkeypatch.setattr("app.telegram_bot.TYPING_REFRESH_SECONDS", 0.01)
    llamadas = []

    class FakeBot:
        async def send_chat_action(self, chat_id, action):
            llamadas.append(chat_id)

    task = asyncio.create_task(_keep_typing(FakeBot(), 42))
    await asyncio.sleep(0.05)
    task.cancel()
    await task

    assert len(llamadas) > 1
    assert set(llamadas) == {42}
