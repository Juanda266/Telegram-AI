# Telegram AI Assistant — notas para trabajar en el proyecto

Asistente de IA para Telegram que investiga en la web, con plan gratuito
limitado y suscripción Premium de pago. Rama de trabajo:
`claude/peaceful-wright-ekaku1`.

## Comandos

```bash
pip install -r requirements-dev.txt
pytest                        # tests (así los ejecuta el CI)
ruff check .                  # linter
python scripts/smoke_test.py  # arranque real: servidor HTTP + BD
```

Los tres se ejecutan en CI (`.github/workflows/ci.yml`). Ejecútalos antes
de cada commit.

## Arquitectura

- `app/assistant.py` — cuotas, historial, errores y tiempos límite. **No
  sabe nada de Telegram**: es el punto de entrada para añadir otras
  plataformas.
- `app/telegram_bot.py` — adaptador de Telegram (comandos, indicador de
  escritura, envío de respuestas). Debe seguir siendo delgado.
- `app/ai/agent.py` — ciclo ReAct: el modelo responde siempre en JSON
  diciendo qué herramienta usar o cuál es la respuesta final.
- `app/ai/openrouter_client.py` + `model_catalog.py` — prueba modelos en
  orden hasta que uno responde, y descubre por API los que son gratis.
- `app/ai/tools/` — búsqueda web, lectura de páginas, calculadora y PDFs.
- `app/payments/` — métodos de pago intercambiables tras el contrato de
  `base.py`: Telegram Stars, Wompi, PayPal y Stripe. Los que no tengan
  credenciales no se le ofrecen al usuario.
- `app/storage/db.py` — SQLite: usuarios, suscripciones, historial.

## Decisiones importantes (no deshacer sin motivo)

- **No se usa function calling nativo**: muchos modelos gratuitos no lo
  soportan bien. De ahí el protocolo JSON del system prompt.
- **La lista de modelos no se fija a mano**: OpenRouter rota cada pocas
  semanas qué modelos son gratis, y una lista fija deja el bot mudo. Por
  eso `openrouter/free` + descubrimiento automático.
- **Se pueden combinar varios métodos de pago**: Stripe no admite cobros
  desde Colombia, así que las opciones reales allí son Telegram Stars,
  Wompi y PayPal.
- **Todo evento de pago se verifica antes de conceder nada**: los webhooks
  son direcciones públicas. Y se procesan de forma idempotente, porque las
  pasarelas reintentan.
- **En PayPal, las renovaciones llegan como `PAYMENT.SALE.COMPLETED`**,
  no como `BILLING.SUBSCRIPTION.ACTIVATED`, y no traen el ID de Telegram:
  hay que resolver al usuario por el ID de la suscripción guardado.
- **La calculadora nunca usa `eval()`**: la expresión viene, en última
  instancia, de lo que escribe un usuario.
- **`web_fetch` bloquea direcciones internas** (SSRF) y el contenido de la
  web se le entrega al modelo marcado como no confiable (inyección de
  prompt). Mantener ambas protecciones.
- **Si el fallo es nuestro, se devuelve la cuota** al usuario
  (`billing.refund`). No cobrarle por nuestros errores.
- **Fechas siempre vía `app/clock.py`**, en UTC y con zona horaria
  explícita: mezclar naive y aware lanza `TypeError`.
- **Las columnas nuevas de SQLite se añaden en `_COLUMNAS_NUEVAS`**:
  `CREATE TABLE IF NOT EXISTS` no toca las bases ya creadas, así que sin
  esa migración quien ya tuviera el bot corriendo perdería datos.

## Estilo

- Código y comentarios en español, igual que el resto del proyecto.
- Comentar solo el *porqué* cuando no sea evidente, no el *qué*.
- Cada cambio de comportamiento va con su test.
