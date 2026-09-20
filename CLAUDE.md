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
- **En OpenRouter, un 403 es "prueba con otro modelo"**, no un error fatal:
  suele significar que ESE modelo o proveedor rechazó la petición
  (moderación, política de datos), no que la petición esté mal formada.
  Está en `RETRYABLE_STATUS_CODES`.
- **Nunca se le reenvía al usuario texto crudo de un modelo que ignoró el
  formato JSON** tras el reintento: el catálogo de modelos "gratis" se
  descubre automáticamente y a veces incluye modelos que no son de chat de
  verdad (p. ej. clasificadores de seguridad), que responden con texto sin
  relación a la pregunta. Ese caso lanza `RespuestaNoConfiableError`
  (`app/ai/agent.py`) y se trata igual que si ningún modelo respondiera:
  se devuelve la cuota y se muestra el mensaje genérico de error.
- **El modelo que falló el formato se excluye en el reintento**
  (`OpenRouterClient.chat(..., exclude_models=...)`): como el cliente
  siempre prueba los modelos en el mismo orden y se detiene en el primero
  que responde con HTTP 200, sin esto el "reintento" volvía a caer en el
  mismo modelo defectuoso en vez de probar otro.
- **`duckduckgo-search` debe mantenerse alineado con `primp`**: versiones
  viejas de esa librería usan cadenas de "impersonate" de navegador (p. ej.
  `chrome_119`) que versiones nuevas de `primp` ya no reconocen y fallan
  con `BuilderError`. Si se actualiza una, revisar la otra.
- **Las peticiones a la API de Wikipedia llevan un `User-Agent`
  descriptivo**: su política de uso responde 403 a quien no lo mande.
- **`chat()` y `VisionService.describe()` tienen un tope total de tiempo**
  (`CHAT_BUDGET_SECONDS` / `VISION_BUDGET_SECONDS`), no solo por petición:
  un modelo "descubierto" puede responder HTTP 200 pero tardar muchísimo
  (o no soltar nunca la respuesta) sin que eso cuente como un error de red.
  Sin ese tope total, probar candidato tras candidato podía consumir por sí
  solo los 180s de tiempo límite de la conversación entera y el usuario se
  quedaba viendo "escribiendo..." varios minutos sin ni un mensaje de error.
- **El modelo a probar se elige según el mensaje, con una heurística de
  texto barata** (`_es_mensaje_simple` en `app/ai/agent.py`), no llamando a
  otro modelo para decidir: charla corta y sin pinta de necesitar búsqueda
  usa `prefer_light=True` (modelos de menor contexto en
  `FreeModelCatalog.get_free_models`, normalmente más rápidos); el resto
  sigue usando los de mayor contexto primero. Si la heurística se
  equivoca, la cadena de respaldo entre modelos sigue funcionando igual.

## Estilo

- Código y comentarios en español, igual que el resto del proyecto.
- Comentar solo el *porqué* cuando no sea evidente, no el *qué*.
- Cada cambio de comportamiento va con su test.
