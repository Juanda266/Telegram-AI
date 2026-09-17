import pytest

from app.config import load_settings

BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "OPENROUTER_API_KEY": "sk-or-test",
}

STRIPE_ENV = {
    "BILLING_ENABLED": "true",
    "STRIPE_SECRET_KEY": "sk_test",
    "STRIPE_PRICE_ID": "price_123",
    "STRIPE_WEBHOOK_SECRET": "whsec_123",
}


@pytest.fixture
def env(monkeypatch):
    """Parte de un entorno limpio para que el .env local no afecte los tests."""
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODELS",
        "BILLING_ENABLED",
        "FREE_DAILY_MESSAGES",
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_ID",
        "STRIPE_WEBHOOK_SECRET",
        "ALLOWED_TELEGRAM_USER_IDS",
        "MAX_HISTORY_MESSAGES",
        "PORT",
        "TELEGRAM_STARS_PRICE",
        "TELEGRAM_STARS_SUBSCRIPTION",
    ):
        monkeypatch.delenv(key, raising=False)

    def _set(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    _set(**BASE_ENV)
    return _set


def test_configuracion_minima(env):
    settings = load_settings()
    assert settings.telegram_bot_token == "123:abc"
    assert settings.openrouter_models  # trae la lista por defecto
    assert not settings.billing.enabled
    assert settings.allowed_user_ids == set()


def test_falta_token_de_telegram(env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_settings()


def test_falta_api_key_de_openrouter(env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        load_settings()


def test_facturacion_incompleta_falla_con_mensaje_claro(env):
    env(BILLING_ENABLED="true", STRIPE_SECRET_KEY="sk_test")
    with pytest.raises(RuntimeError, match="STRIPE_PRICE_ID"):
        load_settings()


def test_facturacion_completa(env):
    env(**STRIPE_ENV, FREE_DAILY_MESSAGES="7")
    settings = load_settings()
    assert settings.billing.enabled
    assert settings.billing.free_daily_messages == 7


def test_lista_de_modelos_se_parsea_y_limpia(env):
    env(OPENROUTER_MODELS=" modelo-a , modelo-b ,, ")
    assert load_settings().openrouter_models == ["modelo-a", "modelo-b"]


def test_lista_de_modelos_vacia_falla(env):
    env(OPENROUTER_MODELS="  ,  ")
    with pytest.raises(RuntimeError, match="OPENROUTER_MODELS"):
        load_settings()


def test_ids_autorizados_se_parsean(env):
    env(ALLOWED_TELEGRAM_USER_IDS="111, 222 ,333")
    assert load_settings().allowed_user_ids == {111, 222, 333}


def test_entero_invalido_falla_con_nombre_de_variable(env):
    env(MAX_HISTORY_MESSAGES="muchos")
    with pytest.raises(RuntimeError, match="MAX_HISTORY_MESSAGES"):
        load_settings()


def test_stars_por_si_solo_basta_para_facturar(env):
    """Telegram Stars no necesita Stripe: es el método recomendado."""
    env(BILLING_ENABLED="true", TELEGRAM_STARS_PRICE="150")
    settings = load_settings()
    assert settings.billing.enabled
    assert settings.billing.stars_price == 150


def test_sin_ningun_metodo_de_pago_falla(env):
    env(BILLING_ENABLED="true")
    with pytest.raises(RuntimeError, match="método de pago"):
        load_settings()


def test_stripe_a_medias_falla_aunque_haya_stars(env):
    """Una configuración de Stripe incompleta es un error de despiste, no
    algo que debamos ignorar en silencio."""
    env(BILLING_ENABLED="true", TELEGRAM_STARS_PRICE="150", STRIPE_SECRET_KEY="sk_test")
    with pytest.raises(RuntimeError, match="a medias"):
        load_settings()
