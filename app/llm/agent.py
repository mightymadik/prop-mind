"""Агент (v2): модель сама вызывает инструменты (поиск, карточка ЖК, ипотека, статистика цен) и отвечает.

Ссылки [n] — сквозные по всем ЖК, которые инструменты вернули в этом ходе. Лимит вызовов — в конфиге;
после него — tool_choice: none. Пользователю стримится только финальный ответ, промежуточный текст —
нет: он пишется до результатов инструментов и может не подтвердиться."""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from app.config import AppConfig, Settings
from app.costs import record
from app.history import signed_turn
from app.llm.answer import FALLBACK_BETA, REFUSAL_TEXT, RELAX_TEXT, hit_line, resolve_refs
from app.llm.client import load_prompt
from app.llm.parser import render_prompt
from app.moderation import ContactFilter, strip_contacts
from app.pipeline import retrieve
from app.schemas import ChatTurn, SearchFilters, build_parser_schema
from app.search.catalog import ComplexHit
from app.tools import ToolError, get_listing, mortgage_payment, price_stats

PROMPT_FILE = "agent.md"
Event = Callable[[str, dict], None]
LABELS = {  # событие tool: только метка, без аргументов
    "search_listings": "ищу варианты…",
    "get_listing": "смотрю ЖК…",
    "mortgage_payment": "считаю ипотеку…",
    "price_stats": "смотрю цены…",
}
DESCRIPTIONS = {
    "search_listings": "Поиск ЖК со свободными квартирами по фильтрам. Возвращает ЖК с номерами [n].",
    "get_listing": "Актуальная карточка ЖК по source_id и все его свободные квартиры.",
    "mortgage_payment": "Ежемесячный платёж по программе из справочника; ставку и ограничения берёт сам.",
    "price_stats": "Медианная цена и цена за м² по свободным квартирам каталога (не рынка).",
}
LIMIT_TEXT = "Лимит вызовов инструментов исчерпан — отвечай по уже полученным данным."


def _lit(values) -> Any:
    return Literal[tuple(values)]  # type: ignore[valid-type]


def tool_models(cfg: AppConfig) -> dict[str, type[BaseModel]]:
    """Схемы входа инструментов: словари — Literal из конфига, все поля обязательны (strict)."""
    forbid = ConfigDict(extra="forbid")
    parser = build_parser_schema(cfg)
    search = {
        n: (f.annotation, Field(..., description=f.description))
        for n, f in parser.model_fields.items()
        if n != "needs_clarification"  # уточнение агент задаёт сам, без инструмента
    }
    districts = dict.fromkeys(d for ds in cfg.districts.values() for d in ds)
    return {
        "search_listings": create_model("search_listings", __config__=forbid, **search),
        "get_listing": create_model("get_listing", __config__=forbid, source_id=(str, ...)),
        "mortgage_payment": create_model(
            "mortgage_payment",
            __config__=forbid,
            program=(_lit(cfg.mortgage), Field(..., description="Программа из справочника")),
            price_kzt=(int, Field(..., description="Цена квартиры, ₸")),
            down_payment_kzt=(int, Field(..., description="Первоначальный взнос, ₸")),
            years=(int, Field(..., description="Срок, лет")),
        ),
        "price_stats": create_model(
            "price_stats",
            __config__=forbid,
            city=(_lit(cfg.cities), ...),
            district=(_lit(districts) | None, ...),
            rooms=(int | None, ...),
            housing_class=(_lit(cfg.catalog.classes) | None, ...),
        ),
    }


def tool_defs(models: dict[str, type[BaseModel]]) -> list[dict]:
    return [
        {"name": n, "description": DESCRIPTIONS[n], "input_schema": anthropic.transform_schema(m), "strict": True}
        for n, m in models.items()
    ]


