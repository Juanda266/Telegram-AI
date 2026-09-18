# Telegram AI Assistant

Asistente virtual para Telegram con capacidad de **investigar en la web**
(similar a Gemini o Perplexity), con **plan gratuito y suscripción de pago**
por varios medios (Telegram Stars, Wompi, PayPal o Stripe), pensado para
poder ampliarse en el futuro a otras plataformas además de Telegram.

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
- La lógica del asistente (cuotas, historial, manejo de errores) vive en
  `app/assistant.py`, separada de Telegram. Añadir web, WhatsApp o Discord
  consiste en escribir un adaptador delgado, sin reimplementar nada de eso.
- Se pueden combinar **varios métodos de pago** y el usuario elige el que
  prefiera: Telegram Stars, Wompi (Nequi, PSE, Daviplata, efectivo),
  PayPal y Stripe. Nunca tocamos datos de tarjeta, y el Premium se activa
  y se revoca solo en todos los casos.

## Estructura del proyecto

```
main.py                        Punto de entrada (bot + servidor de webhooks)
app/config.py                   Carga y validación de configuración (.env)
app/assistant.py                Lógica del asistente, independiente de plataforma
app/telegram_bot.py             Adaptador de Telegram (handlers y comandos)
app/ai/agent.py                 Bucle del agente (decide buscar o responder)
app/ai/openrouter_client.py     Cliente a OpenRouter con fallback entre modelos
app/ai/model_catalog.py         Descubre automáticamente los modelos gratis vigentes
app/ai/vision.py                Lectura de imágenes con modelos de visión gratuitos
app/ai/rate_limiter.py          Respeta la cuota de peticiones por minuto
app/ai/tools/web_search.py      Herramienta de búsqueda (DuckDuckGo)
app/ai/tools/web_fetch.py       Herramienta para leer el contenido de una URL
app/ai/tools/calculator.py      Calculadora segura (sin eval) para el agente
app/ai/tools/pdf_reader.py      Extracción de texto de documentos PDF
app/billing/service.py          Cuotas del plan gratuito y estado Premium
app/payments/base.py            Contrato común de los métodos de pago
app/payments/telegram_stars.py  Cobros con Telegram Stars (sin cuenta de comercio)
app/payments/wompi.py           Wompi: Nequi, PSE, Daviplata, tarjeta y efectivo
app/payments/paypal.py          Suscripciones con PayPal
app/payments/stripe_client.py   Creación de links de pago y validación de webhooks
app/payments/webhook_handler.py Traduce los eventos de pago a cambios en la BD
app/payments/webhook_server.py  Servidor HTTP (/health y webhooks de cada pasarela)
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

5. Escríbele a tu bot en Telegram. En chats privados responde a todo; en
   grupos, solo si lo mencionas (`@tubot ...`) o respondes a un mensaje
   suyo. Entiende:
   - **Texto:** preguntas normales; busca en la web cuando hace falta.
   - **Fotos:** las analiza y responde sobre lo que ve, incluido el texto
     que aparezca en ellas.
   - **PDFs:** extrae su contenido y puedes preguntarle sobre el documento
     (mándalo con un pie de foto para pedirle algo concreto).

### Comandos disponibles

| Comando | Qué hace |
| --- | --- |
| `/start`, `/ayuda` | Mensaje de bienvenida |
| `/nuevo` | Borra el historial de la conversación actual |
| `/estado` | Muestra tu plan y mensajes disponibles hoy |
| `/suscribirme` | Muestra los métodos de pago disponibles |
| `/borrar_datos` | Borra todo lo que el bot guarda sobre ti |
| `/stats` | Solo administradores: usuarios, suscriptores y uso del día |
| `/regalar <id> [días]` | Solo administradores: da Premium a alguien sin cobrarle |

Para usar `/stats` pon tu ID de Telegram en `ADMIN_TELEGRAM_USER_IDS`
(puedes averiguarlo escribiéndole a [@userinfobot](https://t.me/userinfobot)).

## Activar los pagos

Por defecto `BILLING_ENABLED=false` y el bot es ilimitado para todos.

Hay cuatro métodos de cobro y **puedes activar los que quieras a la vez**:
cuando el usuario manda `/suscribirme`, ve un botón por cada método
disponible y elige el que le convenga.

**Los métodos son independientes.** Basta con configurar uno: los que dejes
en blanco simplemente no se le ofrecen a nadie, sin más. Si solo tienes
PayPal, rellena sus variables y listo. Eso sí, si dejas un proveedor *a
medias* (unas variables sí y otras no), el bot te avisa al arrancar en vez
de fallar cuando alguien intente pagar.

Al arrancar, el bot escribe en el log qué métodos quedaron activos, para
que puedas comprobarlo de un vistazo.

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

### Opción B: Wompi (la mejor para cobrar en Colombia)

Wompi es la pasarela de Bancolombia. Acepta **Nequi, PSE, Daviplata, botón
Bancolombia, tarjetas y efectivo** (Efecty, Baloto), que es como paga la
mayoría de la gente en Colombia, sin necesidad de tarjeta internacional.
Comisión aproximada: 2,65% + $700 + IVA.

1. Regístrate en <https://comercios.wompi.co> y copia tus llaves.
2. Rellena `WOMPI_PUBLIC_KEY`, `WOMPI_INTEGRITY_SECRET`,
   `WOMPI_EVENTS_SECRET` y `WOMPI_AMOUNT` (el precio, p. ej. `20000`).
3. En el panel de Wompi, registra la URL de eventos:
   `https://tu-dominio/wompi/webhook`.

