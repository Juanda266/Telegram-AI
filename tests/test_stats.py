import pytest

from app.clock import today_iso
from app.storage.db import Database
from app.storage.memory import ConversationMemory


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_estadisticas_en_base_vacia(db):
    stats = await db.get_stats(today_iso())
    assert stats == {
        "usuarios": 0,
        "premium": 0,
        "activos_hoy": 0,
        "nuevos_hoy": 0,
        "mensajes_hoy": 0,
    }


@pytest.mark.asyncio
async def test_cuenta_usuarios_y_suscriptores(db):
    for user_id in (1, 2, 3):
        await db.get_or_create_user(user_id)
    await db.set_premium(2, is_premium=True, premium_until=None)

    stats = await db.get_stats(today_iso())
    assert stats["usuarios"] == 3
    assert stats["premium"] == 1
    assert stats["nuevos_hoy"] == 3


@pytest.mark.asyncio
async def test_cuenta_activos_y_mensajes_de_hoy(db):
    hoy = today_iso()
    await db.get_or_create_user(1)
    await db.increment_free_usage(1, hoy)

    memory = ConversationMemory(db, max_messages=10)
    await memory.append(1, {"role": "user", "content": "hola"})
    await memory.append(1, {"role": "assistant", "content": "qué tal"})

    stats = await db.get_stats(hoy)
    assert stats["activos_hoy"] == 1
    # Solo se cuentan los mensajes del usuario, no las respuestas del bot.
    assert stats["mensajes_hoy"] == 1


@pytest.mark.asyncio
async def test_actividad_de_otro_dia_no_cuenta_como_hoy(db):
    await db.get_or_create_user(1)
    await db.increment_free_usage(1, "2020-01-01")

    stats = await db.get_stats(today_iso())
    assert stats["activos_hoy"] == 0
    assert stats["usuarios"] == 1
