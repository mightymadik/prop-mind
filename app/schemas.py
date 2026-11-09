from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from app.config import AppConfig

Finishing = Literal["rough", "pre_finish", "finished"]  # черновая, предчистовая, чистовая
UnitStatus = Literal["free", "reserved", "sold"]
Windows = Literal["yard", "street", "both"]  # окна во двор, на улицу, на две стороны
View = Literal["mountains", "city", "park", "river", "none"]  # river — река или набережная (Астана, Есильский)
Side = Literal["north", "east", "south", "west"]


class Complex(BaseModel):
    """Жилой комплекс: всё смысловое (описание, инфраструктура) — здесь, на уровне ЖК."""

    source_id: str
    city: str
    district: str
    name: str
    developer: str
    housing_class: str  # ключ из config.classes
    completion_year: int
    completion_quarter: int = Field(ge=1, le=4)
    delivered: bool = False  # дом сдан
    features: list[str] = []  # ключи из config.complex_features
    payment: list[str] = []  # ключи из config.payment
    description: str


class Unit(BaseModel):
    """Квартира в ЖК: только структурные поля, своего текста нет."""

    source_id: str
    complex_id: str  # source_id ЖК
    rooms: int
    area_m2: float
    floor: int
    floors_total: int
    price_kzt: int
    finishing: Finishing
    status: UnitStatus = "free"
    windows: Windows
    view: View
    orientation: list[Side]  # стороны света, куда выходят окна


class SearchFilters(BaseModel):
    """Фильтры поиска новостроек. Условия ЖК и квартир; списки — «любой из», пустой — фильтра нет (без null:
    у API лимит 16 nullable-полей в схеме). Словари — из config.catalog (Literal только в схеме для LLM).
    Квартиры всегда только свободные."""

    # ЖК
    city: str | None = Field(None, description="Город; по району можно вывести, угадывать нельзя")
    districts: list[str] = Field(default_factory=list, description="Районы без слова 'район'")
    classes: list[str] = Field(default_factory=list, description="Класс жилья")
    completion_max_year: int | None = Field(None, description="Сдача не позже этого года")
    completion_max_quarter: int | None = Field(None, description="…и квартала (1–4); год без квартала — 4")
    delivered_only: bool = Field(False, description="Только сданные дома")
    features: list[str] = Field(default_factory=list, description="Инфраструктура ЖК — нужны все")
    payment: list[str] = Field(default_factory=list, description="Способы покупки — нужны все")
    # квартира
    rooms: list[int] = Field(
        default_factory=list, description="Допустимое число комнат. 'Евродвушка' = [2], 'студия' = [1]"
    )
    price_min_kzt: int | None = Field(None, description="Минимальная цена квартиры в тенге")
    price_max_kzt: int | None = Field(None, description="Максимальная цена квартиры в тенге")
    area_min_m2: float | None = None
    area_max_m2: float | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    not_first_floor: bool = False
    not_last_floor: bool = False
    finishing: list[str] = Field(default_factory=list, description="Отделка")
    windows: list[str] = Field(default_factory=list, description="Куда выходят окна")
    views: list[str] = Field(default_factory=list, description="Вид из окна")
    orientations: list[str] = Field(default_factory=list, description="Стороны света; подходит квартира с любой из них")
    semantic_query: str = Field("", description="То, что не выражается полями: 'тихий двор', 'для семьи с детьми'")
    needs_clarification: str | None = Field(
        None, description="Вопрос пользователю, если запрос невыполним или слишком размыт"
    )


def build_parser_schema(cfg: AppConfig) -> type[SearchFilters]:
    """Подкласс SearchFilters только для LLM: словари — Literal из конфига, все поля обязательны
    (модель явно решает по каждому полю: иначе молча пропускает ключи)."""
    cat = cfg.catalog

    def lit(values) -> Any:
        return Literal[tuple(values)]  # type: ignore[valid-type]

    narrowed = {
        "city": lit(cfg.cities) | None,
        "districts": list[lit(dict.fromkeys(d for ds in cfg.districts.values() for d in ds))],
        "classes": list[lit(cat.classes)],
        "features": list[lit(cat.complex_features)],
        "payment": list[lit(cat.payment)],
        "finishing": list[lit(cat.finishing)],
        "windows": list[lit(cat.windows)],
        "views": list[lit(cat.views)],
        "orientations": list[lit(cat.orientations)],
    }
    fields = {
        name: (narrowed.get(name, f.annotation), Field(..., description=f.description))
        for name, f in SearchFilters.model_fields.items()
    }
    return create_model("SearchFilters", __base__=SearchFilters, **fields)
