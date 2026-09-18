import asyncio

import pytest
from telegram.error import BadRequest

from app.telegram_bot import (
    TELEGRAM_MESSAGE_LIMIT,
    _is_authorized,
    _keep_typing,
    _reply_safely,
    _texto_dirigido_al_bot,
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


class FakeChat:
    def __init__(self, tipo):
        self.type = tipo


class FakeUser:
    def __init__(self, is_bot=False):
        self.is_bot = is_bot


class FakeIncoming:
    def __init__(self, texto="", tipo="private", reply_de=None, caption=None):
        self.text = texto
        self.caption = caption
        self.chat = FakeChat(tipo)
        self.reply_to_message = reply_de


def test_en_privado_responde_a_todo():
    mensaje = FakeIncoming("hola", tipo="private")
    assert _texto_dirigido_al_bot(mensaje, "mibot") == "hola"


def test_en_grupo_ignora_lo_que_no_va_con_el():
    """Responder a todo en un grupo sería molesto y agotaría la cuota
    compartida de OpenRouter con conversaciones ajenas."""
    mensaje = FakeIncoming("nos vemos mañana", tipo="group")
    assert _texto_dirigido_al_bot(mensaje, "mibot") is None


def test_en_grupo_responde_si_lo_mencionan():
    mensaje = FakeIncoming("@mibot ¿qué hora es?", tipo="group")
    assert _texto_dirigido_al_bot(mensaje, "mibot") == "¿qué hora es?"


def test_en_grupo_responde_si_le_contestan():
    respuesta_del_bot = FakeIncoming(tipo="group")
    respuesta_del_bot.from_user = FakeUser(is_bot=True)
    mensaje = FakeIncoming("¿y eso por qué?", tipo="group", reply_de=respuesta_del_bot)

    assert _texto_dirigido_al_bot(mensaje, "mibot") == "¿y eso por qué?"


def test_en_grupo_ignora_respuestas_a_otras_personas():
    mensaje_de_persona = FakeIncoming(tipo="group")
    mensaje_de_persona.from_user = FakeUser(is_bot=False)
    mensaje = FakeIncoming("claro", tipo="group", reply_de=mensaje_de_persona)

    assert _texto_dirigido_al_bot(mensaje, "mibot") is None


def test_usa_el_pie_de_foto_cuando_no_hay_texto():
    mensaje = FakeIncoming(texto=None, tipo="private", caption="¿qué es esto?")
    assert _texto_dirigido_al_bot(mensaje, "mibot") == "¿qué es esto?"
