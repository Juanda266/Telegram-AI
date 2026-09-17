import pytest

from app.ai.tools.calculator import calculate


@pytest.mark.parametrize(
    ("expresion", "esperado"),
    [
        ("2 + 2", "4"),
        ("10 / 4", "2.5"),
        ("(1250 * 1.19)", "1487.5"),
        ("2 ** 10", "1024"),
        ("17 % 5", "2"),
        ("17 // 5", "3"),
        ("-5 + 3", "-2"),
        ("sqrt(144)", "12"),
        ("round(3.14159, 2)", "3.14"),
        ("max(3, 9, 1)", "9"),
        ("abs(-7)", "7"),
    ],
)
def test_calculos_correctos(expresion, esperado):
    assert calculate(expresion) == esperado


def test_usa_constantes_matematicas():
    assert calculate("round(pi, 4)") == "3.1416"


def test_resultado_entero_no_muestra_decimales():
    """4.0 se muestra como '4': un decimal sobrante se ve como un error."""
    assert calculate("8 / 2") == "4"


@pytest.mark.parametrize(
    "expresion",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "exec('x=1')",
        "[].__class__",
        "lambda: 1",
    ],
)
def test_rechaza_codigo_arbitrario(expresion):
    """La expresión viene del modelo, que a su vez repite lo que escribe un
    usuario: jamás debe poder ejecutar código."""
    resultado = calculate(expresion)
    assert resultado.startswith("No se pudo calcular")


def test_rechaza_exponentes_enormes():
    """Sin tope, 9**9**9 congelaría el proceso entero."""
    assert calculate("9 ** 9 ** 9").startswith("No se pudo calcular")


def test_division_por_cero_no_revienta():
    assert calculate("1 / 0").startswith("No se pudo calcular")


def test_expresion_invalida_no_revienta():
    assert calculate("2 +").startswith("No se pudo calcular")


def test_nombre_desconocido_se_rechaza():
    assert "desconocido" in calculate("variable_rara + 1")
