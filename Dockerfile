FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directorio de la base SQLite; en producción conviene montar aquí un
# volumen para no perder las suscripciones al redesplegar.
RUN mkdir -p /app/data

# El bot no necesita privilegios: si alguien lograra ejecutar código a
# través de él, no debería poder tocar el resto del sistema.
RUN useradd --create-home --uid 1000 bot && chown -R bot:bot /app
USER bot

EXPOSE 8080

# Permite al orquestador reiniciar el contenedor si el bot deja de responder.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health').read()"

CMD ["python", "main.py"]
