import json

import pytest

from scripts.label_relevant import (
    ask,
    format_row,
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
    for bad in ("0", "31", "5-2", "a", "1-x", ", 1", "1,,2", "1,"):
        with pytest.raises(ValueError):
            parse_selection(bad, 30)


def test_interleave_alternates_dedupes_and_limits():
    assert interleave(["a", "b", "c"], ["b", "d"], limit=10) == ["a", "b", "d", "c"]
    assert interleave(["a", "b", "c"], ["d", "e"], limit=3) == ["a", "d", "b"]
    assert interleave([], ["x"], limit=5) == ["x"]


def test_set_labels_rewrites_only_target_line():
    other = '{"id":"d01",  "query": "как есть"}\n'  # чужое форматирование не трогаем
    target = json.dumps({"id": "d02", "query": "q", "expected_filters": {}}, ensure_ascii=False) + "\n"
    ids = ["syn-00009", "syn-00002"]
    out = set_labels([other, target, "\n"], "d02", ids, "auto", judged=ids + ["syn-00005"])
    assert out[0] == other and out[2] == "\n"
    case = json.loads(out[1])
    assert case["relevant_source_ids"] == ["syn-00002", "syn-00009"] and case["relevance"] == "auto"
    assert case["judged_source_ids"] == ["syn-00002", "syn-00005", "syn-00009"]
    assert set_labels(out, "d02", ids, "auto", judged=ids + ["syn-00005"]) == out  # повтор — без изменений


def test_set_labels_merge_adds_to_previous_labels():
    line = json.dumps(
        {"id": "d15", "relevant_source_ids": ["a"], "judged_source_ids": ["a", "b"], "relevance": "llm_reviewed"}
    )
    [merged] = set_labels([line + "\n"], "d15", ["c"], "llm_reviewed", judged=["c", "d"], merge=True)
    case = json.loads(merged)
    assert case["relevant_source_ids"] == ["a", "c"] and case["judged_source_ids"] == ["a", "b", "c", "d"]
    [replaced] = set_labels([line + "\n"], "d15", ["c"], "llm_reviewed", judged=["c", "d"])
    assert json.loads(replaced)["relevant_source_ids"] == ["c"]


def test_set_labels_relevant_must_be_judged():
    with pytest.raises(ValueError, match="оценённые"):
        set_labels(['{"id": "d15"}\n'], "d15", ["x"], "manual", judged=["y"])


def case(semantic: str, clarify: bool = False, **extra) -> dict:
    ef = {"needs_clarification": True} if clarify else {}
    return {"id": "a", "semantic": semantic, "expected_filters": ef, **extra}


def test_labeling_mode_by_semantic():
    assert labeling_mode(case("empty")) == "auto"
    assert labeling_mode(case("required")) == "manual"
    assert labeling_mode(case("any", clarify=True)) is None
    assert labeling_mode({"id": "r01", "split": "relax", "expected_filters": {}}) is None


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


ROWS = [
    (
        "syn-00324",
        45_000_000,
        2,
        64.0,
        "Бостандыкский",
        "renovated",
        [],
        "Двушка с большой кухней-гостиной, вид на горы",
    ),
    ("syn-00096", 61_000_000, 3, 92.0, "Медеуский", "renovated", ["parking"], "Продам 3-комнатную в ЖК Нурлы Тау"),
    ("seed-d15-01", 70_000_000, 4, 120.0, "Медеуский", "designer", [], "Четырёхкомнатная с террасой"),
]


def dialog(inputs: list[str]) -> tuple[list[str] | str, list[str]]:
    it, out = iter(inputs), []
    return ask("d15", ROWS, read=lambda _: next(it), write=out.append), out


def test_detail_shows_same_object_as_table_row():
    for k, row in enumerate(ROWS, 1):
        result, out = dialog([f"? {k}", "q"])
        detail = out[0].splitlines()
        assert result == "q"
        assert detail[0] == "  d15, кандидат " + str(k) + ":"
        assert detail[1] == format_row(k, row)  # та же строка таблицы: хеш, комнаты, цена
        assert detail[2].strip() == row[-1]
        others = [ref(r[0]) for r in ROWS if r is not row]
        assert not any(h in out[0] for h in others)


def test_selection_saved_only_after_confirmation():
    result, out = dialog(["2", "n", "1,3", ""])
    assert result == ["syn-00324", "seed-d15-01"]
    assert out == [
        f"  d15: отмечено 2 ({ref('syn-00096')})",
        f"  d15: отмечено 1 ({ref('syn-00324')}), 3 ({ref('seed-d15-01')})",
    ]


def test_empty_answer_means_none_after_confirmation():
    result, out = dialog(["", ""])
    assert result == [] and out == ["  d15: отмечено ничего — подходящих нет"]


def test_bad_input_does_not_save():
    result, out = dialog([", 2", "? 9", "s"])
    assert result == "s" and out[0].startswith("  ошибка") and out[1].startswith("  ошибка")
