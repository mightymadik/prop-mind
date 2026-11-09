"""Инварианты синтетики новостроек: на закоммиченных data/complexes.jsonl и data/units.jsonl."""

import json
from collections import Counter
from pathlib import Path

from app.config import load_config
from app.schemas import Complex, Unit
from scripts.generate_catalog import load_calibration

CFG = load_config("config/demo.yaml")
CAT = CFG.catalog
CALIB = load_calibration(CAT.price_calibration_path)
COMPLEXES = [json.loads(ln) for ln in Path(CAT.complexes_path).read_text(encoding="utf-8").splitlines()]
UNITS = [json.loads(ln) for ln in Path(CAT.units_path).read_text(encoding="utf-8").splitlines()]
BY_ID = {c["source_id"]: c for c in COMPLEXES}


def test_rows_valid_and_names_unique():
    for c in COMPLEXES:
        Complex.model_validate(c)
    for u in UNITS:
        Unit.model_validate(u)
        assert 1 <= u["floor"] <= u["floors_total"] == BY_ID[u["complex_id"]]["floors_total"]
    assert len({c["name"] for c in COMPLEXES}) == len(COMPLEXES)
    assert len({u["source_id"] for u in UNITS}) == len(UNITS)


def test_price_per_m2_within_calibration_plus_minus_10_percent():
    dev = CAT.price_coefs["max_deviation"]
    for u in UNITS:
        c = BY_ID[u["complex_id"]]
        lo, hi = CALIB[(c["city"], c["district"], c["housing_class"])]
        rate = u["price_kzt"] / u["area_m2"]
        assert lo * (1 - dev) <= rate <= hi * (1 + dev), (u["source_id"], rate, lo, hi)


def test_every_calibration_row_has_a_complex_and_business_only_where_calibrated():
    assert {(c["city"], c["district"], c["housing_class"]) for c in COMPLEXES} == set(CALIB)


def test_views_metro_and_river_follow_geography():
    river = Counter()
    for u in UNITS:
        c = BY_ID[u["complex_id"]]
        if u["view"] == "mountains":
            assert c["city"] == "Алматы" and "south" in u["orientation"]
        if u["view"] == "river":
            assert c["city"] == "Астана"
            river[c["district"]] += 1
    assert river["Есильский"] / sum(river.values()) >= 0.7
    for c in COMPLEXES:
        if "near_metro" in c["features"]:
            assert c["city"] == "Алматы"
        assert c["district"] in CFG.districts[c["city"]]
