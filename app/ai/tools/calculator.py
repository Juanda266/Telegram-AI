"""Calculadora para el agente.

Los modelos de lenguaje se equivocan con frecuencia en aritmética, sobre
todo con números grandes o varios pasos encadenados, y los modelos
gratuitos más aún. Delegar las cuentas a Python evita respuestas mal
calculadas dichas con total seguridad.

La expresión se evalúa recorriendo el árbol sintáctico y aceptando solo
operaciones aritméticas: NUNCA se usa eval(), que permitiría ejecutar
código arbitrario enviado por un usuario a través del modelo.
"""

import ast
import logging
import math
import operator

logger = logging.getLogger(__name__)

_OPERADORES_BINARIOS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_OPERADORES_UNARIOS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCIONES = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
}

_CONSTANTES = {"pi": math.pi, "e": math.e, "tau": math.tau}

# Sin este tope, una expresión como 9**9**9 congelaría el proceso entero
# intentando calcular un número con miles de millones de dígitos.
_EXPONENTE_MAXIMO = 1000


class CalculationError(ValueError):
    pass


def _evaluar(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Constant):
        if isinstance(nodo.value, bool) or not isinstance(nodo.value, int | float):
            raise CalculationError("Solo se admiten números.")
        return nodo.value

    if isinstance(nodo, ast.BinOp):
        operacion = _OPERADORES_BINARIOS.get(type(nodo.op))
        if operacion is None:
            raise CalculationError("Operación no permitida.")
        izquierda, derecha = _evaluar(nodo.left), _evaluar(nodo.right)
        if isinstance(nodo.op, ast.Pow) and abs(derecha) > _EXPONENTE_MAXIMO:
            raise CalculationError("El exponente es demasiado grande.")
        return operacion(izquierda, derecha)

    if isinstance(nodo, ast.UnaryOp):
        operacion = _OPERADORES_UNARIOS.get(type(nodo.op))
        if operacion is None:
            raise CalculationError("Operación no permitida.")
        return operacion(_evaluar(nodo.operand))

    if isinstance(nodo, ast.Name):
        if nodo.id not in _CONSTANTES:
            raise CalculationError(f"Nombre desconocido: {nodo.id}")
        return _CONSTANTES[nodo.id]

    if isinstance(nodo, ast.Call):
        if not isinstance(nodo.func, ast.Name) or nodo.func.id not in _FUNCIONES:
            raise CalculationError("Función no permitida.")
        if nodo.keywords:
            raise CalculationError("No se admiten argumentos con nombre.")
        argumentos = [_evaluar(arg) for arg in nodo.args]
        return _FUNCIONES[nodo.func.id](*argumentos)

    if isinstance(nodo, ast.Tuple | ast.List):
        return [_evaluar(elemento) for elemento in nodo.elts]

    raise CalculationError("Expresión no permitida.")


def calculate(expression: str) -> str:
    try:
        arbol = ast.parse(expression, mode="eval")
        resultado = _evaluar(arbol.body)
    except CalculationError as exc:
        return f"No se pudo calcular: {exc}"
    except SyntaxError:
        return "No se pudo calcular: la expresión no es válida."
    except (ArithmeticError, ValueError, TypeError) as exc:
        return f"No se pudo calcular: {exc}"

    if isinstance(resultado, float) and resultado.is_integer():
        return str(int(resultado))
    return str(resultado)
