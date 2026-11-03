"""Единственное место, которое знает о провайдере LLM."""

from pathlib import Path

import anthropic
from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def call_structured[T: BaseModel](
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    output: type[T],
    max_tokens: int = 1024,
    client: anthropic.Anthropic | None = None,
) -> T:
    """Ответ строго по схеме Pydantic-модели (structured outputs), уже провалидированный.

    SDK сам приводит схему к требованиям API (additionalProperties: false и т. п.).
    Без temperature/top_p/top_k: API 2026-10-21 их отклоняет, SDK 2.x не принимает.
    Без принудительного tool_choice: актуальные модели отвечают на него 400.
    Стрим: SDK отказывается от нестримовых запросов с большим max_tokens.
    """
    client = client or anthropic.Anthropic(api_key=api_key or None)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_format=output,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    # обрезанный JSON или текст отказа SDK сам отвергает ValidationError; тут — ответ без текста
    if msg.parsed_output is None:
        raise RuntimeError(f"no structured output: stop_reason={msg.stop_reason}")
    return msg.parsed_output
