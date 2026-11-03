from typing import Literal

from pydantic import BaseModel

DealType = Literal["sale", "rent"]
Condition = Literal["rough", "pre_finish", "renovated", "designer"]


class Listing(BaseModel):
    """Нормализованный объект: то, что любой ListingSource отдаёт ядру."""

    source_id: str
    city: str
    district: str | None = None
    complex_name: str | None = None
    deal_type: DealType
    rooms: int | None = None
    area_m2: float | None = None
    floor: int | None = None
    floors_total: int | None = None
    year_built: int | None = None
    condition: Condition | None = None
    price_kzt: int
    features: list[str] = []
    description: str
