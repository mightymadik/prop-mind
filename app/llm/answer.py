"""Ответ пользователю по найденным объектам: стриминг, ссылки [n] → source_id, учёт задержки и токенов."""

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic

from app.config import AppConfig, CatalogConfig
from app.costs import record
from app.llm.client import load_prompt
from app.moderation import ContactFilter, strip_contacts
from app.schemas import CatalogFilters
from app.search.catalog import ComplexHit

REF = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")  # [3] и на всякий случай [1, 3]
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class AnswerResult:
    model: str
    text: str  # с [source_id] вместо номеров
    raw_text: str  # как написала модель, с [n]
    invalid_refs: list[int] = field(default_factory=list)  # номера вне 1..N — убраны из text
    cited: list[str] = field(default_factory=list)  # source_id, на которые сослался ответ
    stop_reason: str | None = None
    served_model: str | None = None  # какая модель на самом деле ответила
    fallback: bool = False  # сработал серверный фолбэк: ответила не запрошенная модель — пишем в query_log
    ttft_ms: int | None = None  # до первого текстового токена
    total_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


CATALOG_PROMPT_FILE = "answer_catalog.md"
CATALOG_RELAX_TEXT = {
    "features": "инфраструктура ЖК",
    "payment": "способ покупки",
    "finishing": "отделка",
    "orientations": "стороны света",
    "windows": "окна",
    "views": "вид из окна",
    "completion": "срок сдачи",
    "districts": "район",
}


def millions(v: int) -> str:
    """41500000 → «41,5» (млн, без единицы)."""
    return f"{v / 1e6:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def num(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def completion_text(c: dict) -> str:
    return "дом сдан" if c["delivered"] else f"сдача {c['completion_quarter']} кв. {c['completion_year']}"


def build_catalog_message(
    query: str, filters: CatalogFilters, relaxed: list[str], hits: list[ComplexHit], cat: CatalogConfig
) -> str:
    shown = filters.model_dump(exclude_defaults=True, exclude={"semantic_query", "needs_clarification"})
    lines = [
        f"Запрос: {query}",
        f"Условия запроса (фильтры, уже применены поиском): {shown or 'нет'}",
        f"Смысловая часть (подтверждать описанием ЖК): {filters.semantic_query or 'нет'}",
        "Ослаблено поиском: " + (", ".join(CATALOG_RELAX_TEXT.get(r, r) for r in relaxed) if relaxed else "ничего"),
        "",
        "Кандидаты — ЖК:" if hits else "Кандидатов нет: поиск ничего не нашёл.",
    ]
    for n, h in enumerate(hits, 1):
        c = h.complex
        infra = ", ".join(cat.complex_features.get(x, x) for x in c["features"]) or "-"
        pay = ", ".join(cat.payment.get(x, x) for x in c["payment"]) or "-"
        groups = "; ".join(
            f"{g.rooms}-комн. — {g.count} шт., {num(g.area_min)}–{num(g.area_max)} м², "
            f"{millions(g.price_min)}–{millions(g.price_max)} млн ₸"
            for g in h.groups
        )
        lines.append(
            f"[{n}] ЖК {c['name']} — {cat.classes.get(c['housing_class'], c['housing_class'])}, "
            f"{c['district']} р-н, {c['city']}; {completion_text(c)}; инфраструктура: {infra}; покупка: {pay}"
            f"\n    Свободные квартиры под запрос: {groups or 'нет'}"
            f"\n    <description>{quote_description(c['description'])}</description>"
        )
    return "\n".join(lines)


def quote_description(desc: str) -> str:
    """Описание — данные продавца: в одну строку и с экранированными < >, чтобы текст не мог закрыть
    тег <description>, начать «новую реплику» или строку кандидата [n] с новой строки."""
    return html.escape(" ".join(desc.split()), quote=False)


