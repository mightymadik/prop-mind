"""Функциональные кейсы агента (v2): диалоги из 1–3 ходов, проверки кодом (судья — только для ослабления).

    uv run python evals/run_agent_cases.py                 # evals/cases_agent.jsonl, один прогон

Ходы идут по очереди с настоящей подписанной историей. Поиск — на кэшах Voyage, как в остальных evals.
Отчёт: evals/results/<UTC>_agent_cases_dev[_tag].json
"""

import argparse
import json
import re
from datetime import UTC, datetime

from app.config import get_settings, load_config
from app.costs import track
from app.db import connect
from app.history import render_history
from app.llm.agent import run_agent
from app.llm.answer import RELAX_TEXT
from app.schemas import ChatTurn
from app.tools import mortgage_payment, price_stats
from evals.run_evals import ROOT, cached_retrieval_fns, judge, pct

CASES = ROOT / "cases_agent.jsonl"
SECRET = "agent-cases-eval-secret"
NUMBER = re.compile(r"(\d[\d   ]*(?:[.,]\d+)?)\s*(млн|тыс)?")


def numbers(text: str) -> list[float]:
    """Числа из ответа с учётом «млн» / «тыс»: «412 тыс» → 412000, «39,7 млн» → 39700000."""
    out = []
    for digits, unit in NUMBER.findall(text):
        v = float(re.sub(r"[   ]", "", digits).replace(",", "."))
        out.append(v * {"млн": 1e6, "тыс": 1e3}.get(unit, 1))
    return out


def mentions(text: str, target: float, tol: float = 0.01) -> bool:
    return any(abs(v - target) <= tol * target for v in numbers(text))


def calls(res, name: str, ok: bool | None = None) -> list[dict]:
    return [x for x in res.tool_log if x["name"] == name and (ok is None or x["error"] != ok)]


