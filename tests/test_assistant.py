import asyncio

import pytest

from app.ai.openrouter_client import AllModelsFailedError
from app.assistant import (
    MENSAJE_IMAGEN_NO_SOPORTADA,
    SIN_CUOTA,
    Assistant,
    _ConversationLocks,
)
from app.billing.service import BillingService
from app.storage.db import Database
from app.storage.memory import ConversationMemory


class FakeAgent:
    def __init__(self, respuesta="respuesta", error=None):
        self.respuesta = respuesta
        self.error = error
        self.llamadas = []

    async def run(self, history, user_message):
        self.llamadas.append((list(history), user_message))
        if self.error:
            raise self.error
        return self.respuesta


class FakeVision:
    def __init__(self, descripcion="un gato naranja", error=None):
        self.descripcion = descripcion
        self.error = error

    async def describe(self, image_bytes, prompt=None):
        if self.error:
            raise self.error
        return self.descripcion


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


def _assistant(db, agent=None, vision=None, free_daily=10, billing_enabled=True):
    return Assistant(
        agent=agent or FakeAgent(),
        memory=ConversationMemory(db, max_messages=10),
        billing=BillingService(db, free_daily, billing_enabled),
        vision=vision,
    )


@pytest.mark.asyncio
async def test_responde_y_guarda_el_historial(db):
    agent = FakeAgent("¡Hola!")
    assistant = _assistant(db, agent)

    reply = await assistant.handle_text(user_id=1, conversation_id=100, text="hola")

    assert reply.text == "¡Hola!"
    assert not reply.quota_exhausted
    assert agent.llamadas[0][1] == "hola"


@pytest.mark.asyncio
async def test_el_historial_llega_al_agente_en_el_siguiente_turno(db):
    agent = FakeAgent()
    assistant = _assistant(db, agent)

    await assistant.handle_text(1, 100, "primera")
    await assistant.handle_text(1, 100, "segunda")

    historial_segunda_llamada = agent.llamadas[1][0]
    assert [m["content"] for m in historial_segunda_llamada] == ["primera", "respuesta"]


@pytest.mark.asyncio
async def test_avisa_cuando_se_agota_la_cuota(db):
    assistant = _assistant(db, free_daily=1)

    await assistant.handle_text(1, 100, "primera")
    reply = await assistant.handle_text(1, 100, "segunda")

    assert reply.quota_exhausted
    assert reply.text == SIN_CUOTA


@pytest.mark.asyncio
async def test_devuelve_la_cuota_si_fallan_los_modelos(db):
    agent = FakeAgent(error=AllModelsFailedError("sin modelos"))
    assistant = _assistant(db, agent, free_daily=2)

    reply = await assistant.handle_text(1, 100, "hola")

    assert "no te cuenta" in reply.text.lower()
    # La cuota se devolvió: el siguiente mensaje vuelve a consumir del total
    # completo, no de lo que quedaba.
    agent.error = None
    siguiente = await assistant.handle_text(1, 100, "otra")
    assert siguiente.remaining_free_messages == 1


@pytest.mark.asyncio
async def test_un_error_inesperado_no_rompe_ni_gasta_cuota(db):
    agent = FakeAgent(error=RuntimeError("boom"))
    assistant = _assistant(db, agent, free_daily=2)

    reply = await assistant.handle_text(1, 100, "hola")

    assert "error inesperado" in reply.text.lower()
    agent.error = None
    assert (await assistant.handle_text(1, 100, "otra")).remaining_free_messages == 1


@pytest.mark.asyncio
async def test_una_respuesta_fallida_no_entra_al_historial(db):
    agent = FakeAgent(error=RuntimeError("boom"))
    assistant = _assistant(db, agent)

    await assistant.handle_text(1, 100, "hola")
    agent.error = None
    await assistant.handle_text(1, 100, "segunda")

    # La segunda llamada no debe arrastrar el turno fallido.
    assert agent.llamadas[1][0] == []


@pytest.mark.asyncio
async def test_procesa_una_imagen(db):
    agent = FakeAgent("Es un gato")
    assistant = _assistant(db, agent, vision=FakeVision("un gato naranja"))

    reply = await assistant.handle_image(1, 100, b"imagen", caption="¿qué es esto?")

    assert reply.text == "Es un gato"
    contexto = agent.llamadas[0][1]
    assert "un gato naranja" in contexto
    assert "¿qué es esto?" in contexto


@pytest.mark.asyncio
async def test_imagen_sin_pie_de_foto_usa_pregunta_por_defecto(db):
    agent = FakeAgent()
    assistant = _assistant(db, agent, vision=FakeVision())

    await assistant.handle_image(1, 100, b"imagen")

    assert "¿Qué hay en esta imagen?" in agent.llamadas[0][1]


