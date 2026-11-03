import psycopg

from app.config import get_settings


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url)
