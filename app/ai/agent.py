"""Agente de IA con capacidad de investigar en la web.

Muchos de los modelos gratuitos de OpenRouter no soportan de forma fiable el
"function calling" nativo de OpenAI, así que en vez de depender de eso se usa
un ciclo estilo ReAct: le pedimos al modelo que responda SIEMPRE en JSON,
indicando si quiere usar una herramienta o si ya tiene la respuesta final.
Esto funciona con prácticamente cualquier modelo de chat, sin importar si
soporta tool calling.
"""

import json
import logging
import re

from app.ai.openrouter_client import OpenRouterClient
from app.ai.tools.calculator import calculate
from app.ai.tools.web_fetch import web_fetch
from app.ai.tools.web_search import web_search
from app.clock import utcnow

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
Eres un asistente virtual en Telegram, útil, honesto y directo, capaz de \
investigar en la web para dar respuestas actualizadas y verificadas (como \
Gemini o Perplexity). Respondes en el mismo idioma en que te escribe el \
usuario (por defecto español).

La fecha y hora actual es {fecha_actual} (UTC). Tenla en cuenta al \
interpretar expresiones como "hoy", "este año" o "lo último", y recuerda \
que tu conocimiento interno puede estar desactualizado respecto a esta \
fecha: ante cualquier duda sobre hechos recientes, busca en la web.

Tienes acceso a estas herramientas:
- web_search(query, max_results?): busca en la web y devuelve título, URL y \
un fragmento de cada resultado.
- web_fetch(url): descarga una página y devuelve su texto principal, para \
leer un resultado con más detalle.
- calculator(expression): evalúa una expresión matemática con precisión.

Usa las de búsqueda cuando la pregunta necesite información actual, hechos \
que puedas no saber con certeza, precios, noticias o eventos recientes, o \
cuando el usuario pida explícitamente que busques algo. No las uses para \
saludos, charla casual o preguntas que ya puedes responder con confianza.

Usa la calculadora SIEMPRE que haga falta una operación aritmética, por \
sencilla que parezca: calcular "de memoria" produce errores.

FORMATO DE RESPUESTA (muy importante): en cada uno de tus turnos debes \
responder ÚNICAMENTE con un objeto JSON, sin texto antes ni después, con \
una de estas dos formas:

1. Para usar una herramienta:
{{"action": "web_search", "input": {{"query": "..."}}}}
{{"action": "web_fetch", "input": {{"url": "..."}}}}
{{"action": "calculator", "input": {{"expression": "..."}}}}

2. Para dar la respuesta final al usuario:
{{"action": "final", "content": "Tu respuesta aquí, en texto plano o \
markdown simple, lista para mostrarse en Telegram."}}

Reglas:
- Nunca mezcles texto fuera del JSON.
- Si buscas y los resultados no alcanzan, puedes buscar de nuevo con otros \
términos o usar web_fetch sobre el resultado más prometedor.
- Cuando ya tengas suficiente información, responde con "final" cuanto \
antes; no repitas búsquedas innecesarias.
- Si citas datos de la web, menciona brevemente la fuente (nombre del sitio \
o URL).
"""


def _system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        fecha_actual=utcnow().strftime("%Y-%m-%d %H:%M")
    )


def _extract_json(raw_text: str) -> dict:
    """Intenta parsear la respuesta del modelo como JSON, siendo tolerante
    con modelos que agregan texto extra o bloques ```json``` alrededor."""
    text = raw_text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No se pudo interpretar la respuesta del modelo: {text[:300]!r}")


def _normalize_history(history: list[dict]) -> list[dict]:
    """Reescribe las respuestas pasadas del bot con el mismo formato JSON que
    le exigimos al modelo.

    El historial guarda las respuestas ya "limpias" (texto para el usuario).
    Si se las devolviéramos tal cual, el modelo vería sus propios turnos
    anteriores incumpliendo el formato que el system prompt le pide, y
    tendería a imitarlos respondiendo en texto plano.
    """
    normalized = []
    for message in history:
        if message.get("role") == "assistant":
            content = json.dumps(
                {"action": "final", "content": message.get("content", "")},
                ensure_ascii=False,
            )
            normalized.append({"role": "assistant", "content": content})
        else:
            normalized.append(message)
    return normalized


class ResearchAgent:
    def __init__(self, client: OpenRouterClient, max_steps: int = 6) -> None:
        self._client = client
        self._max_steps = max_steps

    async def run(self, history: list[dict], user_message: str) -> str:
        messages = [{"role": "system", "content": _system_prompt()}]
        messages.extend(_normalize_history(history))
        messages.append({"role": "user", "content": user_message})

        for step in range(self._max_steps):
            # Si ningún modelo responde se propaga AllModelsFailedError: quien
            # llama necesita distinguir un fallo nuestro de una respuesta real
            # (por ejemplo, para no gastarle la cuota al usuario).
            raw_reply = await self._client.chat(messages)

            try:
                action = _extract_json(raw_reply)
            except ValueError:
                logger.warning("Respuesta no-JSON del modelo, se devuelve tal cual")
                return raw_reply.strip()

            action_type = action.get("action")

            if action_type == "final":
                content = action.get("content", "").strip()
                return content or "No tengo una respuesta para eso."

            if action_type == "web_search":
                tool_input = action.get("input", {})
                query = tool_input.get("query", "")
                max_results = tool_input.get("max_results", 5)
                logger.info("Paso %d: web_search(%r)", step + 1, query)
                results = await web_search(query, max_results)
                observation = json.dumps(results, ensure_ascii=False)

            elif action_type == "web_fetch":
                tool_input = action.get("input", {})
                url = tool_input.get("url", "")
                logger.info("Paso %d: web_fetch(%r)", step + 1, url)
                observation = await web_fetch(url)

            elif action_type == "calculator":
                tool_input = action.get("input", {})
                expression = tool_input.get("expression", "")
                logger.info("Paso %d: calculator(%r)", step + 1, expression)
                observation = calculate(expression)

            else:
                logger.warning("Acción desconocida del modelo: %r", action_type)
                return json.dumps(action, ensure_ascii=False)

            messages.append({"role": "assistant", "content": raw_reply})
            messages.append(
                {
                    "role": "user",
                    "content": f"[Resultado de la herramienta]\n{observation}",
                }
            )

        return (
            "No logré llegar a una respuesta definitiva tras varias "
            "búsquedas. ¿Puedes reformular o precisar tu pregunta?"
        )
