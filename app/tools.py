"""Инструменты агента (v2) без LLM: калькулятор ипотеки и статистика цен по каталогу.
Ошибка ввода → ToolError: её текст уходит модели как результат инструмента с is_error."""

from dataclasses import dataclass

from app.config import AppConfig
from app.schemas import SearchFilters
from app.search.catalog import complex_where, unit_where


class ToolError(ValueError):
    pass


@dataclass
class MortgageResult:
    program: str
    label: str
    rate_pct: float
    price_kzt: int
    down_payment_kzt: int
    loan_kzt: int
    years: int
    monthly_kzt: int
    total_kzt: int  # сумма всех платежей по кредиту, без первоначального взноса
    overpayment_kzt: int
    as_of: str
    source: str


def mortgage_payment(program: str, price: int, down_payment: int, years: int, cfg: AppConfig) -> MortgageResult:
    """Аннуитетный платёж; при ставке 0 (рассрочка) — равные доли. Ставку, срок, взнос и лимит берём из конфига."""
    p = cfg.mortgage.get(program)
    if p is None:
        raise ToolError(f"неизвестная программа {program!r}; доступны: {', '.join(cfg.mortgage)}")
    if price <= 0:
        raise ToolError("цена должна быть больше нуля")
    if not 0 <= down_payment < price:
        raise ToolError("первоначальный взнос — от 0 и меньше цены")
    if down_payment * 100 < p.min_down_pct * price:
        raise ToolError(f"{p.label}: взнос не меньше {p.min_down_pct:g}% цены ({int(price * p.min_down_pct / 100)} ₸)")
    if not 1 <= years <= p.max_years:
        raise ToolError(f"{p.label}: срок от 1 до {p.max_years} лет")
    loan = price - down_payment
    if p.max_loan_kzt is not None and loan > p.max_loan_kzt:
        raise ToolError(f"{p.label}: сумма кредита не больше {p.max_loan_kzt} ₸, а нужно {loan} ₸")

    n = years * 12
    r = p.rate_pct / 100 / 12
    monthly = loan / n if r == 0 else loan * r / (1 - (1 + r) ** -n)
    total = round(monthly * n)
    return MortgageResult(
        program=program,
        label=p.label,
        rate_pct=p.rate_pct,
        price_kzt=price,
        down_payment_kzt=down_payment,
        loan_kzt=loan,
        years=years,
        monthly_kzt=round(monthly),
        total_kzt=total,
        overpayment_kzt=total - loan,
        as_of=p.as_of,
        source=p.source,
    )


@dataclass
class PriceStats:
    city: str
    district: str | None
    rooms: int | None
    housing_class: str | None
    count: int  # свободных квартир в выборке
    enough_data: bool  # False — медиан нет: выборка меньше порога из конфига
    median_price_kzt: int | None = None
    median_kzt_per_m2: int | None = None
    p25_kzt_per_m2: int | None = None
    p75_kzt_per_m2: int | None = None
    note: str = "по свободным квартирам нашего каталога, не по рынку"


STATS_SQL = """
SELECT count(*),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY u.price_kzt),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY u.price_kzt / u.area_m2),
       percentile_cont(0.25) WITHIN GROUP (ORDER BY u.price_kzt / u.area_m2),
       percentile_cont(0.75) WITHIN GROUP (ORDER BY u.price_kzt / u.area_m2)
FROM units u JOIN complexes c ON c.source_id = u.complex_id
WHERE {where}
"""


def price_stats(
    conn,
    cfg: AppConfig,
    city: str,
    district: str | None = None,
    rooms: int | None = None,
    housing_class: str | None = None,
) -> PriceStats:
    """Медиана цены и цены за м² по свободным квартирам каталога; мало квартир — без медиан."""
    if city not in cfg.cities:
        raise ToolError(f"город — один из: {', '.join(cfg.cities)}")
    if district is not None and district not in cfg.districts.get(city, []):
        raise ToolError(f"в городе {city} нет района {district!r}")
    if housing_class is not None and housing_class not in cfg.catalog.classes:
        raise ToolError(f"класс — один из: {', '.join(cfg.catalog.classes)}")
    f = SearchFilters(
        city=city,
        districts=[district] if district else [],
        rooms=[rooms] if rooms is not None else [],
        classes=[housing_class] if housing_class else [],
    )
    (cw, cp), (uw, up) = complex_where(f), unit_where(f)
    count, med_price, med_m2, p25, p75 = conn.execute(STATS_SQL.format(where=f"{cw} AND {uw}"), cp | up).fetchone()
    stats = PriceStats(city, district, rooms, housing_class, count, count >= cfg.agent.price_stats_min_count)
    if stats.enough_data:
        stats.median_price_kzt, stats.median_kzt_per_m2 = round(med_price), round(med_m2)
        stats.p25_kzt_per_m2, stats.p75_kzt_per_m2 = round(p25), round(p75)
    return stats
