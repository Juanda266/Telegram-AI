# Telegram AI Assistant

Asistente virtual para Telegram con capacidad de **investigar en la web**
(similar a Gemini o Perplexity), pensado para poder ampliarse en el futuro
a otras plataformas además de Telegram.

## Cómo funciona

- El "cerebro" es un modelo de lenguaje servido a través de
  [OpenRouter](https://openrouter.ai), que da acceso gratuito a varios
  modelos (Llama, Gemini, DeepSeek, Qwen, Mistral, etc.) con una sola API
  key. El bot está configurado con **una lista de modelos gratuitos**: si
  el que está usando se queda sin cupo o falla, prueba automáticamente con
  el siguiente de la lista.
- El agente sigue un ciclo tipo *ReAct*: en cada turno decide si necesita
  buscar en la web (`web_search`), leer una página completa (`web_fetch`)
  o si ya puede responder (`final`). Esto funciona con modelos gratuitos
  que no soportan "function calling" nativo.
- La búsqueda web usa DuckDuckGo (no requiere API key).
- El historial de conversación se guarda en memoria, por chat de Telegram.

## Estructura del proyecto

```
main.py                     Punto de entrada
app/config.py                Carga de configuración (.env)
app/telegram_bot.py          Handlers de Telegram
app/storage/memory.py        Historial de conversación en RAM
app/ai/agent.py               Bucle del agente (decide buscar o responder)
app/ai/openrouter_client.py   Cliente HTTP a OpenRouter con fallback de modelos
app/ai/tools/web_search.py    Herramienta de búsqueda (DuckDuckGo)
app/ai/tools/web_fetch.py     Herramienta para leer el contenido de una URL
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
   docker run --env-file .env telegram-ai
   ```

5. Escríbele a tu bot en Telegram. Comandos disponibles: `/start`,
   `/ayuda`, `/nuevo` (borra el historial de la conversación actual).

## Notas sobre el nivel gratuito

Los modelos `...:free` de OpenRouter tienen límites de uso (por minuto y
por día) que pueden cambiar sin aviso. Por eso el cliente prueba varios
modelos en orden: si todos fallan al mismo tiempo, el bot avisa al usuario
en vez de quedarse colgado.

## Próximos pasos / roadmap

- Restringir el uso a ciertos usuarios (`ALLOWED_TELEGRAM_USER_IDS`).
- Persistir el historial en disco (SQLite) en vez de solo en memoria.
- Añadir más herramientas (calculadora, lectura de PDFs/imágenes, etc.).
- Extraer la lógica del agente a un servicio reutilizable para conectar
  otras plataformas además de Telegram (web, WhatsApp, Discord...).
