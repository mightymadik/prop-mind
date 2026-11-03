"""Единственное место, которое знает о провайдере LLM."""

from pathlib import Path
from typing import Any

import anthropic

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def call_tool(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 1024,
    temperature: float = 0,
) -> dict[str, Any]:
    """Принудительный вызов инструмента → его input (надёжный способ получить JSON)."""
    client = anthropic.Anthropic(api_key=api_key or None)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        tools=[{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    )
    return next(b for b in resp.content if b.type == "tool_use").input
