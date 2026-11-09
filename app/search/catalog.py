"""Поиск новостроек: жёсткие фильтры по ЖК и квартирам, смысловая часть — по ЖК (векторы + полнотекстовый,
RRF), ослабление мягких фильтров, группировка свободных квартир по ЖК и числу комнат."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas import CatalogFilters


class _Where:
    def __init__(self) -> None:
        self.clauses: list[str] = []
        self.params: dict[str, Any] = {}

    def add(self, sql: str, **params: Any) -> None:
        self.clauses.append(sql)
        self.params.update(params)

    def sql(self) -> str:
        return " AND ".join(self.clauses) or "true"


def unit_where(f: CatalogFilters) -> tuple[str, dict[str, Any]]:
    """Условия на квартиры (таблица units, алиас u). Только параметры, без склейки ввода."""
    w = _Where()
    w.add("u.status = 'free'")
    if f.rooms:
        w.add("u.rooms = ANY(%(rooms)s)", rooms=f.rooms)
    if f.price_min_kzt is not None:
        w.add("u.price_kzt >= %(price_min)s", price_min=f.price_min_kzt)
    if f.price_max_kzt is not None:
        w.add("u.price_kzt <= %(price_max)s", price_max=f.price_max_kzt)
    if f.area_min_m2 is not None:
        w.add("u.area_m2 >= %(area_min)s", area_min=f.area_min_m2)
    if f.area_max_m2 is not None:
        w.add("u.area_m2 <= %(area_max)s", area_max=f.area_max_m2)
    if f.floor_min is not None:
        w.add("u.floor >= %(floor_min)s", floor_min=f.floor_min)
    if f.floor_max is not None:
        w.add("u.floor <= %(floor_max)s", floor_max=f.floor_max)
    if f.not_first_floor:
        w.add("u.floor > 1")
    if f.not_last_floor:
        w.add("u.floor < u.floors_total")
    if f.finishing:
        w.add("u.finishing = ANY(%(finishing)s)", finishing=f.finishing)
    if f.windows:
        w.add("u.windows = ANY(%(windows)s)", windows=f.windows)
    if f.views:
        w.add("u.view = ANY(%(views)s)", views=f.views)
    if f.orientations:
        w.add("u.orientation && %(orientations)s::text[]", orientations=f.orientations)
    return w.sql(), w.params


def complex_where(f: CatalogFilters) -> tuple[str, dict[str, Any]]:
    """Условия на ЖК (таблица complexes, алиас c)."""
    w = _Where()
    if f.city:
        w.add("c.city = %(city)s", city=f.city)
    if f.districts:
        w.add("c.district = ANY(%(districts)s)", districts=f.districts)
    if f.classes:
        w.add("c.housing_class = ANY(%(classes)s)", classes=f.classes)
    if f.delivered_only:
        w.add("c.delivered")
    if f.completion_max_year is not None:
        q = f.completion_max_quarter or 4
        w.add(
            "(c.delivered OR (c.completion_year, c.completion_quarter) <= (%(cy)s, %(cq)s))",
            cy=f.completion_max_year,
            cq=q,
        )
    if f.features:
        w.add("c.features @> %(features)s::text[]", features=f.features)
    if f.payment:
        w.add("c.payment @> %(payment)s::text[]", payment=f.payment)
    return w.sql(), w.params


def query_text(filters: CatalogFilters, query: str) -> str:
    """Текст для эмбеддинга и полнотекстового поиска: смысловая часть, а если её нет — весь запрос."""
    return filters.semantic_query.strip() or query


# ЖК, где есть хотя бы одна свободная квартира под фильтры, — только они ранжируются
BASE = """
base AS (
  SELECT c.source_id, c.tsv, c.embedding FROM complexes c
  WHERE {cw} AND EXISTS (SELECT 1 FROM units u WHERE u.complex_id = c.source_id AND {uw})
)"""

VECTOR_SQL = (
    "WITH"
    + BASE
    + """
SELECT source_id FROM base ORDER BY embedding <=> %(qvec)s::vector, source_id LIMIT %(limit)s"""
)

# как в поиске по объявлениям: OR в tsquery, иначе на целом тексте совпадений почти нет
HYBRID_SQL = (
    "WITH"
    + BASE
    + """,
