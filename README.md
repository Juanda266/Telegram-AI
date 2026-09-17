# Telegram AI Assistant

Asistente virtual para Telegram con capacidad de **investigar en la web**
(similar a Gemini o Perplexity), con **plan gratuito y suscripción de pago**
(Telegram Stars o Stripe), pensado para poder ampliarse en el futuro a otras
plataformas además de Telegram.

## Cómo funciona

- El "cerebro" es un modelo de lenguaje servido a través de
  [OpenRouter](https://openrouter.ai), que da acceso a decenas de modelos
  con una sola API key gratuita. Por defecto se usa `openrouter/free`, el
  router de OpenRouter que reparte las peticiones entre los modelos
  gratuitos disponibles en ese momento.
- **Nunca se queda sin modelo:** como la lista de modelos gratis de
  OpenRouter cambia cada pocas semanas, el bot consulta el catálogo, se
  queda con los que cuestan 0 y los usa como cadena de respaldo. Si un
  modelo se queda sin cupo (HTTP 429), falla o devuelve vacío, pasa
  automáticamente al siguiente.
- El agente sigue un ciclo tipo *ReAct*: en cada turno decide si necesita
  buscar en la web (`web_search`), leer una página completa (`web_fetch`),
  hacer un cálculo (`calculator`) o si ya puede responder (`final`). Esto
  funciona con modelos gratuitos que no soportan "function calling" nativo.
- La búsqueda web usa DuckDuckGo (no requiere API key), con Wikipedia como
  respaldo si DuckDuckGo limita las peticiones.
- Los usuarios, su consumo diario y su estado de suscripción se guardan en
  SQLite, para que sobrevivan a reinicios del proceso.
- Los pagos se cobran con **Telegram Stars** (dentro de la propia app, sin
  cuenta de comercio) o con **Stripe Checkout** (nunca tocamos datos de
  tarjeta). En ambos casos el Premium se activa y se revoca solo.

## Estructura del proyecto

```
main.py                        Punto de entrada (bot + servidor de webhooks)
app/config.py                   Carga y validación de configuración (.env)
app/telegram_bot.py             Handlers y comandos de Telegram
app/ai/agent.py                 Bucle del agente (decide buscar o responder)
app/ai/openrouter_client.py     Cliente a OpenRouter con fallback entre modelos
app/ai/model_catalog.py         Descubre automáticamente los modelos gratis vigentes
app/ai/vision.py                Lectura de imágenes con modelos de visión gratuitos
app/ai/rate_limiter.py          Respeta la cuota de peticiones por minuto
app/ai/tools/web_search.py      Herramienta de búsqueda (DuckDuckGo)
app/ai/tools/web_fetch.py       Herramienta para leer el contenido de una URL
app/ai/tools/calculator.py      Calculadora segura (sin eval) para el agente
app/billing/service.py          Cuotas del plan gratuito y estado Premium
app/payments/telegram_stars.py  Cobros con Telegram Stars (sin cuenta de comercio)
app/payments/stripe_client.py   Creación de links de pago y validación de webhooks
app/payments/webhook_handler.py Traduce eventos de Stripe a cambios en la BD
app/payments/webhook_server.py  Servidor HTTP (/health y /stripe/webhook)
app/storage/db.py               Persistencia SQLite (usuarios, suscripciones)
app/storage/memory.py           Historial de conversación persistente
app/clock.py                    Utilidades de fecha/hora en UTC
scripts/smoke_test.py           Prueba de arranque real (servidor + BD)
tests/                          Tests (pytest)
```

## Puesta en marcha

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Crea una API key gratuita en <https://openrouter.ai/keys>.
3. Copia `.env.example` a `.env` y completa `TELEGRAM_BOT_TOKEN` y
   `OPENROUTER_API_KEY`. Puedes ajustar `OPENROUTER_MODELS` para cambiar
   qué modelos gratuitos usar y en qué orden.
4. Instala dependencias y ejecuta:

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python main.py
   ```

   O con Docker:

   ```bash
   docker build -t telegram-ai .
   docker run --env-file .env -p 8080:8080 telegram-ai
   ```

5. Escríbele a tu bot en Telegram. Puedes mandarle texto o fotos: si le
   envías una imagen (con o sin pie de foto), la analiza y responde sobre
   lo que ve, incluyendo el texto que aparezca en ella.

### Comandos disponibles

| Comando | Qué hace |
| --- | --- |
| `/start`, `/ayuda` | Mensaje de bienvenida |
| `/nuevo` | Borra el historial de la conversación actual |
| `/estado` | Muestra tu plan y mensajes disponibles hoy |
| `/suscribirme` | Envía la factura del plan Premium |
| `/stats` | Solo administradores: usuarios, suscriptores y uso del día |

Para usar `/stats` pon tu ID de Telegram en `ADMIN_TELEGRAM_USER_IDS`
(puedes averiguarlo escribiéndole a [@userinfobot](https://t.me/userinfobot)).

## Activar los pagos

Por defecto `BILLING_ENABLED=false` y el bot es ilimitado para todos. Hay
dos métodos de cobro y basta con configurar uno.

### Opción A: Telegram Stars (recomendada)

Los usuarios pagan con Stars dentro de la propia app de Telegram. **No
necesitas cuenta de comercio, empresa registrada ni que Stripe opere en tu
país** (Stripe, por ejemplo, no admite cobros desde Colombia), así que es
la vía más rápida para empezar a facturar.

1. Pon `BILLING_ENABLED=true`.
2. Pon el precio en `TELEGRAM_STARS_PRICE` (por ejemplo `150`; como
   referencia, 150 Stars ≈ 2-3 USD).
3. Con `TELEGRAM_STARS_SUBSCRIPTION=true` Telegram renueva el cobro solo
   cada 30 días; con `false` es un pago único que da 30 días de Premium.

Cuando el usuario manda `/suscribirme` recibe la factura dentro del chat.
Al pagar, el bot activa su Premium al instante. Los Stars acumulados se
retiran desde [@BotFather](https://t.me/BotFather) (Bot Settings →
Payments).

### Opción B: Stripe

Útil si ya tienes una cuenta de Stripe y quieres cobrar con tarjeta:

1. Crea una cuenta en [Stripe](https://dashboard.stripe.com) y activa los
   pagos de tu país.
2. Crea un **producto con precio recurrente** (por ejemplo 5 USD/mes) y
   copia su ID (`price_...`) en `STRIPE_PRICE_ID`.
3. Copia tu clave secreta (`sk_live_...` o `sk_test_...`) en
   `STRIPE_SECRET_KEY`.
4. El bot expone el endpoint `POST /stripe/webhook`. Necesita ser accesible
   desde internet:
   - En desarrollo: `stripe listen --forward-to localhost:8080/stripe/webhook`
   - En producción: despliega en Render/Railway/Fly y registra
     `https://tu-dominio/stripe/webhook` en
     [Dashboard → Webhooks](https://dashboard.stripe.com/webhooks).
   Suscribe estos eventos: `checkout.session.completed`,
   `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`.
5. Copia el *signing secret* del webhook (`whsec_...`) en
   `STRIPE_WEBHOOK_SECRET`.
6. Pon `BILLING_ENABLED=true` y ajusta `FREE_DAILY_MESSAGES` (mensajes
   gratis al día) y `PREMIUM_PRICE_LABEL` (el texto que ve el usuario).

Cuando un usuario manda `/suscribirme`, el bot genera un link de Stripe
Checkout con su ID de Telegram asociado. Al confirmarse el pago, el webhook
activa su Premium. Si cancela o falla el cobro, vuelve automáticamente al
plan gratuito.

> Los eventos de Stripe se procesan de forma **idempotente** (se guarda el
> `event_id` procesado), así que reintentos de Stripe no duplican nada.

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest                        # tests
ruff check .                  # linter
python scripts/smoke_test.py  # arranque real del servidor
```

## Notas sobre el nivel gratuito de OpenRouter

Los modelos gratuitos tienen límites de uso (aproximadamente 20 peticiones
por minuto y 200 por día) y **qué modelos son gratis cambia con el tiempo**:
modelos que hoy son gratis mañana pasan a ser de pago. Por eso el bot no
depende de una lista fija, sino que descubre los modelos gratuitos vigentes
y los prueba en orden. Si todos fallan a la vez, avisa al usuario en vez de
quedarse colgado.

## Roadmap

- [x] Agente con búsqueda web y lectura de páginas
- [x] Fallback automático entre modelos gratuitos
- [x] Plan gratuito con cuota diaria + Premium (Telegram Stars y Stripe)
- [x] Tests automatizados y linter
- [x] Historial de conversación persistente (sobrevive a reinicios)
- [x] Métricas de uso para el administrador (`/stats`)
- [x] Calculadora (los modelos fallan en aritmética)
- [x] Entender imágenes que envíe el usuario
- [ ] Transcribir audios y notas de voz
- [ ] Lectura de PDFs
- [ ] Conectar otras plataformas (web, WhatsApp, Discord)
