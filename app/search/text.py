from app.config import CatalogConfig, ModerationConfig
from app.moderation import index_description
from app.schemas import Complex, Listing

CONDITION_TEXT = {
    "rough": "черновая отделка",
    "pre_finish": "предчистовая отделка",
    "renovated": "с ремонтом",
    "designer": "дизайнерский ремонт",
}
DEAL_TEXT = {"sale": "продажа", "rent": "аренда"}


def build_search_text(
    listing: Listing, feature_labels: dict[str, str], moderation: ModerationConfig | None = None
) -> str:
    """Contextual retrieval: поля объекта текстом перед описанием. Набитое описание (moderation) не попадает."""
    parts = [
        f"{listing.rooms}-комнатная квартира" if listing.rooms else "квартира",
        DEAL_TEXT[listing.deal_type],
        f"{listing.area_m2:g} м²" if listing.area_m2 else None,
        f"{listing.floor}/{listing.floors_total} этаж" if listing.floor and listing.floors_total else None,
        f"{listing.year_built} год постройки" if listing.year_built else None,
        f"ЖК {listing.complex_name}" if listing.complex_name else None,
        f"{listing.district} район" if listing.district else None,
        listing.city,
        CONDITION_TEXT.get(listing.condition) if listing.condition else None,
        *(feature_labels.get(f, f) for f in listing.features),
    ]
    desc = index_description(listing.description, moderation) if moderation else listing.description
    return ", ".join(p for p in parts if p) + ". " + desc


def build_complex_text(c: Complex, cat: CatalogConfig, moderation: ModerationConfig | None = None) -> str:
    """Contextual retrieval для ЖК: поля текстом перед описанием. Квартиры в текст не входят — их условия
    проверяют фильтры."""
    status = "дом сдан" if c.delivered else f"сдача {c.completion_quarter} квартал {c.completion_year} года"
    parts = [
        f"ЖК {c.name}",
        "новостройка",
        cat.classes.get(c.housing_class, c.housing_class),
        f"{c.district} район",
        c.city,
        f"застройщик {c.developer}",
        status,
        *(cat.complex_features.get(x, x) for x in c.features),
        *(cat.payment.get(x, x) for x in c.payment),
    ]
    desc = index_description(c.description, moderation) if moderation else c.description
    return ", ".join(parts) + ". " + desc
