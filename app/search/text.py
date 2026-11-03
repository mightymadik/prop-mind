from app.schemas import Listing

CONDITION_TEXT = {
    "rough": "черновая отделка",
    "pre_finish": "предчистовая отделка",
    "renovated": "с ремонтом",
    "designer": "дизайнерский ремонт",
}
DEAL_TEXT = {"sale": "продажа", "rent": "аренда"}


def build_search_text(listing: Listing, feature_labels: dict[str, str]) -> str:
    """Contextual retrieval: поля объекта текстом перед описанием."""
    l = listing
    parts = [
        f"{l.rooms}-комнатная квартира" if l.rooms else "квартира",
        DEAL_TEXT[l.deal_type],
        f"{l.area_m2:g} м²" if l.area_m2 else None,
        f"{l.floor}/{l.floors_total} этаж" if l.floor and l.floors_total else None,
        f"{l.year_built} год постройки" if l.year_built else None,
        f"ЖК {l.complex_name}" if l.complex_name else None,
        f"{l.district} район" if l.district else None,
        l.city,
        CONDITION_TEXT.get(l.condition) if l.condition else None,
        *(feature_labels.get(f, f) for f in l.features),
    ]
    return ", ".join(p for p in parts if p) + ". " + l.description
