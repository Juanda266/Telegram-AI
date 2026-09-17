FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directorio por defecto de la base SQLite; en producción conviene montar
# aquí un volumen para no perder las suscripciones al redesplegar.
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "main.py"]
