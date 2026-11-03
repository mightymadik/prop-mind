import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import Listing


class SyntheticJsonlSource:
    """Демо-адаптер: JSONL с синтетикой, поля переименовываются по field_map."""

    def __init__(self, path: str | Path, field_map: dict[str, str]):
        self.path = Path(path)
        self.field_map = field_map

    def fetch_changed(self, since: datetime) -> Iterator[Listing]:
        # ponytail: изменения по mtime всего файла → полная выгрузка; построчно не отслеживаем
        mtime = datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
        if mtime <= since:
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    raw = json.loads(line)
                    yield Listing.model_validate({self.field_map.get(k, k): v for k, v in raw.items()})

    def fetch_removed(self, since: datetime) -> list[str]:
        return []  # из файла ничего не снимается
