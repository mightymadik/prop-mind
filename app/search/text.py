from app.config import CatalogConfig, ModerationConfig
from app.moderation import index_description
from app.schemas import Complex


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
