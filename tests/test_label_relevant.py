import json

import pytest

from scripts.label_relevant import (
    interleave,
    labeling_mode,
    needs_labels,
    parse_selection,
    pick_one,
    ref,
    set_labels,
)


def test_parse_selection():
    assert parse_selection("1,4,7-9", 30) == [1, 4, 7, 8, 9]
    assert parse_selection(" 3 , 3,2-3 ", 5) == [2, 3]
    assert parse_selection("", 5) == []
    for bad in ("0", "31", "5-2", "a", "1-x"):
        with pytest.raises(ValueError):
            parse_selection(bad, 30)


def test_interleave_alternates_dedupes_and_limits():
    assert interleave(["a", "b", "c"], ["b", "d"], limit=10) == ["a", "b", "d", "c"]
    assert interleave(["a", "b", "c"], ["d", "e"], limit=3) == ["a", "d", "b"]
    assert interleave([], ["x"], limit=5) == ["x"]


def test_set_labels_rewrites_only_target_line():
    other = '{"id":"d01",  "query": "как есть"}\n'  # чужое форматирование не трогаем
    target = json.dumps({"id": "d02", "query": "q", "expected_filters": {}}, ensure_ascii=False) + "\n"
    out = set_labels([other, target, "\n"], "d02", ["syn-00009", "syn-00002"], "auto")
    assert out[0] == other and out[2] == "\n"
    case = json.loads(out[1])
    assert case["relevant_source_ids"] == ["syn-00002", "syn-00009"] and case["relevance"] == "auto"
    assert set_labels(out, "d02", ["syn-00009", "syn-00002"], "auto") == out  # повторный прогон — без изменений


def case(semantic: str, clarify: bool = False, **extra) -> dict:
    ef = {"needs_clarification": True} if clarify else {}
    return {"id": "a", "semantic": semantic, "expected_filters": ef, **extra}


def test_labeling_mode_by_semantic():
    assert labeling_mode(case("empty")) == "auto"
    assert labeling_mode(case("required")) == "manual"
    assert labeling_mode(case("any", clarify=True)) is None


def test_needs_labels_only_unlabeled_manual_or_relabel():
    assert needs_labels(case("required"), None)
    assert not needs_labels(case("empty"), None)  # авто — без ручной разметки
    assert not needs_labels(case("any", clarify=True), None)
    assert not needs_labels(case("required", relevant_source_ids=[]), None)
    assert needs_labels(case("required", relevant_source_ids=[]), "a")
    assert not needs_labels(case("empty"), "a")


def test_ref_hides_source_id():
    assert ref("seed-d10-01") == ref("seed-d10-01") and len(ref("seed-d10-01")) == 6
    assert "seed" not in ref("seed-d10-01") and ref("seed-d10-01") != ref("syn-00001")


def test_pick_one():
    assert pick_one(" 2", 3) == 2
    for bad in ("", "0", "4", "1-2", "x", "1,2"):
        assert pick_one(bad, 3) is None
