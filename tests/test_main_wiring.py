"""Comprobaciones sobre el arranque: fallos aquí dejan el bot inservible
aunque todos los módulos funcionen por separado."""

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "main.py"


def _llamada_start_polling() -> ast.Call:
    arbol = ast.parse(MAIN.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "start_polling"
        ):
            return nodo
    raise AssertionError("main.py ya no llama a start_polling")


def _allowed_updates() -> list[str]:
    llamada = _llamada_start_polling()
    for keyword in llamada.keywords:
        if keyword.arg == "allowed_updates":
            return [elemento.value for elemento in keyword.value.elts]
    raise AssertionError("start_polling ya no recibe allowed_updates")


def test_pide_los_updates_de_pago():
    """Sin 'pre_checkout_query', Telegram no avisa de las compras y ningún
    pago con Stars llega a completarse: el bot parecería funcionar pero no
    cobraría nada."""
    assert "pre_checkout_query" in _allowed_updates()


def test_pide_los_updates_de_mensajes():
    assert "message" in _allowed_updates()
