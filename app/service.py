"""/search целиком: разбор → поиск → ответ → query_log. События отдаются через on_event (для SSE)."""

import json
import logging
import time
from collections.abc import Callable

from app.config import AppConfig, Settings
from app.costs import track
from app.db import connect
from app.history import render_history
from app.llm.agent import AgentResult, run_agent
from app.llm.answer import completion_text, generate_answer
from app.moderation import is_stuffed, looks_injected
from app.pii import mask_pii
from app.pipeline import Retrieval, retrieve
from app.schemas import ChatTurn, SearchFilters
from app.search.catalog import ComplexHit

log_ = logging.getLogger(__name__)
Event = Callable[[str, dict], None]

LOG_SQL = """
INSERT INTO query_log (query, filters, relaxed, clarification, result_source_ids, answer_model, fallback,
                       invalid_refs, error, latency_ms, timings_ms, cost_usd, flagged_source_ids,
                       mode, tool_calls, history_turns, history_rejected)
VALUES (%(query)s, %(filters)s, %(relaxed)s, %(clarification)s, %(result_source_ids)s, %(answer_model)s,
        %(fallback)s, %(invalid_refs)s, %(error)s, %(latency_ms)s, %(timings_ms)s, %(cost_usd)s,
        %(flagged_source_ids)s, %(mode)s, %(tool_calls)s, %(history_turns)s, %(history_rejected)s)
"""


def complex_card(h: ComplexHit, cfg: AppConfig) -> dict:
    """Карточка ЖК для интерфейса: поля, подписи из конфига и группы свободных квартир под запрос."""
    c, cat = h.complex, cfg.catalog
    return {
        "source_id": c["source_id"],
        "name": c["name"],
        "city": c["city"],
        "district": c["district"],
        "developer": c["developer"],
        "class_label": cat.classes.get(c["housing_class"], c["housing_class"]),
        "completion_label": completion_text(c),
        "feature_labels": [cat.complex_features.get(x, x) for x in c["features"]],
        "payment_labels": [cat.payment.get(x, x) for x in c["payment"]],
        "description": c["description"],
        "groups": [
            {
                "rooms": g.rooms,
                "count": g.count,
                "area_min": g.area_min,
                "area_max": g.area_max,
                "price_min": g.price_min,
                "price_max": g.price_max,
            }
            for g in h.groups
        ],
    }


def flagged_complexes(hits: list[ComplexHit], cfg: AppConfig) -> list[str]:
    """ЖК с признаками вставки или набивки в описании — на модерацию (query_log)."""
    return [
        h.complex["source_id"]
        for h in hits
        if looks_injected(h.complex["description"]) or is_stuffed(h.complex["description"], cfg.moderation)
    ]


def catalog_chips(f: SearchFilters, cfg: AppConfig) -> list[str]:
    """Распознанные условия новостроек короткими подписями."""
    cat = cfg.catalog
    chips = [f.city] if f.city else []
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
    if f.area_max_m2:
        chips.append(f"до {f.area_max_m2:g} м²")
    chips += [f"{d} р-н" for d in f.districts]
    chips += [cat.classes.get(x, x) for x in f.classes]
    if f.delivered_only:
        chips.append("дом сдан")
    elif f.completion_max_year:
        chips.append(f"сдача до {f.completion_max_quarter or 4} кв. {f.completion_max_year}")
    if f.not_first_floor:
        chips.append("не первый этаж")
    if f.not_last_floor:
        chips.append("не последний этаж")
    if f.floor_min is not None and f.floor_max is not None:
        chips.append(f"этаж {f.floor_min}–{f.floor_max}")
    elif f.floor_min is not None:
        chips.append(f"этаж от {f.floor_min}")
    elif f.floor_max is not None:
        chips.append(f"этаж до {f.floor_max}")
    for values, labels in (
        (f.finishing, cat.finishing),
        (f.windows, cat.windows),
        (f.views, cat.views),
        (f.features, cat.complex_features),
        (f.payment, cat.payment),
    ):
        chips += [labels.get(x, x) for x in values]
    if f.orientations:
        chips.append("окна на " + "/".join(cat.orientations.get(x, x) for x in f.orientations))
    if f.semantic_query:
        chips.append(f"«{f.semantic_query}»")
    return chips