vec AS (
  SELECT source_id, row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector, source_id) AS r
  FROM base ORDER BY embedding <=> %(qvec)s::vector, source_id LIMIT %(pool)s
),
fts AS (
  SELECT source_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC, source_id) AS r
  FROM base, CAST(replace(plainto_tsquery('russian', %(qtext)s)::text, '&', '|') AS tsquery) AS q
  WHERE tsv @@ q
  ORDER BY ts_rank_cd(tsv, q) DESC, source_id LIMIT %(pool)s
)
SELECT source_id, SUM(1.0 / (%(rrf_k)s + r)) AS score
FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) AS x
GROUP BY source_id
ORDER BY score DESC, source_id
LIMIT %(limit)s
"""
)


def _params(f: CatalogFilters) -> tuple[str, str, dict[str, Any]]:
    cw, cp = complex_where(f)
    uw, up = unit_where(f)
    return cw, uw, {**cp, **up}


def vector_search(conn, f: CatalogFilters, qvec: str, limit: int = 50) -> list[str]:
    cw, uw, params = _params(f)
    rows = conn.execute(VECTOR_SQL.format(cw=cw, uw=uw), {**params, "qvec": qvec, "limit": limit}).fetchall()
    return [r[0] for r in rows]


def hybrid_search(
    conn, f: CatalogFilters, qvec: str, qtext: str, limit: int = 50, pool: int = 50, rrf_k: int = 60
) -> list[str]:
    cw, uw, params = _params(f)
    rows = conn.execute(
        HYBRID_SQL.format(cw=cw, uw=uw),
        {**params, "qvec": qvec, "qtext": qtext, "limit": limit, "pool": pool, "rrf_k": rrf_k},
    ).fetchall()
    return [r[0] for r in rows]


SearchMode = Literal["vector", "hybrid"]
# мягкие фильтры — снимаются по очереди и накопительно, если строгий поиск пуст;
# город, комнаты, цена, площадь, этаж и класс — жёсткие
RELAX_ORDER = ("features", "payment", "finishing", "orientations", "windows", "views", "completion", "districts")
_RELAX_RESET = {
    "features": {"features": []},
    "payment": {"payment": []},
    "completion": {"completion_max_year": None, "completion_max_quarter": None, "delivered_only": False},
}


def _is_set(f: CatalogFilters, step: str) -> bool:
    if step == "completion":
        return f.completion_max_year is not None or f.delivered_only
    return bool(getattr(f, step))


def search(
    conn, filters: CatalogFilters, qvec: str, qtext: str, mode: SearchMode = "hybrid"
) -> tuple[list[str], list[str]]:
    """(source_id ЖК по убыванию релевантности, какие фильтры сняты). Ослабление — только если строгие
    фильтры дали 0; остановка на первом непустом шаге. Пустой список без мягких фильтров — честный ноль."""
    relaxed: list[str] = []
    f = filters
    for step in (None, *RELAX_ORDER):
        if step is not None:
            if not _is_set(f, step):
                continue
            f = f.model_copy(update=_RELAX_RESET.get(step, {step: None}))
            relaxed.append(step)
        ranked = vector_search(conn, f, qvec) if mode == "vector" else hybrid_search(conn, f, qvec, qtext)
        if ranked:
            return ranked, relaxed
    return [], relaxed


def relaxed_filters(filters: CatalogFilters, relaxed: list[str]) -> CatalogFilters:
    """Фильтры после ослабления — по ним же берутся квартиры для группировки."""
    f = filters
    for step in relaxed:
        f = f.model_copy(update=_RELAX_RESET.get(step, {step: None}))
    return f


@dataclass
class UnitGroup:
    """Свободные квартиры ЖК с одним числом комнат, подходящие под фильтры."""

    rooms: int
    count: int
    area_min: float
    area_max: float
    price_min: int
    price_max: int
    unit_ids: list[str] = field(default_factory=list)  # по возрастанию цены


@dataclass
class ComplexHit:
    complex: dict
    groups: list[UnitGroup]


COMPLEX_FIELDS = (
    "source_id",
    "city",
    "district",
    "name",
    "developer",
    "housing_class",
    "completion_year",
    "completion_quarter",
    "delivered",
    "features",
    "payment",
    "description",
)


def group_units(rows: list[tuple]) -> list[UnitGroup]:
    """rows: (rooms, source_id, area_m2, price_kzt) по возрастанию комнат и цены → группы по числу комнат."""
    groups: dict[int, UnitGroup] = {}
    for rooms, sid, area, price in rows:
        area = float(area)
        g = groups.get(rooms)
        if g is None:
            groups[rooms] = UnitGroup(rooms, 1, area, area, price, price, [sid])
        else:
            g.count += 1
            g.area_min, g.area_max = min(g.area_min, area), max(g.area_max, area)
            g.price_min, g.price_max = min(g.price_min, price), max(g.price_max, price)
            g.unit_ids.append(sid)
    return [groups[k] for k in sorted(groups)]


def fetch_hits(conn, complex_ids: list[str], f: CatalogFilters) -> list[ComplexHit]:
    """ЖК в порядке complex_ids + их свободные квартиры под фильтры, сгруппированные по числу комнат."""
    if not complex_ids:
        return []
    cols = ", ".join(COMPLEX_FIELDS)
    by_id = {
        r[0]: dict(zip(COMPLEX_FIELDS, r, strict=True))
        for r in conn.execute(f"SELECT {cols} FROM complexes WHERE source_id = ANY(%s)", (complex_ids,))
    }
    uw, params = unit_where(f)
    units: dict[str, list[tuple]] = {}
    for cid, *rest in conn.execute(
        f"SELECT u.complex_id, u.rooms, u.source_id, u.area_m2, u.price_kzt FROM units u "
        f"WHERE u.complex_id = ANY(%(ids)s) AND {uw} ORDER BY u.complex_id, u.rooms, u.price_kzt, u.source_id",
        {**params, "ids": complex_ids},
    ):
        units.setdefault(cid, []).append(tuple(rest))
    return [ComplexHit(by_id[c], group_units(units.get(c, []))) for c in complex_ids]
