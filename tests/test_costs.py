import contextvars
import threading

import pytest

from app.config import load_config
from app.costs import Ledger, record, track

CFG = load_config("config/demo.yaml")


def test_track_collects_and_prices_per_model():
    record("claude-haiku-5-5", 1000, 1000)  # вне track — игнорируется
    with track() as ledger:
        record("claude-haiku-5-5", 1000, 200)
        record("claude-haiku-5-5", 1000, 0)
        record("rerank-2.5", 6000)
    assert ledger.tokens == {"claude-haiku-5-5": [2000, 200, 0, 0], "rerank-2.5": [6000, 0, 0, 0]}
    assert ledger.cost_usd(CFG.pricing) == pytest.approx((2000 * 0.10 + 200 * 0.50 + 6000 * 0.05) / 1e6)


def test_prompt_cache_tokens_are_priced():
    with track() as ledger:
        record("claude-sonnet-5-5", 100, 10, cache_write=4000)
        record("claude-sonnet-5-5", 200, 20, cache_read=4000)
    assert ledger.tokens["claude-sonnet-5-5"] == [300, 30, 4000, 4000]
    assert ledger.cost_usd(CFG.pricing) == pytest.approx((300 * 2.0 + 30 * 10.0 + 4000 * 2.5 + 4000 * 0.20) / 1e6)
    assert CFG.pricing["rerank-2.5"].read_price() == pytest.approx(0.005)  # без явной цены — 0,1 × input


def test_unknown_model_price_is_an_error():
    ledger = Ledger()
    ledger.add("no-such-model", 10)
    with pytest.raises(KeyError, match="no-such-model"):
        ledger.cost_usd(CFG.pricing)


def test_ledger_reaches_thread_only_with_copied_context():
    with track() as ledger:
        threading.Thread(target=record, args=("rerank-2.5", 1)).start()  # без контекста — мимо
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(record, "rerank-2.5", 5))
        t.start()
        t.join()
    assert ledger.tokens == {"rerank-2.5": [5, 0, 0, 0]}


def test_every_model_in_config_has_a_price():
    used = {CFG.models["parser"], CFG.embeddings.model, CFG.rerank.model, CFG.answer.model, *CFG.answer.models}
    assert used <= set(CFG.pricing)