Wompi cobra pagos únicos: cada pago aprobado concede 30 días de Premium.

### Opción C: PayPal

Cobra en cualquier país y es lo más rápido si ya tienes cuenta. **Ten en
cuenta las comisiones**: recibir dinero en Colombia por PayPal sale bastante
más caro que Wompi (comisión de recepción, margen al convertir a pesos y
comisión de retiro), así que si más adelante consigues Wompi, conviene
ponerlo como principal y dejar PayPal como alternativa.

1. Necesitas una **cuenta de negocio** de PayPal (crearla es gratis) con
   la identidad verificada.
2. En <https://developer.paypal.com> → *Apps & Credentials*, crea una app
   y copia `PAYPAL_CLIENT_ID` y `PAYPAL_CLIENT_SECRET`.
3. Crea un **producto y un plan de suscripción** con el precio mensual que
   quieras cobrar, y copia el ID del plan (`P-...`) en `PAYPAL_PLAN_ID`.
4. En *Webhooks*, crea uno apuntando a `https://tu-dominio/paypal/webhook`
   suscrito a los eventos `BILLING.SUBSCRIPTION.*`, y copia su ID en
   `PAYPAL_WEBHOOK_ID`.
5. Pon `BILLING_ENABLED=true` y ajusta `PREMIUM_PRICE_LABEL` al precio real.

Para probarlo sin mover dinero, pon `PAYPAL_SANDBOX=true` y usa las
credenciales y cuentas de prueba del entorno *sandbox*.

### Opción D: Stripe

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

> Los eventos de todas las pasarelas se procesan de forma **idempotente**
> (se guarda el identificador ya procesado), así que los reintentos no
> conceden Premium de más. Y **ningún evento se acepta sin verificar su
> firma**: estas direcciones son públicas, y sin esa comprobación
> cualquiera podría regalarse el Premium con una simple petición.

## Seguridad

Aspectos tenidos en cuenta, por si amplías el proyecto:

- **SSRF:** `web_fetch` resuelve el dominio y rechaza direcciones privadas,
  de loopback, link-local y reservadas (incluida `169.254.169.254`, los
  metadatos de la nube), también tras seguir redirecciones. La URL la elige
  el modelo a partir de lo que escribe el usuario, así que no es confiable.
- **Inyección de prompt:** el contenido de las páginas se le entrega al
  modelo envuelto y marcado como datos no confiables, con instrucciones
  explícitas de no obedecer lo que diga.
- **Ejecución de código:** la calculadora evalúa recorriendo el árbol
  sintáctico, nunca con `eval()`.
- **Pagos:** los webhooks de Stripe se validan por firma y se procesan de
  forma idempotente; nunca se manejan datos de tarjeta.
- **Datos personales:** `/borrar_datos` elimina el historial y el registro
  de uso de quien lo pida.

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
- [x] Lectura de documentos PDF
- [ ] Transcribir audios y notas de voz
- [x] Lógica separada de Telegram, lista para otras plataformas
- [ ] Conectar otras plataformas (web, WhatsApp, Discord)
