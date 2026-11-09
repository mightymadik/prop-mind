from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from app.schemas import Complex, Listing, Unit


class ListingSource(Protocol):
    def fetch_changed(self, since: datetime) -> Iterable[Listing]: ...  # новые и изменённые
    def fetch_removed(self, since: datetime) -> Iterable[str]: ...  # source_id проданных и снятых


class CatalogSource(Protocol):
    """Новостройки: ЖК и квартиры в них. Проданные квартиры приходят со status='sold', а не удаляются."""

    def fetch_complexes(self, since: datetime) -> Iterable[Complex]: ...  # новые и изменённые ЖК
    def fetch_units(self, since: datetime) -> Iterable[Unit]: ...  # новые и изменённые квартиры
