"""Синтетические объявления через Claude, пачками по 50, дописываются в data_path.

Повторный запуск продолжает с того места, где остановился.
    python scripts/generate_listings.py --config config/demo.yaml --count 2500
"""

import argparse
import json
import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.config import AppConfig, get_settings, load_config
from app.llm.client import call_tool, load_prompt
from app.schemas import Condition, DealType

BATCH = 50


def batch_model(cfg: AppConfig) -> type[BaseModel]:
    """Схема пачки в полях *источника* (до field_map): price, room_count, text."""
    Feature = Literal[tuple(cfg.features)]  # type: ignore[valid-type]
    Raw = create_model(
        "RawListing",
        district=(str, ...),
        complex_name=(str | None, None),
        deal_type=(DealType, ...),
        room_count=(int, ...),
        area_m2=(float, ...),
        floor=(int, ...),
        floors_total=(int, ...),
        year_built=(int, ...),
        condition=(Condition, ...),
        price=(int, Field(..., description="Цена в тенге; для аренды — в месяц")),
        features=(list[Feature], []),
        text=(str, Field(..., description="Текст объявления")),
    )
    return create_model("Batch", listings=(list[Raw], ...))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--count", type=int, default=2500)
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = get_settings()
    out = Path(cfg.data_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = sum(1 for line in out.open(encoding="utf-8") if line.strip()) if out.exists() else 0
    Batch = batch_model(cfg)
    schema = Batch.model_json_schema()
    system = load_prompt("generate_listings.md")

    # ponytail: последовательно, ~50 вызовов на 2500 объектов; распараллелить, если станет долго
    while done < args.count:
        city = random.choices(cfg.cities, weights=[6, 4][: len(cfg.cities)])[0]
        deal = random.choices(["sale", "rent"], weights=[7, 3])[0]
        n = min(BATCH, args.count - done)
        user = (
            f"Сгенерируй {n} объявлений. Город: {city}. Тип сделки: {deal}. "
            f"Районы (распредели между ними): {', '.join(cfg.districts[city])}. "
            f"Допустимые признаки features: {', '.join(cfg.features)}."
        )
        data = call_tool(
            api_key=settings.anthropic_api_key,
            model=cfg.models["generator"],
            system=system,
            user=user,
            tool_name="save_listings",
            tool_description="Сохранить сгенерированные объявления",
            input_schema=schema,
            max_tokens=32000,
            temperature=1,
        )
        batch = Batch.model_validate(data)
        with out.open("a", encoding="utf-8") as f:
            for item in batch.listings:
                done += 1
                row = {"source_id": f"syn-{done:05d}", "city": city, **item.model_dump()}
                row["deal_type"] = deal  # город и сделку задаём мы, а не модель
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{done}/{args.count}")


if __name__ == "__main__":
    main()
