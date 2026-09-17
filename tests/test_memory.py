import pytest

from app.storage.db import Database
from app.storage.memory import ConversationMemory


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_historial_vacio_al_inicio(db):
    memory = ConversationMemory(db, max_messages=10)
    assert await memory.get(1) == []


@pytest.mark.asyncio
async def test_guarda_y_devuelve_en_orden_cronologico(db):
    memory = ConversationMemory(db, max_messages=10)

    await memory.append(1, {"role": "user", "content": "primero"})
    await memory.append(1, {"role": "assistant", "content": "segundo"})

    assert await memory.get(1) == [
        {"role": "user", "content": "primero"},
        {"role": "assistant", "content": "segundo"},
    ]


@pytest.mark.asyncio
async def test_conserva_solo_los_ultimos_mensajes(db):
    memory = ConversationMemory(db, max_messages=3)

    for i in range(6):
        await memory.append(1, {"role": "user", "content": f"msg-{i}"})

    contenidos = [m["content"] for m in await memory.get(1)]
    assert contenidos == ["msg-3", "msg-4", "msg-5"]


@pytest.mark.asyncio
async def test_los_chats_no_se_mezclan(db):
    memory = ConversationMemory(db, max_messages=10)

    await memory.append(1, {"role": "user", "content": "del chat 1"})
    await memory.append(2, {"role": "user", "content": "del chat 2"})

    assert [m["content"] for m in await memory.get(1)] == ["del chat 1"]
    assert [m["content"] for m in await memory.get(2)] == ["del chat 2"]


@pytest.mark.asyncio
async def test_clear_borra_solo_el_chat_indicado(db):
    memory = ConversationMemory(db, max_messages=10)
    await memory.append(1, {"role": "user", "content": "a"})
    await memory.append(2, {"role": "user", "content": "b"})

    await memory.clear(1)

    assert await memory.get(1) == []
    assert len(await memory.get(2)) == 1


@pytest.mark.asyncio
async def test_el_historial_sobrevive_a_un_reinicio(db, tmp_path):
    """Es el motivo de pasar de RAM a SQLite: reiniciar no debe borrar nada."""
    path = tmp_path / "persistente.db"
    memory = ConversationMemory(Database(path), max_messages=10)
    await memory.append(1, {"role": "user", "content": "recuérdame esto"})

    # Simulamos un reinicio abriendo la base de datos desde cero.
    memory_tras_reinicio = ConversationMemory(Database(path), max_messages=10)

    assert [m["content"] for m in await memory_tras_reinicio.get(1)] == ["recuérdame esto"]
