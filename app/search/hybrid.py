from typing import Any

from app.schemas import SearchFilters


def where_from_filters(f: SearchFilters) -> tuple[str, dict[str, Any]]:
    """SearchFilters → SQL-условие для listings и параметры. Только параметры, без склейки ввода."""
    clauses = ["deal_type = %(deal_type)s"]
    params: dict[str, Any] = {"deal_type": f.deal_type}

    def add(sql: str, name: str, value: Any) -> None:
        clauses.append(sql)
        params[name] = value

    if f.city:
        add("city = %(city)s", "city", f.city)
    if f.rooms:
        add("rooms = ANY(%(rooms)s)", "rooms", f.rooms)
    if f.price_min_kzt is not None:
        add("price_kzt >= %(price_min)s", "price_min", f.price_min_kzt)
    if f.price_max_kzt is not None:
        add("price_kzt <= %(price_max)s", "price_max", f.price_max_kzt)
    if f.area_min_m2 is not None:
        add("area_m2 >= %(area_min)s", "area_min", f.area_min_m2)
    if f.districts:
        add("district = ANY(%(districts)s)", "districts", f.districts)
    if f.conditions:
        add("condition = ANY(%(conditions)s)", "conditions", f.conditions)
    if f.features:
        add("features @> %(features)s::text[]", "features", f.features)
    return " AND ".join(clauses), params
