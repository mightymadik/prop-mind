from pathlib import Path

import pytest

from app.config import Settings, load_config
from scripts import llm_label
from scripts.label_relevant import ref

CFG = load_config("config/demo.yaml")
ROWS = [
    ("syn-00804", 90_000_000, 4, 140.0, "Медеуский", "designer", ["parking"], "С видом на горы, тихо."),
    ("syn-00812", 25_000_000, 1, 40.0, "Алатауский", "pre_finish", [], "Рядом горы, до центра 40 минут."),
]
CASE = {"id": "d15", "query": "тихая квартира с видом на горы", "semantic": "required", "expected_filters": {}}


def decisions(*flags: bool) -> list[llm_label.Decision]:
    return [llm_label.Decision(n=i, relevant=f, reason=f"причина {i}") for i, f in enumerate(flags, 1)]


def test_rules_come_from_labeling_doc():
    rules = llm_label.labeling_rules(Path("docs/labeling.md").read_text(encoding="utf-8"))
    assert rules.startswith("- **Объект подходит") and "Сомневаешься — не подходит" in rules
    assert "## " not in rules  # ровно один раздел


def test_review_roundtrip_after_human_check():
    text = llm_label.render_review([(CASE, ROWS, decisions(True, False))], "m")
    assert "Проверено: нет" in text
    with pytest.raises(ValueError, match="не отмечен"):
        llm_label.parse_review(text)
    edited = text.replace("Проверено: нет", "Проверено: да").replace(
        f"| {ref('syn-00812')} | нет |", f"| {ref('syn-00812')} | да |"
    )
    assert llm_label.parse_review(edited) == {"d15": {ref("syn-00804"): True, ref("syn-00812"): True}}


def test_parse_review_rejects_unknown_decision():
    text = llm_label.render_review([(CASE, ROWS, decisions(True, False))], "m").replace(
        "Проверено: нет", "Проверено: да"
    )
    with pytest.raises(ValueError, match="да.*нет"):
        llm_label.parse_review(text.replace("| да |", "| может |", 1))


def test_reason_cannot_break_table():
    d = [llm_label.Decision(n=1, relevant=True, reason="a | b\nc")]
    text = llm_label.render_review([(CASE, ROWS[:1], d)], "m").replace("Проверено: нет", "Проверено: да")
    assert llm_label.parse_review(text) == {"d15": {ref("syn-00804"): True}}


def test_resolve_refs_unique_or_error():
    ids = ["syn-00804", "syn-00812"]
    assert llm_label.resolve_refs({ref("syn-00804")}, ids) == {ref("syn-00804"): "syn-00804"}
    with pytest.raises(ValueError, match="найдено объектов 0"):
        llm_label.resolve_refs({"ffffff"}, ids)


def test_decide_retries_when_numbers_missing(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        items = decisions(True) if len(calls) == 1 else decisions(True, False)
        return llm_label.Decisions(items=items)

    monkeypatch.setattr(llm_label, "call_structured", fake)
    out = llm_label.decide(CASE, ROWS, CFG, Settings(), "правила")
    assert [d.relevant for d in out] == [True, False] and len(calls) == 2
    assert calls[0]["model"] == CFG.models["labeler"]
    assert "правила" in calls[0]["system"] and "$rules" not in calls[0]["system"]
    assert CASE["query"] in calls[0]["user"] and "С видом на горы, тихо." in calls[0]["user"]


def test_review_prints_no_queries_or_decisions(monkeypatch, tmp_path, capsys):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"id": "h06", "split": "holdout", "query": "СЕКРЕТНЫЙ ЗАПРОС", '
        '"semantic": "required", "expected_filters": {}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_label, "CASES", cases)
    monkeypatch.setattr(llm_label, "connect", lambda: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(llm_label, "candidates", lambda conn, c, cfg, key: (2, ROWS))
    monkeypatch.setattr(llm_label, "decide", lambda c, rows, cfg, s, rules: decisions(True, False))
    llm_label.review(CFG, Settings(), path=tmp_path / "review.md")
    printed = capsys.readouterr().out
    assert "СЕКРЕТНЫЙ" not in printed and "причина" not in printed and ref("syn-00804") not in printed
    assert "h06: размечено кандидатов 2" in printed
    assert "СЕКРЕТНЫЙ ЗАПРОС" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_targets_only_semantic_cases():
    cases = [
        {"id": "d15", "semantic": "required", "expected_filters": {}},
        {"id": "d01", "semantic": "empty", "expected_filters": {}},
    ]
    assert [c["id"] for c in llm_label.targets(cases)] == ["d15"]
    assert [c["id"] for c in llm_label.targets(cases, ["d15"])] == ["d15"]
    with pytest.raises(SystemExit, match="d01"):
        llm_label.targets(cases, ["d01"])


def test_review_never_overwrites_existing_file(monkeypatch, tmp_path):
    existing = tmp_path / "review.md"
    existing.write_text("проверено человеком", encoding="utf-8")
    cases = tmp_path / "cases.jsonl"
    cases.write_text('{"id": "d15", "query": "q", "semantic": "required", "expected_filters": {}}\n', encoding="utf-8")
    monkeypatch.setattr(llm_label, "CASES", cases)
    monkeypatch.setattr(llm_label, "connect", lambda: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(llm_label, "candidates", lambda conn, c, cfg, key: (2, ROWS))
    monkeypatch.setattr(llm_label, "decide", lambda c, rows, cfg, s, rules: decisions(True, False))
    with pytest.raises(SystemExit, match="не перезаписываю"):
        llm_label.review(CFG, Settings(), path=existing)
    assert existing.read_text(encoding="utf-8") == "проверено человеком"


def test_unjudged_from_report_unions_modes_and_skips_judged_and_auto():
    cases = [
        {"id": "d15", "semantic": "required", "expected_filters": {}, "judged_source_ids": ["a", "b"]},
        {"id": "d01", "semantic": "empty", "expected_filters": {}, "judged_source_ids": ["a"]},
    ]
    report = {
        "results": {
            "vector": [{"id": "d15", "top10": ["a", "x"]}, {"id": "d01", "top10": ["z"]}],
            "hybrid": [{"id": "d15", "top10": ["y", "x", "b"]}],
        }
    }
    assert llm_label.unjudged_from_report(report, cases) == {"d15": ["x", "y"]}


def test_extend_prints_no_queries_or_decisions(monkeypatch, tmp_path, capsys):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"id": "h06", "query": "СЕКРЕТНЫЙ ЗАПРОС", "semantic": "required", "expected_filters": {}, '
        '"judged_source_ids": []}\n',
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    report.write_text('{"results": {"hybrid": [{"id": "h06", "top10": ["syn-00804", "syn-00812"]}]}}', encoding="utf-8")
    monkeypatch.setattr(llm_label, "CASES", cases)
    monkeypatch.setattr(llm_label, "connect", lambda: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(llm_label, "rows_by_ids", lambda conn, ids: ROWS)
    monkeypatch.setattr(llm_label, "decide", lambda c, rows, cfg, s, rules: decisions(True, False))
    out = tmp_path / "extend.md"
    llm_label.extend(CFG, Settings(), report, out)
    printed = capsys.readouterr().out
    assert "h06: на дооценку 2" in printed and "СЕКРЕТНЫЙ" not in printed and "причина" not in printed
    assert "Проверено: нет" in out.read_text(encoding="utf-8")
