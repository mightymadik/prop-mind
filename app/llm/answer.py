"""Ответ пользователю по найденным объектам: стриминг, ссылки [n] → source_id, учёт задержки и токенов."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic

from app.config import AppConfig
from app.llm.client import load_prompt
from app.schemas import SearchFilters

PROMPT_FILE = "answer.md"
REF = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")  # [3] и на всякий случай [1, 3]
RELAX_TEXT = {"features": "признаки", "conditions": "состояние", "districts": "район"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# строка кандидата из listings: source_id, price_kzt, rooms, area_m2, district, condition, features, description
Row = tuple


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


def build_user_message(
    query: str, filters: SearchFilters, relaxed: list[str], rows: list[Row], feature_labels: dict[str, str]
) -> str:
    shown = filters.model_dump(exclude_defaults=True, exclude={"semantic_query", "needs_clarification"})
    lines = [
        f"Запрос: {query}",
        f"Фильтры: {shown or 'нет'}; смысловая часть: {filters.semantic_query or 'нет'}",
        "Ослаблено поиском: " + (", ".join(RELAX_TEXT.get(r, r) for r in relaxed) if relaxed else "ничего"),
        "",
        "Кандидаты:" if rows else "Кандидатов нет: поиск ничего не нашёл.",
    ]
    for n, (_, price, rooms, area, district, cond, feats, desc) in enumerate(rows, 1):
        feats_txt = ", ".join(feature_labels.get(f, f) for f in feats) or "-"
        lines.append(
            f"[{n}] {price:,} ₸".replace(",", " ")
            + f", {rooms or '-'} комн., {area or '-'} м², {district or '-'} р-н, {cond or '-'}, признаки: {feats_txt}"
            + f"\n    {desc}"
        )
    return "\n".join(lines)


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


def generate_answer(
    query: str,
    filters: SearchFilters,
    relaxed: list[str],
    rows: list[Row],
    cfg: AppConfig,
    api_key: str,
    model: str | None = None,
    on_text: Callable[[str], None] | None = None,
    client: anthropic.Anthropic | None = None,
) -> AnswerResult:
    """Стримит ответ; on_text получает куски текста по мере генерации (для SSE). Ссылки проверяются в конце."""
    model = model or cfg.answer.model
    mcfg = cfg.answer.models[model]
    client = client or anthropic.Anthropic(api_key=api_key or None)
    kwargs = dict(
        model=model,
        max_tokens=cfg.answer.max_tokens,
        system=load_prompt(PROMPT_FILE),
        thinking=mcfg.thinking,
        messages=[{"role": "user", "content": build_user_message(query, filters, relaxed, rows, cfg.features)}],
    )
    if mcfg.fallbacks:
        stream_ctx = client.beta.messages.stream(**kwargs, fallbacks=mcfg.fallbacks, betas=[FALLBACK_BETA])
    else:
        stream_ctx = client.messages.stream(**kwargs)

    t0 = time.perf_counter()
    ttft = None
    with stream_ctx as stream:
        for chunk in stream.text_stream:
            if ttft is None and chunk:
                ttft = int((time.perf_counter() - t0) * 1000)
            if on_text:
                on_text(chunk)
        msg = stream.get_final_message()
    total = int((time.perf_counter() - t0) * 1000)

    raw = "".join(b.text for b in msg.content if b.type == "text")
    # фолбэк виден как блок типа fallback в ответе и как смена модели в msg.model
    fallback = any(b.type == "fallback" for b in msg.content) or (msg.model or model) != model
    if msg.stop_reason == "refusal":
        raw = "Не получилось подготовить ответ по этому запросу. Попробуйте переформулировать."
    text, invalid, cited = resolve_refs(raw, [r[0] for r in rows])
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
