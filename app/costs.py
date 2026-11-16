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
    tokens: dict[str, list[int]] = field(default_factory=dict)  # модель → [вход, выход, запись в кэш, чтение кэша]

    def add(
        self, model: str, input_tokens: int = 0, output_tokens: int = 0, cache_write: int = 0, cache_read: int = 0
    ) -> None:
        t = self.tokens.setdefault(model, [0, 0, 0, 0])
        for i, n in enumerate((input_tokens, output_tokens, cache_write, cache_read)):
            t[i] += n

    def cost_usd(self, pricing: dict[str, Price]) -> float:
        """Оценка сверху (бесплатные токены не учитываются). Модель без цены — ошибка конфига."""
        missing = sorted(set(self.tokens) - set(pricing))
        if missing:
            raise KeyError(f"нет цены в config.pricing: {missing}")
        return sum(
            (i * p.input + o * p.output + w * p.write_price() + r * p.read_price()) / 1e6
            for m, (i, o, w, r) in self.tokens.items()
            for p in [pricing[m]]
        )


_current: ContextVar[Ledger | None] = ContextVar("ledger", default=None)


def record(
    model: str, input_tokens: int = 0, output_tokens: int = 0, cache_write: int = 0, cache_read: int = 0
) -> None:
    if (ledger := _current.get()) is not None:
        ledger.add(model, input_tokens, output_tokens, cache_write, cache_read)


def absorb(other: Ledger) -> None:
    """Токены отдельного счётчика (например, фоновой задачи) — в текущий."""
    for model, tokens in other.tokens.items():
        record(model, *tokens)


def record_usage(model: str, usage) -> None:
    """Токены ответа Anthropic, включая кэш промпта: input_tokens кэшированную часть не содержат."""
    record(
        model,
        usage.input_tokens,
        usage.output_tokens,
        getattr(usage, "cache_creation_input_tokens", None) or 0,
        getattr(usage, "cache_read_input_tokens", None) or 0,
    )


@contextmanager
def track(ledger: Ledger | None = None) -> Iterator[Ledger]:
    """ledger — уже созданный счётчик (например, для фоновой задачи: его читает другой поток)."""
    ledger = ledger if ledger is not None else Ledger()
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)
