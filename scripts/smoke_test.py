"""Prueba de humo: levanta el servidor HTTP real y comprueba sus endpoints.

Complementa a los tests unitarios: verifica que la aplicación arranca de
verdad (configuración, base de datos y servidor web juntos), no solo que
las piezas funcionan por separado.

Uso:  python scripts/smoke_test.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:token-de-prueba")
os.environ.setdefault("OPENROUTER_API_KEY", "clave-de-prueba")
os.environ.setdefault("BILLING_ENABLED", "true")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_falsa")
os.environ.setdefault("STRIPE_PRICE_ID", "price_falso")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_falso")
os.environ.setdefault("PORT", "8099")
os.environ.setdefault(
    "DATABASE_PATH", str(Path(tempfile.gettempdir()) / "smoke_telegram_ai.db")
)

import httpx  # noqa: E402

from app.billing.service import BillingService  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.payments.stripe_client import StripeService  # noqa: E402
from app.payments.webhook_handler import WebhookHandler  # noqa: E402
from app.payments.webhook_server import build_webhook_app, start_webhook_server  # noqa: E402
from app.payments.wompi import WompiService  # noqa: E402
from app.storage.db import Database  # noqa: E402
from app.storage.memory import ConversationMemory  # noqa: E402

BASE_URL = "http://127.0.0.1:{port}"


async def main() -> int:
    settings = load_settings()
    db = Database(settings.database_path)
    stripe_service = StripeService(
        secret_key=settings.billing.stripe_secret_key,
        webhook_secret=settings.billing.stripe_webhook_secret,
        price_id=settings.billing.stripe_price_id,
        success_url=settings.billing.stripe_success_url,
        cancel_url=settings.billing.stripe_cancel_url,
    )
    wompi_service = WompiService(
        public_key="pub_test",
        integrity_secret="integridad",
        events_secret="eventos",
        amount=20000,
    )
    app = build_webhook_app(
        stripe_service,
        WebhookHandler(db, stripe_service),
        wompi_service=wompi_service,
    )
    runner = await start_webhook_server(app, settings.http_port)
    base = BASE_URL.format(port=settings.http_port)
    fallos = []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{base}/health")
            if response.status_code != 200 or response.json().get("status") != "ok":
                fallos.append(f"/health devolvió {response.status_code}")

            # Un webhook con firma falsa debe rechazarse SIEMPRE, en todas
            # las pasarelas: estas direcciones son públicas.
            for ruta, cuerpo, cabeceras in (
                (
                    "/stripe/webhook",
                    b'{"id": "evt_falso", "type": "checkout.session.completed"}',
                    {"Stripe-Signature": "firma-invalida"},
                ),
                (
                    "/wompi/webhook",
                    b'{"data": {"transaction": {"status": "APPROVED", '
                    b'"reference": "premium-1-x"}}, "timestamp": 1, '
                    b'"signature": {"properties": ["transaction.status"], '
                    b'"checksum": "falso"}}',
                    {},
                ),
            ):
                response = await client.post(f"{base}{ruta}", content=cuerpo, headers=cabeceras)
                if response.status_code != 400:
                    fallos.append(
                        f"{ruta} con firma inválida devolvió {response.status_code}, "
                        "se esperaba 400"
                    )

        # La cuota y el historial deben funcionar sobre la base real.
        billing = BillingService(db, free_daily_messages=2, billing_enabled=True)
        if not (await billing.check_and_consume(1)).allowed:
            fallos.append("el primer mensaje del plan gratuito fue rechazado")

        memory = ConversationMemory(db, max_messages=5)
        await memory.append(1, {"role": "user", "content": "hola"})
        if len(await memory.get(1)) != 1:
            fallos.append("el historial no guardó el mensaje")
    finally:
        await runner.cleanup()

    if fallos:
        print("SMOKE TEST FALLÓ:")
        for fallo in fallos:
            print(f"  - {fallo}")
        return 1

    print("SMOKE TEST OK: servidor, base de datos, cuotas e historial funcionan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
