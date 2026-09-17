import io

import pytest

from app.ai.tools.pdf_reader import MAX_PAGES, PdfExtractionError, extract_text


def build_pdf(paginas: list[str]) -> bytes:
    """Construye un PDF mínimo pero válido, sin dependencias extra.

    Se generan PDFs de verdad (en vez de simular pypdf) para comprobar que
    la extracción funciona sobre archivos reales.
    """
    objetos = []
    n_pag = len(paginas)
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(n_pag))
    objetos.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objetos.append(f"<</Type/Pages/Kids[{kids}]/Count {n_pag}>>".encode())
    objetos.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    for texto in paginas:
        contenido = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode()
        idx_contenido = len(objetos) + 2
        objetos.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            f"/Contents {idx_contenido} 0 R/Resources<</Font<</F1 3 0 R>>>>>>".encode()
        )
        objetos.append(
            b"<</Length %d>>stream\n" % len(contenido) + contenido + b"\nendstream"
        )

    salida = io.BytesIO()
    salida.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objetos, start=1):
        offsets.append(salida.tell())
        salida.write(b"%d 0 obj" % i + obj + b"endobj\n")
    xref = salida.tell()
    salida.write(b"xref\n0 %d\n" % (len(objetos) + 1))
    salida.write(b"0000000000 65535 f \n")
    for off in offsets:
        salida.write(b"%010d 00000 n \n" % off)
    salida.write(
        b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF"
        % (len(objetos) + 1, xref)
    )
    return salida.getvalue()


def test_extrae_el_texto_de_una_pagina():
    texto = extract_text(build_pdf(["Contenido de prueba"]))
    assert "Contenido de prueba" in texto


def test_marca_el_numero_de_pagina():
    texto = extract_text(build_pdf(["Primera", "Segunda"]))
    assert "--- Página 1 ---" in texto
    assert "--- Página 2 ---" in texto
    assert texto.index("Primera") < texto.index("Segunda")


def test_avisa_si_el_documento_es_muy_largo():
    texto = extract_text(build_pdf([f"Pagina {i}" for i in range(MAX_PAGES + 5)]))
    assert f"solo se leyeron las primeras {MAX_PAGES}" in texto


def test_pdf_sin_texto_se_explica_al_usuario():
    """Los PDF escaneados son imágenes: hay que decirle qué hacer, no fallar
    con un error técnico."""
    with pytest.raises(PdfExtractionError, match="escaneado"):
        extract_text(build_pdf([""]))


def test_archivo_que_no_es_pdf():
    with pytest.raises(PdfExtractionError, match="seguro que es un PDF"):
        extract_text(b"esto es un txt disfrazado")


def test_archivo_vacio():
    with pytest.raises(PdfExtractionError):
        extract_text(b"")
