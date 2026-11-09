"""/search целиком: разбор → поиск → ответ → query_log. События отдаются через on_event (для SSE)."""

import json
import logging
import time
from collections.abc import Callable

from app.config import AppConfig, Settings
from app.costs import track
from app.db import connect
from app.llm.answer import generate_answer
from app.moderation import flagged
from app.pii import mask_pii
from app.pipeline import Retrieval, retrieve
from app.schemas import SearchFilters
from app.search.text import CONDITION_TEXT

log_ = logging.getLogger(__name__)
Event = Callable[[str, dict], None]
CARD_FIELDS = ("source_id", "price_kzt", "rooms", "area_m2", "district", "condition", "features", "description")

LOG_SQL = """
INSERT INTO query_log (query, filters, relaxed, clarification, result_source_ids, answer_model, fallback,
                       invalid_refs, error, latency_ms, timings_ms, cost_usd, flagged_source_ids)
VALUES (%(query)s, %(filters)s, %(relaxed)s, %(clarification)s, %(result_source_ids)s, %(answer_model)s,
        %(fallback)s, %(invalid_refs)s, %(error)s, %(latency_ms)s, %(timings_ms)s, %(cost_usd)s,
        %(flagged_source_ids)s)
"""


def card(row: tuple, cfg: AppConfig | None = None) -> dict:
    d = dict(zip(CARD_FIELDS, row, strict=True))
    d["area_m2"] = float(d["area_m2"]) if d["area_m2"] is not None else None
    if cfg:  # подписи для интерфейса — из конфига, чтобы не дублировать словари в JS
        d["condition_label"] = CONDITION_TEXT.get(d["condition"]) if d["condition"] else None
        d["feature_labels"] = [cfg.features.get(f, f) for f in d["features"]]
    return d


def money(v: int) -> str:
    return f"{v / 1e6:g} млн" if v >= 1_000_000 else f"{v // 1000} тыс"


def filter_chips(f: SearchFilters, cfg: AppConfig) -> list[str]:
    """Распознанные условия короткими подписями — пользователь видит их, пока идёт поиск."""
    chips = [f.city] if f.city else []
    chips.append("аренда" if f.deal_type == "rent" else "покупка")
    if f.rooms:
        chips.append("/".join(map(str, f.rooms)) + " комн.")
    if f.price_min_kzt is not None and f.price_max_kzt is not None:
        chips.append(f"{money(f.price_min_kzt)} – {money(f.price_max_kzt)}")
    elif f.price_max_kzt is not None:
        chips.append(f"до {money(f.price_max_kzt)}")
    elif f.price_min_kzt is not None:
        chips.append(f"от {money(f.price_min_kzt)}")
    if f.area_min_m2:
        chips.append(f"от {f.area_min_m2:g} м²")
    chips += [f"{d} р-н" for d in f.districts or []]
    chips += [CONDITION_TEXT.get(c, c) for c in f.conditions or []]
    chips += [cfg.features.get(x, x) for x in f.features]
    if f.semantic_query:
        chips.append(f"«{f.semantic_query}»")
    return chips


def run_search(query: str, cfg: AppConfig, settings: Settings, on_event: Event | None = None) -> dict:
    """Возвращает итоговый ответ API; при on_event — шлёт filters, results, delta*, answer/clarification, done."""
    emit = on_event or (lambda name, data: None)
    t0 = time.perf_counter()
    payload: dict = {"query": query, "error": None}
    r: Retrieval | None = None
    answer = None
    with track() as ledger:
        try:

            def on_filters(f: SearchFilters) -> None:
                payload["filters"] = f.model_dump(exclude_defaults=True)
                payload["chips"] = filter_chips(f, cfg)
                emit("filters", {"filters": payload["filters"], "chips": payload["chips"]})

            with connect() as conn:
                r = retrieve(
                    conn,
                    query,
                    cfg,
                    settings,
                    on_stage=lambda st: emit("status", {"stage": st}),
                    on_filters=on_filters,
                )
            if r.clarification:
                payload["clarification"] = r.clarification
                emit("clarification", {"text": r.clarification})
            else:
                payload["relaxed"] = r.relaxed
                payload["results"] = [card(row, cfg) for row in r.rows]
                payload["flagged"] = flagged(r.rows, cfg.moderation)  # на модерацию; пользователю не показываем
                # номер [n] в тексте ответа = n-й объект этого списка
                emit("results", {"relaxed": r.relaxed, "results": payload["results"]})
                emit("status", {"stage": "answer"})
                answer = generate_answer(
                    query,
                    r.filters,
                    r.relaxed,
                    r.rows,
                    cfg,
                    settings.anthropic_api_key,
                    on_text=lambda chunk: emit("delta", {"text": chunk}),
                )
                payload["answer"] = answer.text
                payload["invalid_refs"] = answer.invalid_refs
                emit("answer", {"text": answer.text, "invalid_refs": answer.invalid_refs, "cited": answer.cited})
        except Exception as e:
            payload["error"] = f"{e.__class__.__name__}: {e}"
            raise
        finally:
            timings = dict(r.timings_ms) if r else {}
            if answer:
                timings |= {"ttft": answer.ttft_ms, "answer": answer.total_ms}
            payload["timings_ms"] = timings
            payload["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            try:
                payload["cost_usd"] = round(ledger.cost_usd(cfg.pricing), 6)
            except KeyError:
                payload["cost_usd"] = None
            try:
                log(payload, r, answer)
            except Exception:  # журнал не должен ломать ответ и подменять исходную ошибку
                log_.exception("query_log write failed")
    emit(
        "done",
        {"timings_ms": payload["timings_ms"], "latency_ms": payload["latency_ms"], "cost_usd": payload["cost_usd"]},
    )
    return payload


def log(payload: dict, r: Retrieval | None, answer) -> None:
    """Запись в query_log — и при ошибке запроса. Текст запроса — после маскирования ПДн."""
    row = {
        "query": mask_pii(payload["query"]),
        "filters": json.dumps(payload.get("filters"), ensure_ascii=False) if payload.get("filters") else None,
        "relaxed": payload.get("relaxed"),
        "clarification": payload.get("clarification"),
        "result_source_ids": [x[0] for x in r.rows] if r else None,
        "answer_model": answer.served_model if answer else None,
        "fallback": answer.fallback if answer else None,
        "invalid_refs": len(answer.invalid_refs) if answer else None,
        "error": payload.get("error"),
        "latency_ms": payload["latency_ms"],
        "timings_ms": json.dumps(payload["timings_ms"]),
        "cost_usd": payload.get("cost_usd"),
        "flagged_source_ids": payload.get("flagged") or None,
    }
    with connect() as conn:
        conn.execute(LOG_SQL, row)
