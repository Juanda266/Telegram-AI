"""Utilidades de fecha/hora en UTC.

Todo el proyecto guarda y compara fechas en UTC con zona horaria explícita.
Se centraliza aquí porque mezclar datetimes "naive" (sin zona) y "aware"
(con zona) hace que Python lance TypeError al compararlos, y porque
`datetime.utcnow()` está obsoleto desde Python 3.12.
"""

import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def today_iso() -> str:
    return utcnow().date().isoformat()


def parse_utc(value: str | None) -> dt.datetime | None:
    """Convierte una fecha ISO a datetime con zona UTC.

    Acepta valores guardados por versiones anteriores sin zona horaria,
    asumiendo que estaban en UTC. Devuelve None si el valor no es parseable.
    """
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed


def from_unix(timestamp: int | None) -> dt.datetime | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, dt.UTC)