class Registry:
    """ЖК, которые инструменты вернули в этом ходе: номер [n] = позиция. Повторный ЖК — тот же номер."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.hits: dict[str, ComplexHit] = {}

    def add(self, hit: ComplexHit) -> int:
        sid = hit.complex["source_id"]
        if sid not in self.hits:
            self.ids.append(sid)
        self.hits[sid] = hit  # свежие группы квартир
        return self.ids.index(sid) + 1


@dataclass
class AgentResult:
    model: str
    text: str  # с [source_id] вместо номеров
    raw_text: str
    source_ids: list[str] = field(default_factory=list)  # карточки по порядку номеров [n]
    hits: list[ComplexHit] = field(default_factory=list)
    invalid_refs: list[int] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)  # имена, по порядку; без аргументов
    first_filters: SearchFilters | None = None  # аргументы первого search_listings — для evals разбора
    refused: bool = False
    stop_reason: str | None = None
    served_model: str | None = None
    fallback: bool = False
    rounds: int = 0
    timings_ms: dict[str, int] = field(default_factory=dict)  # этапы поиска (сумма по вызовам), ttft, answer
    ttft_ms: int | None = None  # до первого токена финального ответа у пользователя
    total_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def clarified(self) -> bool:
        """Ответил без инструментов — уточнение (для evals: отдельный исход, не ноль)."""
        return not self.tool_calls and not self.refused

    def assistant_turn(self, secret: str, now: float | None = None) -> ChatTurn | None:
        """Подписанная реплика для истории клиента; отказ в историю как ответ не идёт."""
        return None if self.refused else signed_turn(self.text, self.source_ids, secret, now)


def echo_blocks(content: list) -> list:
    """Блоки ответа для следующего запроса. После серверного фолбэка посреди ответа блоки до последнего
    fallback, кроме текста, не возвращаются (thinking отказавшей модели, её незавершённые tool_use)."""
    cut = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    return [b for i, b in enumerate(content) if b.type != "fallback" and (i > cut or b.type == "text")]


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'вход'}: {x['msg']}" for x in e.errors())


def run_agent(
    conn,
    query: str,
    history: str,
    cfg: AppConfig,
    settings: Settings,
    model: str | None = None,
    on_event: Event | None = None,
    client: anthropic.Anthropic | None = None,
    embed_fn: Callable[[str], str] | None = None,
    rerank_fn: Callable[[object, list[str], str], list[str]] | None = None,
) -> AgentResult:
    """Один ход диалога. history — готовый блок <history> (app.history.render_history) или пусто.
    События: tool {label}, filters {filters}, results {hits, relaxed}, delta {text}."""
    model = model or cfg.models["agent"]
    mcfg = cfg.answer.models[model]
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    emit = on_event or (lambda name, data: None)
    models = tool_models(cfg)
    base = dict(
        model=model,
        max_tokens=cfg.answer.max_tokens,
        system=load_prompt(PROMPT_FILE) + render_prompt(cfg),
        thinking=mcfg.thinking,
        tools=tool_defs(models),
    )
    messages: list[dict] = [{"role": "user", "content": f"{history}\n\nЗапрос: {query}" if history else query}]
    reg, res = Registry(), AgentResult(model=model, text="", raw_text="")
    lim = cfg.agent
    searches = 0
    guard = ContactFilter(cfg.moderation.allowed_contacts)
    tool_choice = {"type": "auto"}
    t0 = time.perf_counter()

    def say(chunk: str) -> None:
        if safe := guard.feed(chunk):
            if res.ttft_ms is None:
                res.ttft_ms = int((time.perf_counter() - t0) * 1000)
            emit("delta", {"text": safe})

    def run_tool(u) -> str:
        nonlocal searches
        args = models[u.name].model_validate(u.input)
        if u.name == "search_listings":
            f = SearchFilters(**args.model_dump())
            res.first_filters = res.first_filters or f
            searches += 1
            emit("filters", {"filters": f})
            r = retrieve(conn, query, cfg, settings, filters=f, embed_fn=embed_fn, rerank_fn=rerank_fn)
            for stage, ms in r.timings_ms.items():
                res.timings_ms[stage] = res.timings_ms.get(stage, 0) + ms
            lines = ["Ослаблено поиском: " + (", ".join(RELAX_TEXT.get(x, x) for x in r.relaxed) or "ничего")]
            lines += [hit_line(reg.add(h), h, cfg.catalog) for h in r.hits] or ["Ничего не найдено."]
            emit("results", {"hits": [reg.hits[i] for i in reg.ids], "relaxed": r.relaxed})
            return "\n".join(lines)
        if u.name == "get_listing":
            hit = get_listing(conn, args.source_id)
            line = hit_line(reg.add(hit), hit, cfg.catalog, "Свободные квартиры (все)")
            emit("results", {"hits": [reg.hits[i] for i in reg.ids], "relaxed": []})
            return line
        if u.name == "mortgage_payment":
            out = mortgage_payment(args.program, args.price_kzt, args.down_payment_kzt, args.years, cfg)
            return json.dumps(asdict(out), ensure_ascii=False)
        return json.dumps(asdict(price_stats(conn, cfg, **args.model_dump())), ensure_ascii=False)

    def tool_result(u) -> dict:
        over = len(res.tool_calls) >= lim.max_tool_calls or (
            u.name == "search_listings" and searches >= lim.max_search_calls
        )
        res.tool_calls.append(u.name)
        out = {"type": "tool_result", "tool_use_id": u.id}
        if over:
            return out | {"content": LIMIT_TEXT, "is_error": True}
        if u.name not in models:
            return out | {"content": f"нет инструмента {u.name}", "is_error": True}
        emit("tool", {"label": LABELS[u.name]})
        try:
            return out | {"content": run_tool(u)}
        except ValidationError as e:
            return out | {"content": f"неверные аргументы: {_errors(e)}", "is_error": True}
        except ToolError as e:
            return out | {"content": str(e), "is_error": True}

    while True:
        final = tool_choice["type"] == "none"  # без инструментов — этот ответ точно финальный: стримим
        kwargs = base | {"tool_choice": tool_choice, "messages": messages}
        if mcfg.fallbacks:
            ctx = client.beta.messages.stream(**kwargs, fallbacks=mcfg.fallbacks, betas=[FALLBACK_BETA])
        else:
            ctx = client.messages.stream(**kwargs)
        with ctx as stream:
            for chunk in stream.text_stream:
                if final:
                    say(chunk)
            msg = stream.get_final_message()
        res.rounds += 1
        served = msg.model if msg.model in cfg.pricing else model
        record(served, msg.usage.input_tokens, msg.usage.output_tokens)
        res.input_tokens += msg.usage.input_tokens
        res.output_tokens += msg.usage.output_tokens
        res.served_model, res.stop_reason = msg.model, msg.stop_reason
        res.fallback |= any(b.type == "fallback" for b in msg.content) or (msg.model or model) != model

        if msg.stop_reason == "refusal":
            res.refused, raw = True, REFUSAL_TEXT
            if not final:
                say(raw)
            break
        blocks = echo_blocks(msg.content)
        uses = [b for b in blocks if b.type == "tool_use"]
        if msg.stop_reason != "tool_use" or not uses:
            raw = "".join(b.text for b in blocks if b.type == "text")
            if not final:
                say(raw)
            break
        messages.append(
            {"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in blocks]}
        )
        messages.append({"role": "user", "content": [tool_result(u) for u in uses]})  # на каждый tool_use — ответ
        if len(res.tool_calls) >= lim.max_tool_calls:
            tool_choice = {"type": "none"}

    if rest := guard.flush():
        emit("delta", {"text": rest})
    res.total_ms = int((time.perf_counter() - t0) * 1000)
    res.timings_ms["answer"] = res.total_ms
    if res.ttft_ms is not None:
        res.timings_ms["ttft"] = res.ttft_ms
    res.raw_text = raw
    res.source_ids, res.hits = list(reg.ids), [reg.hits[i] for i in reg.ids]
    res.text, res.invalid_refs, res.cited = resolve_refs(strip_contacts(raw, cfg.moderation.allowed_contacts), reg.ids)
    return res