def resolve_refs(text: str, ids: list[str]) -> tuple[str, list[int], list[str]]:
    """[n] → [source_id]. Номера вне 1..len(ids) — галлюцинация: убираются и возвращаются отдельно."""
    invalid: list[int] = []
    cited: list[str] = []

    def sub(m: re.Match) -> str:
        out = []
        for n in (int(x) for x in m.group(1).split(",")):
            if 1 <= n <= len(ids):
                out.append(f"[{ids[n - 1]}]")
                if ids[n - 1] not in cited:
                    cited.append(ids[n - 1])
            else:
                invalid.append(n)
        return "".join(out)

    return REF.sub(sub, text), invalid, cited


def generate_catalog_answer(
    query: str,
    filters: CatalogFilters,
    relaxed: list[str],
    hits: list[ComplexHit],
    cfg: AppConfig,
    api_key: str,
    model: str | None = None,
    on_text: Callable[[str], None] | None = None,
    client: anthropic.Anthropic | None = None,
) -> AnswerResult:
    """Ответ по найденным ЖК (новостройки): ссылки [n] → source_id ЖК."""
    message = build_catalog_message(query, filters, relaxed, hits, cfg.catalog)
    ids = [h.complex["source_id"] for h in hits]
    return _generate(message, ids, CATALOG_PROMPT_FILE, cfg, api_key, model, on_text, client)


def _generate(
    message: str,
    ids: list[str],
    prompt_file: str,
    cfg: AppConfig,
    api_key: str,
    model: str | None,
    on_text: Callable[[str], None] | None,
    client: anthropic.Anthropic | None,
) -> AnswerResult:
    model = model or cfg.answer.model
    mcfg = cfg.answer.models[model]
    client = client or anthropic.Anthropic(api_key=api_key or None)
    kwargs = dict(
        model=model,
        max_tokens=cfg.answer.max_tokens,
        system=load_prompt(prompt_file),
        thinking=mcfg.thinking,
        messages=[{"role": "user", "content": message}],
    )
    if mcfg.fallbacks:
        stream_ctx = client.beta.messages.stream(**kwargs, fallbacks=mcfg.fallbacks, betas=[FALLBACK_BETA])
    else:
        stream_ctx = client.messages.stream(**kwargs)

    allowed = cfg.moderation.allowed_contacts
    guard = ContactFilter(allowed)  # контакты и ссылки не уходят пользователю даже в стриме
    t0 = time.perf_counter()
    ttft = None
    with stream_ctx as stream:
        for chunk in stream.text_stream:
            if ttft is None and chunk:
                ttft = int((time.perf_counter() - t0) * 1000)
            if on_text and (safe := guard.feed(chunk)):
                on_text(safe)
        msg = stream.get_final_message()
    if on_text and (rest := guard.flush()):
        on_text(rest)
    total = int((time.perf_counter() - t0) * 1000)

    # при фолбэке платим по цене модели, которая ответила, если она есть в прайсе
    served = msg.model if msg.model in cfg.pricing else model
    record(served, msg.usage.input_tokens, msg.usage.output_tokens)
    raw = "".join(b.text for b in msg.content if b.type == "text")
    # фолбэк виден как блок типа fallback в ответе и как смена модели в msg.model
    fallback = any(b.type == "fallback" for b in msg.content) or (msg.model or model) != model
    if msg.stop_reason == "refusal":
        raw = "Не получилось подготовить ответ по этому запросу. Попробуйте переформулировать."
    text, invalid, cited = resolve_refs(strip_contacts(raw, allowed), ids)
    return AnswerResult(
        model=model,
        text=text,
        raw_text=raw,
        invalid_refs=invalid,
        cited=cited,
        stop_reason=msg.stop_reason,
        served_model=msg.model,
        fallback=fallback,
        ttft_ms=ttft,
        total_ms=total,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
    )


def cost_usd(r: AnswerResult, cfg: AppConfig) -> float:
    p = cfg.pricing[r.model]
    return (r.input_tokens * p.input + r.output_tokens * p.output) / 1e6
