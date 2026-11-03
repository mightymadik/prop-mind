from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.config import AppConfig

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


class SearchFilters(BaseModel):
    """Фильтры поиска. В коде — str/list[str]; Literal из конфига только в build_parser_schema."""

    city: str | None = Field(None, description="Город; по району можно вывести, угадывать нельзя")
    deal_type: DealType = "sale"
    rooms: list[int] | None = Field(None, description="Допустимое число комнат. 'Евродвушка' = [2], 'студия' = [1]")
    price_min_kzt: int | None = Field(None, description="Минимальная цена в тенге")
    price_max_kzt: int | None = Field(None, description="Максимальная цена в тенге; для аренды — в месяц")
    area_min_m2: float | None = None
    districts: list[str] | None = Field(None, description="Районы без слова 'район'")
    conditions: list[Condition] | None = None
    features: list[str] = Field(default_factory=list)
    semantic_query: str = Field("", description="То, что не выражается полями: 'тихий двор', 'вид на горы'")
    needs_clarification: str | None = Field(
        None, description="Вопрос пользователю, если запрос невыполним или слишком размыт"
    )


def build_parser_schema(cfg: AppConfig) -> type[SearchFilters]:
    """Подкласс SearchFilters только для LLM.

    - город, районы и признаки — Literal из конфига;
    - все поля обязательны (без default): модель явно решает по каждому полю,
      а «нет фильтра» пишет как null / [] / "". Иначе она молча пропускает ключи.
    """
    City = Literal[tuple(cfg.cities)]  # type: ignore[valid-type]
    District = Literal[tuple(dict.fromkeys(d for ds in cfg.districts.values() for d in ds))]  # type: ignore[valid-type]
    Feature = Literal[tuple(cfg.features)]  # type: ignore[valid-type]
    narrowed = {"city": City | None, "districts": list[District] | None, "features": list[Feature]}
    fields = {
        name: (narrowed.get(name, f.annotation), Field(..., description=f.description))
        for name, f in SearchFilters.model_fields.items()
    }
    return create_model("SearchFilters", __base__=SearchFilters, **fields)
