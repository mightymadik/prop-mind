import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.schemas import Complex, Unit


class JsonlReader:
    """JSONL с синтетикой: строки → Pydantic-модели, поля переименовываются по field_map."""

    def __init__(self, path: str | Path, field_map: dict[str, str]):
        self.path = Path(path)
        self.field_map = field_map

    def fetch_raw[T: BaseModel](self, since: datetime, model: type[T]) -> Iterator[T]:
        # ponytail: изменения по mtime всего файла → полная выгрузка; построчно не отслеживаем
        mtime = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        if mtime <= since:
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    raw = json.loads(line)
                    yield model.model_validate({self.field_map.get(k, k): v for k, v in raw.items()})


class SyntheticCatalogSource:
    """Демо-адаптер новостроек: два JSONL — ЖК и квартиры, поля переименовываются по field_map."""

    def __init__(self, complexes_path: str | Path, units_path: str | Path, field_map: dict[str, str]):
        self.complexes = JsonlReader(complexes_path, field_map)
        self.units = JsonlReader(units_path, field_map)

    def fetch_complexes(self, since: datetime) -> Iterator[Complex]:
        yield from self.complexes.fetch_raw(since, Complex)

    def fetch_units(self, since: datetime) -> Iterator[Unit]:
        yield from self.units.fetch_raw(since, Unit)
