"""Extracción de texto de documentos PDF enviados por el usuario.

Permite preguntarle al asistente sobre un PDF (un recibo, un artículo, un
trabajo de la universidad) en vez de tener que copiar y pegar su contenido.

Solo se extrae texto ya presente en el archivo: los PDF escaneados, que en
realidad son imágenes, necesitarían OCR y no se cubren aquí.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

# El texto acaba dentro del prompt, así que hay que dejar sitio para la
# conversación y la respuesta.
MAX_CHARS = 12000
MAX_PAGES = 50


class PdfExtractionError(RuntimeError):
    pass


def extract_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            # Algunos PDF solo llevan permisos, no contraseña: con cadena
            # vacía se abren. Si pide contraseña de verdad, falla.
            try:
                reader.decrypt("")
            except Exception as exc:
                raise PdfExtractionError(
                    "El PDF está protegido con contraseña."
                ) from exc

        paginas = reader.pages[:MAX_PAGES]
        if not paginas:
            raise PdfExtractionError("El PDF no tiene páginas.")

        partes = []
        total = 0
        for numero, pagina in enumerate(paginas, start=1):
            try:
                texto = pagina.extract_text() or ""
            except Exception:
                logger.warning("No se pudo extraer la página %d", numero)
                continue

            texto = texto.strip()
            if not texto:
                continue

            partes.append(f"--- Página {numero} ---\n{texto}")
            total += len(texto)
            if total >= MAX_CHARS:
                break
    except PdfExtractionError:
        raise
    except (PyPdfError, ValueError, OSError) as exc:
        logger.warning("PDF ilegible: %s", exc)
        raise PdfExtractionError("No pude leer el archivo: ¿seguro que es un PDF?") from exc

    if not partes:
        raise PdfExtractionError(
            "El PDF no tiene texto que pueda leer. Si es un documento "
            "escaneado, prueba a enviarme una foto de la página."
        )

    texto_completo = "\n\n".join(partes)
    if len(texto_completo) > MAX_CHARS:
        texto_completo = texto_completo[:MAX_CHARS] + "\n\n[...documento truncado]"

    if len(reader.pages) > MAX_PAGES:
        texto_completo += (
            f"\n\n[El documento tiene {len(reader.pages)} páginas; "
            f"solo se leyeron las primeras {MAX_PAGES}.]"
        )

    return texto_completo
