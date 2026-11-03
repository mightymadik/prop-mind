import json

import pytest

from app.config import load_config
from app.schemas import SearchFilters
from scripts.seed_listings import (
    SeedText,
    applicable_violations,
    plan_case,
    replace_seed_lines,
    shares_phrase,
    to_raw,
    violations,
)

CFG = load_config("config/demo.yaml")

FILTERS = [
    {
        "city": "Астана",
        "districts": ["Есильский"],
        "rooms": [3],
        "conditions": ["pre_finish"],
        "price_max_kzt": 55_000_000,
    },
    {"city": "Астана", "rooms": [2, 3], "conditions": ["rough"], "area_min_m2": 70},
    {
        "deal_type": "rent",
        "city": "Алматы",
        "districts": ["Медеуский"],
        "rooms": [2, 3],
        "features": ["furnished", "parking"],
        "price_max_kzt": 600_000,
    },
    {
        "city": "Астана",
        "districts": ["Нура"],
        "rooms": [1],
        "features": ["mortgage"],
        "price_min_kzt": 20_000_000,
        "price_max_kzt": 25_000_000,
    },
]


@pytest.mark.parametrize("ef", FILTERS)
@pytest.mark.parametrize("intent", ["", "тихо, окна во двор"])
def test_plan_relevant_match_and_decoys_break_exactly_one_filter(ef, intent):
    f = SearchFilters.model_validate(ef)
    specs = plan_case({"id": "x", "expected_filters": ef, "intent": intent}, CFG)
    assert [s["role"] for s in specs].count("relevant") == 4
    for s in specs:
        assert violations(f, s["fields"]) == ([s["violated"]] if s["violated"] else [])
        assert s["fields"]["district"] in CFG.districts[s["fields"]["city"]]
    decoys = [s for s in specs if s["role"] == "decoy"]
    assert len(decoys) == 4
    if intent:
        assert any(s["violated"] is None for s in decoys)  # фильтры есть, смысла нет
        assert all(intent not in s["task"] or s["task"].startswith("Не упоминай") for s in decoys if not s["violated"])
    else:
        assert all(s["violated"] for s in decoys)  # без intent обманка — только нарушение фильтра
    assert {s["violated"] for s in decoys if s["violated"]} <= set(applicable_violations(f, CFG))


def test_plan_is_reproducible_per_case():
    case = {"id": "d10", "expected_filters": FILTERS[0], "intent": ""}
    assert plan_case(case, CFG) == plan_case(case, CFG)


def test_shares_phrase_three_words():
    q = "кв-ра 3 комн, предчистовая, астана"
    assert shares_phrase("Продаётся КВ-РА 3 комн у парка", q)
    assert not shares_phrase("Трёшка в Астане, предчистовая отделка", q)


def test_to_raw_uses_source_field_names():
    t = SeedText(n=1, complex_name=None, floor=2, floors_total=5, year_built=1975, text="Текст")
    raw = to_raw("seed-d10-01", {"price_kzt": 1, "rooms": 2, "city": "Астана"}, t, CFG.field_map)
    assert raw["price"] == 1 and raw["room_count"] == 2 and raw["text"] == "Текст"
    assert "price_kzt" not in raw and "description" not in raw


def test_replace_seed_lines_only_touches_own_case():
    lines = [
        json.dumps({"source_id": "syn-00001"}) + "\n",
        json.dumps({"source_id": "seed-d10-01"}) + "\n",
        json.dumps({"source_id": "seed-d12-01"}) + "\n",
    ]
    out = replace_seed_lines(lines, "d10", [{"source_id": "seed-d10-01", "v": 2}])
    assert [json.loads(x)["source_id"] for x in out] == ["syn-00001", "seed-d12-01", "seed-d10-01"]
    assert json.loads(out[-1])["v"] == 2


def test_rooms_violation_when_neighbours_are_in_filter():
    ef = {"city": "Алматы", "rooms": [1, 2], "price_max_kzt": 30_000_000}
    f = SearchFilters.model_validate(ef)
    for s in plan_case({"id": "r12", "expected_filters": ef, "intent": "x"}, CFG):
        assert violations(f, s["fields"]) == ([s["violated"]] if s["violated"] else [])


def test_no_violation_offered_when_filter_lists_every_option():
    f = SearchFilters(
        city="Астана",
        districts=list(CFG.districts["Астана"]),
        conditions=["rough", "pre_finish", "renovated", "designer"],
        rooms=[1, 2, 3, 4, 5],
    )
    assert applicable_violations(f, CFG) == []