def check(c: dict, turns: list, conn, cfg, settings, query: str) -> tuple[bool, str]:
    res = turns[c["turn"] - 1]
    t = c["type"]
    if t == "filters":
        f = res.first_filters
        if f is None:
            return False, "поиска не было"
        bad = {k: getattr(f, k) for k, v in c["expect"].items() if getattr(f, k) != v}
        return not bad, f"не совпало: {bad}" if bad else "ok"
    if t == "get_listing_of":
        want = turns[c["of_turn"] - 1].source_ids[c["index"] - 1 : c["index"]]
        got = [x["input"]["source_id"] for x in calls(res, "get_listing")]
        return bool(want) and want[0] in got, f"нужен {want}, вызваны {got}"
    if t == "mortgage":
        ok = calls(res, "mortgage_payment", ok=True)
        if not ok:
            return False, f"успешного расчёта нет: {res.tool_log}"
        i = ok[-1]["input"]
        m = mortgage_payment(i["program"], i["price_kzt"], i["down_payment_kzt"], i["years"], cfg)
        return mentions(res.text, m.monthly_kzt), f"платёж {m.monthly_kzt}, вход {i}"
    if t == "mortgage_error":
        err = calls(res, "mortgage_payment", ok=False)
        said = bool(re.search(c["mention"], res.text))
        return bool(err) and said, f"ошибка инструмента: {bool(err)}, причина в ответе: {said}"
    if t in ("stats", "stats_low"):
        ok = calls(res, "price_stats", ok=True)
        if not ok:
            return False, f"статистики нет: {res.tool_log}"
        s = price_stats(conn, cfg, **ok[-1]["input"])
        if t == "stats_low":
            # «данных мало» любыми словами: «мало», «недостаточно», «для медианы нужно больше», «всего N квартир»
            said = bool(re.search(rf"мало|недостаточно|нужно больше|всего {s.count}\b", res.text, re.I))
            return not s.enough_data and said, f"enough_data={s.enough_data}, «мало данных» в ответе: {said}"
        hit = mentions(res.text, s.median_kzt_per_m2, 0.015) or mentions(res.text, s.median_price_kzt, 0.015)
        catalog = "каталог" in res.text.lower()
        return hit and catalog, f"медиана в ответе: {hit}, «каталог»: {catalog}"
    if t == "relaxed":
        if not res.relaxed and len(calls(res, "search_listings")) < 2:
            return False, "поиск ничего не ослаблял и не повторялся"
        snyato = ", ".join(RELAX_TEXT.get(x, x) for x in res.relaxed) or "часть условий (повторный поиск)"
        row = {"relaxed": res.relaxed, "ids": res.source_ids}
        v = judge(
            f"Ответ явно сообщает, что поиск снял или ослабил часть условий запроса (снято: {snyato}). "
            "Достаточно назвать конкретное снятое условие — например, какой пункт инфраструктуры или "
            "способа покупки не удалось выполнить; дословно повторять общее название не нужно.",
            query,
            row,
            res.text,
            cfg,
            settings,
        )
        return v.verdict, v.reason
    if t == "clarify":  # вопрос вместо выдачи: без инструментов или по просьбе разбора поиска
        return res.clarified, f"вызовы: {res.tool_calls}, показано ЖК: {len(res.source_ids)}"
    raise ValueError(f"неизвестная проверка {t}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--model", help="модель агента (по умолчанию models.agent)")
    ap.add_argument("--cases", nargs="+", help="только эти id")
    ap.add_argument("--tag")
    args = ap.parse_args()
    cfg, settings = load_config(args.config), get_settings()
    embed_fn, rerank_fn = cached_retrieval_fns(cfg, settings)
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    cases = [c for c in cases if not args.cases or c["id"] in args.cases]

    results = []
    with connect() as conn:
        for case in cases:
            history: list[ChatTurn] = []
            turns, rows = [], []
            for q in case["turns"]:
                block, rejected = render_history(conn, history, cfg.agent, SECRET)
                with track() as ledger:
                    res = run_agent(
                        conn, q, block, cfg, settings, model=args.model, embed_fn=embed_fn, rerank_fn=rerank_fn
                    )
                turns.append(res)
                rows.append(
                    {
                        "query": q,
                        "text": res.raw_text,
                        "tool_log": res.tool_log,
                        "source_ids": res.source_ids,
                        "invalid_refs": res.invalid_refs,
                        "rounds": res.rounds,
                        "total_ms": res.total_ms,
                        "timings_ms": res.timings_ms,
                        "llm_calls": res.llm_calls,
                        "prefetch": res.prefetch,
                        "history_rejected": rejected,
                        "cost_usd": round(ledger.cost_usd(cfg.pricing), 6),
                    }
                )
                history.append(ChatTurn(role="user", text=q))
                if turn := res.assistant_turn(SECRET):
                    history.append(turn)
            verdicts = []
            first = calls(turns[0], "search_listings")
            if first:  # первый поиск без истории — сообщение пользователя дословно
                ok = " ".join(first[0]["input"]["query"].split()) == " ".join(case["turns"][0].split())
                verdicts.append({"check": "query_verbatim", "turn": 1, "ok": ok, "why": first[0]["input"]["query"]})
            for c in case["checks"]:
                ok, why = check(c, turns, conn, cfg, settings, case["turns"][c["turn"] - 1])
                verdicts.append({"check": c["type"], "turn": c["turn"], "ok": ok, "why": why})
            invalid = sum(len(r["invalid_refs"]) for r in rows)
            passed = all(v["ok"] for v in verdicts) and not invalid
            print(
                f"{case['id']} {case['tags']}: {'OK' if passed else 'FAIL'} "
                + "; ".join(f"{v['check']}: {v['why']}" for v in verdicts if not v["ok"])
                + (f"; чужих номеров {invalid}" if invalid else "")
            )
            results.append(
                {"id": case["id"], "tags": case["tags"], "passed": passed, "checks": verdicts, "turns": rows}
            )

    total = [r["total_ms"] for x in results for r in x["turns"]]
    first_token = [
        r["timings_ms"]["first_token"] for x in results for r in x["turns"] if "first_token" in r["timings_ms"]
    ]
    summary = {
        "passed": sum(x["passed"] for x in results),
        "of": len(results),
        "by_tag": {
            t: {
                "passed": sum(x["passed"] for x in results if t in x["tags"]),
                "of": sum(t in x["tags"] for x in results),
            }
            for t in sorted({t for x in results for t in x["tags"]})
        },
        "turns": len(total),
        "invalid_refs": sum(len(r["invalid_refs"]) for x in results for r in x["turns"]),
        "history_rejected": sum(r["history_rejected"] for x in results for r in x["turns"]),
        "total_ms": {"p50": pct(total, 0.5), "p95": pct(total, 0.95)},
        "first_token_ms": {"p50": pct(first_token, 0.5), "p95": pct(first_token, 0.95)},  # если стримить финальный
        "prefetch": {
            "used": sum((r.get("prefetch") or {}).get("state") == "used" for x in results for r in x["turns"]),
            "of": sum(bool(r.get("prefetch")) for x in results for r in x["turns"]),
            "unused_usd": round(
                sum(
                    (r["prefetch"].get("usd") or 0)
                    for x in results
                    for r in x["turns"]
                    if r.get("prefetch") and r["prefetch"]["state"] != "used"
                ),
                6,
            ),
            "unused_not_finished": sum(
                1 for x in results for r in x["turns"] if r.get("prefetch") and r["prefetch"].get("usd") is None
            ),
        },
        "usd_per_turn": round(sum(r["cost_usd"] for x in results for r in x["turns"]) / len(total), 6),
    }
    now = datetime.now(UTC)
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_agent_cases_dev{'_' + args.tag if args.tag else ''}.json"
    report = {
        "layer": "agent_cases",
        "date": now.isoformat(timespec="seconds"),
        "model": args.model or cfg.models["agent"],
        "summary": summary,
        "results": results,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"report: {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
