from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from app.schemas import Listing


class ListingSource(Protocol):
    def fetch_changed(self, since: datetime) -> Iterable[Listing]: ...  # новые и изменённые
    def fetch_removed(self, since: datetime) -> Iterable[str]: ...      # source_id проданных и снятых
