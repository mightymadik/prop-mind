"""Счётчик токенов на один запрос: каждый вызов модели пишет сюда свои токены, в конце — цена по конфигу.

    with track() as ledger:
        ...                      # parse, embed, rerank, answer — каждый вызывает record()
    ledger.cost_usd(cfg.pricing)

Вне track() record() ничего не делает. В другой поток счётчик не переходит сам —
запускать через contextvars.copy_context().run(...).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.config import Price


@dataclass
class Ledger:
    tokens: dict[str, list[int]] = field(default_factory=dict)  # модель → [вход, выход]

    def add(self, model: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        t = self.tokens.setdefault(model, [0, 0])
        t[0] += input_tokens
        t[1] += output_tokens

    def cost_usd(self, pricing: dict[str, Price]) -> float:
        """Оценка сверху (бесплатные токены не учитываются). Модель без цены — ошибка конфига."""
        missing = sorted(set(self.tokens) - set(pricing))
        if missing:
            raise KeyError(f"нет цены в config.pricing: {missing}")
        return sum((i * pricing[m].input + o * pricing[m].output) / 1e6 for m, (i, o) in self.tokens.items())


_current: ContextVar[Ledger | None] = ContextVar("ledger", default=None)


def record(model: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
    if (ledger := _current.get()) is not None:
        ledger.add(model, input_tokens, output_tokens)


@contextmanager
def track() -> Iterator[Ledger]:
    ledger = Ledger()
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)