def money(v: int) -> str:
    return f"{v / 1e6:g} млн" if v >= 1_000_000 else f"{v // 1000} тыс"


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
                payload["chips"] = catalog_chips(f, cfg)
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
                payload["results"] = [complex_card(h, cfg) for h in r.hits]
                payload["flagged"] = flagged_complexes(r.hits, cfg)  # на модерацию; пользователю не показываем
                # номер [n] в тексте ответа = n-й объект этого списка
                emit("results", {"relaxed": r.relaxed, "results": payload["results"]})
                emit("status", {"stage": "answer"})
                answer = generate_answer(
                    query,
                    r.filters,
                    r.relaxed,
                    r.hits,
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
        "result_source_ids": [h.complex["source_id"] for h in r.hits] if r else None,
        "answer_model": answer.served_model if answer else None,
        "fallback": answer.fallback if answer else None,
        "invalid_refs": len(answer.invalid_refs) if answer else None,
        "error": payload.get("error"),
        "latency_ms": payload["latency_ms"],
        "timings_ms": json.dumps(payload["timings_ms"]),
        "cost_usd": payload.get("cost_usd"),
        "flagged_source_ids": payload.get("flagged") or None,
    }
    insert_log(row)


def insert_log(row: dict) -> None:
    """Строка query_log; поля, которых у режима нет, — NULL (mode по умолчанию — v1)."""
    row = {"mode": "v1", "tool_calls": None, "history_turns": None, "history_rejected": None} | row
    with connect() as conn:
        conn.execute(LOG_SQL, row)


def run_agent_search(
    query: str, history: list[ChatTurn], cfg: AppConfig, settings: Settings, on_event: Event | None = None
) -> dict:
    """Ход агента (v2) целиком: история → агент → query_log. События: tool, filters, results, delta*,
    answer, done. turn — подписанная реплика для истории клиента (null при отказе)."""
    emit = on_event or (lambda name, data: None)
    t0 = time.perf_counter()
    payload: dict = {"query": query, "error": None, "history_turns": len(history)}
    res: AgentResult | None = None
    with track() as ledger:
        try:

            def relay(name: str, data: dict) -> None:
                if name == "filters":
                    f = data["filters"]
                    emit("filters", {"filters": f.model_dump(exclude_defaults=True), "chips": catalog_chips(f, cfg)})
                elif name == "results":
                    payload["results"] = [complex_card(h, cfg) for h in data["hits"]]
                    emit("results", {"relaxed": data["relaxed"], "results": payload["results"]})
                else:
                    emit(name, data)

            # ponytail: соединение держится весь ход (инструменты — посреди цикла), ~5–20 с; пул — при нагрузке
            with connect() as conn:
                block, payload["history_rejected"] = render_history(conn, history, cfg.agent, settings.history_secret)
                res = run_agent(conn, query, block, cfg, settings, on_event=relay)
            turn = res.assistant_turn(settings.history_secret)  # без секрета подпись не примут — останутся source_ids
            payload |= {
                "results": [complex_card(h, cfg) for h in res.hits],
                "flagged": flagged_complexes(res.hits, cfg),  # на модерацию; пользователю не показываем
                "answer": res.text,
                "invalid_refs": res.invalid_refs,
                "clarified": res.clarified,
                "tool_calls": res.tool_calls,
                "turn": turn.model_dump() if turn else None,
            }
            emit(
                "answer",
                {
                    "text": res.text,
                    "invalid_refs": res.invalid_refs,
                    "cited": res.cited,
                    "clarified": res.clarified,
                    "turn": payload["turn"],
                },
            )
        except Exception as e:
            payload["error"] = f"{e.__class__.__name__}: {e}"
            raise
        finally:
            payload["timings_ms"] = dict(res.timings_ms) if res else {}
            payload["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            try:
                payload["cost_usd"] = round(ledger.cost_usd(cfg.pricing), 6)
            except KeyError:
                payload["cost_usd"] = None
            try:
                log_agent(payload, res)
            except Exception:  # журнал не должен ломать ответ и подменять исходную ошибку
                log_.exception("query_log write failed")
    emit(
        "done",
        {"timings_ms": payload["timings_ms"], "latency_ms": payload["latency_ms"], "cost_usd": payload["cost_usd"]},
    )
    return payload


def log_agent(payload: dict, res: AgentResult | None) -> None:
    """Строка v2: без текста истории и аргументов инструментов — только имена и счётчики."""
    f = res.first_filters if res else None
    insert_log(
        {
            "mode": "v2",
            "query": mask_pii(payload["query"]),
            "filters": json.dumps(f.model_dump(exclude_defaults=True), ensure_ascii=False) if f else None,
            "relaxed": None,
            "clarification": res.text if res and res.clarified else None,
            "result_source_ids": res.source_ids if res else None,
            "answer_model": res.served_model if res else None,
            "fallback": res.fallback if res else None,
            "invalid_refs": len(res.invalid_refs) if res else None,
            "error": payload.get("error"),
            "latency_ms": payload["latency_ms"],
            "timings_ms": json.dumps(payload["timings_ms"]),
            "cost_usd": payload.get("cost_usd"),
            "flagged_source_ids": payload.get("flagged") or None,
            "tool_calls": res.tool_calls if res else None,
            "history_turns": payload["history_turns"],
            "history_rejected": payload.get("history_rejected"),
        }
    )
