"""Применяет db/migrations/*.sql по порядку. Каждый файл идемпотентен — повторный запуск безопасен.

    python scripts/migrate.py

Новая база получает схему из db/schema.sql (docker initdb); миграции — для уже созданной.
"""

from pathlib import Path

from app.db import connect

MIGRATIONS = Path("db/migrations")


def main() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    with connect() as conn:
        for f in files:
            conn.execute(f.read_text(encoding="utf-8"))
            print(f"applied {f.name}")
        conn.commit()


if __name__ == "__main__":
    main()