@pytest.mark.asyncio
async def test_sin_servicio_de_vision_avisa_al_usuario(db):
    assistant = _assistant(db, vision=None)

    reply = await assistant.handle_image(1, 100, b"imagen")

    assert reply.text == MENSAJE_IMAGEN_NO_SOPORTADA
    assert not assistant.supports_images


@pytest.mark.asyncio
async def test_si_falla_la_vision_devuelve_la_cuota(db):
    vision = FakeVision(error=AllModelsFailedError("sin visión"))
    assistant = _assistant(db, vision=vision, free_daily=2)

    reply = await assistant.handle_image(1, 100, b"imagen")

    assert "no te cuenta" in reply.text.lower()
    vision.error = None
    assert (await assistant.handle_text(1, 100, "otra")).remaining_free_messages == 1


@pytest.mark.asyncio
async def test_clear_conversation_borra_el_historial(db):
    agent = FakeAgent()
    assistant = _assistant(db, agent)
    await assistant.handle_text(1, 100, "hola")

    await assistant.clear_conversation(100)
    await assistant.handle_text(1, 100, "de nuevo")

    assert agent.llamadas[1][0] == []


@pytest.mark.asyncio
async def test_los_locks_serializan_la_misma_conversacion():
    locks = _ConversationLocks()
    orden = []

    async def tarea(nombre, espera):
        async with locks.acquire("1"):
            orden.append(f"inicio-{nombre}")
            await asyncio.sleep(espera)
            orden.append(f"fin-{nombre}")

    await asyncio.gather(tarea("a", 0.02), tarea("b", 0))

    assert orden == ["inicio-a", "fin-a", "inicio-b", "fin-b"]


def test_los_locks_son_independientes_entre_conversaciones():
    locks = _ConversationLocks()
    assert locks.acquire("1") is not locks.acquire("2")
    assert locks.acquire("1") is locks.acquire("1")


@pytest.mark.asyncio
async def test_una_respuesta_colgada_se_cancela(db):
    """Sin tope, el lock de la conversación quedaría tomado para siempre y
    ese chat dejaría de funcionar."""

    class AgenteColgado:
        async def run(self, history, user_message):
            await asyncio.sleep(10)

    assistant = Assistant(
        agent=AgenteColgado(),
        memory=ConversationMemory(db, max_messages=10),
        billing=BillingService(db, 2, True),
        timeout_seconds=0.05,
    )

    reply = await assistant.handle_text(1, 100, "hola")

    assert "tardando demasiado" in reply.text
    # La cuota se devolvió y la conversación sigue utilizable.
    assert (await assistant.handle_text(1, 100, "otra")).text is not None


@pytest.mark.asyncio
async def test_tras_un_timeout_la_conversacion_sigue_viva(db):
    class AgenteLento:
        def __init__(self):
            self.colgado = True

        async def run(self, history, user_message):
            if self.colgado:
                await asyncio.sleep(10)
            return "por fin"

    agent = AgenteLento()
    assistant = Assistant(
        agent=agent,
        memory=ConversationMemory(db, max_messages=10),
        billing=BillingService(db, 5, True),
        timeout_seconds=0.05,
    )

    await assistant.handle_text(1, 100, "hola")
    agent.colgado = False

    assert (await assistant.handle_text(1, 100, "otra")).text == "por fin"


def _pdf_de_prueba(texto="Contenido del documento"):
    from tests.test_pdf_reader import build_pdf

    return build_pdf([texto])


@pytest.mark.asyncio
async def test_procesa_un_pdf(db):
    agent = FakeAgent("El documento habla de X")
    assistant = _assistant(db, agent)

    reply = await assistant.handle_pdf(
        1, 100, _pdf_de_prueba("Factura por 50000 pesos"), "factura.pdf", "¿cuánto es?"
    )

    assert reply.text == "El documento habla de X"
    contexto = agent.llamadas[0][1]
    assert "factura.pdf" in contexto
    assert "Factura por 50000 pesos" in contexto
    assert "¿cuánto es?" in contexto


@pytest.mark.asyncio
async def test_pdf_sin_pie_de_foto_pide_un_resumen(db):
    agent = FakeAgent()
    assistant = _assistant(db, agent)

    await assistant.handle_pdf(1, 100, _pdf_de_prueba(), "doc.pdf")

    assert "Resume este documento" in agent.llamadas[0][1]


@pytest.mark.asyncio
async def test_un_pdf_ilegible_no_gasta_cuota(db):
    assistant = _assistant(db, free_daily=2)

    reply = await assistant.handle_pdf(1, 100, b"no soy un pdf", "raro.pdf")

    assert "PDF" in reply.text
    assert (await assistant.handle_text(1, 100, "otra")).remaining_free_messages == 1
