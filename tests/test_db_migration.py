"""La base de datos evoluciona sin perder lo que ya hay dentro.

CREATE TABLE IF NOT EXISTS no toca las tablas existentes, así que las
columnas nuevas hay que añadirlas a mano. Si eso fallara, alguien que ya
tuviera el bot corriendo perdería sus suscriptores al actualizar.
"""

import sqlite3

import pytest

from app.storage.db import Database

ESQUEMA_ANTIGUO = """
CREATE TABLE users (
    telegram_user_id INTEGER PRIMARY KEY,
    is_premium INTEGER NOT NULL DEFAULT 0,
    premium_until TEXT,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    free_used_today INTEGER NOT NULL DEFAULT 0,
    free_used_date TEXT,
    created_at TEXT NOT NULL
);
"""


@pytest.fixture
def base_antigua(tmp_path):
    ruta = tmp_path / "antigua.db"
    conn = sqlite3.connect(ruta)
    conn.executescript(ESQUEMA_ANTIGUO)
    conn.execute(
        "INSERT INTO users VALUES (5, 1, '2030-01-01T00:00:00+00:00', "
        "'cus_x', 'sub_x', 3, '2026-09-18', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    return ruta


def test_se_anaden_las_columnas_que_faltan(base_antigua):
    Database(base_antigua)

    columnas = {
        fila[1]
        for fila in sqlite3.connect(base_antigua).execute("PRAGMA table_info(users)")
    }
    assert "payment_subscription_id" in columnas


@pytest.mark.asyncio
async def test_no_se_pierden_los_suscriptores_al_actualizar(base_antigua):
    db = Database(base_antigua)

    user = await db.get_or_create_user(5)

    assert user.is_premium
    assert user.premium_until == "2030-01-01T00:00:00+00:00"
    assert user.free_used_today == 3


@pytest.mark.asyncio
async def test_la_base_migrada_admite_las_funciones_nuevas(base_antigua):
    db = Database(base_antigua)

    await db.set_payment_subscription(5, "I-SUB")

    assert (await db.find_user_by_subscription_id("I-SUB")).telegram_user_id == 5


def test_migrar_dos_veces_no_falla(base_antigua):
    Database(base_antigua)
    Database(base_antigua)  # no debe lanzar "duplicate column name"
