import pytest

from scripts.label_catalog import apply_labels, parse_review

REVIEW = """Проверено: да

| кейс | ЖК | id | решение | причина |
| --- | --- | --- | --- | --- |
| n17 | Бутаковка | cx-010 | да | рядом парк |
| n17 | Ботаника | cx-006 | нет | про зелень ничего |
| n28 | Карасу | cx-009 | Да | озеленённый двор |
"""


def test_parse_review_and_apply():
    d = parse_review(REVIEW)
    assert d == {"n17": {"cx-010": True, "cx-006": False}, "n28": {"cx-009": True}}
    [c17, c01] = apply_labels([{"id": "n17"}, {"id": "n01"}], d)
    assert c17["relevant_source_ids"] == ["cx-010"] and c17["judged_source_ids"] == ["cx-006", "cx-010"]
    assert c17["relevance"] == "llm_reviewed" and "relevant_source_ids" not in c01
    with pytest.raises(ValueError):
        parse_review(REVIEW.replace("Проверено: да", "Проверено: нет"))
    with pytest.raises(ValueError):
        parse_review(REVIEW.replace("| нет |", "| может |"))
