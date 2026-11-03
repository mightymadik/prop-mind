import json

import pytest

from scripts.label_relevant import interleave, needs_labels, parse_selection, set_labels


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
    out = set_labels([other, target, "\n"], "d02", ["syn-00009", "syn-00002"])
    assert out[0] == other and out[2] == "\n"
    assert json.loads(out[1])["relevant_source_ids"] == ["syn-00002", "syn-00009"]


def test_needs_labels_skips_clarification_and_labeled():
    assert needs_labels({"id": "a", "expected_filters": {}}, None)
    assert not needs_labels({"id": "a", "expected_filters": {"needs_clarification": True}}, None)
    assert not needs_labels({"id": "a", "expected_filters": {}, "relevant_source_ids": []}, None)
    assert needs_labels({"id": "a", "expected_filters": {}, "relevant_source_ids": []}, "a")
