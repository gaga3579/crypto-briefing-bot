import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import prices  # noqa: E402
from bot.config import load_config  # noqa: E402

HOUR = 3600
NOW = 1793372400 + 7 * 60  # 2026-10-30 15:07 UTC (정각 + 7분)
H = NOW // HOUR * HOUR


@pytest.fixture
def cfg():
    return load_config()


def make_coinbase(spec):
    """spec: {심볼: dict(price, high=100, high_age=100, prev_close=None, low=None)}.
    고점 봉 하나를 제외하면 모든 봉의 값은 price(또는 prev_close)다."""
    def fetch(sym, now_ts, lookback):
        s = spec[sym]
        if s.get("fail"):
            raise prices.FetchError(f"coinbase ticker {sym}-USD HTTP0")
        price = Decimal(str(s["price"]))
        base = Decimal(str(s.get("prev_close", s["price"])))
        low = Decimal(str(s.get("low", base)))
        h = int(now_ts) // HOUR * HOUR
        candles = {}
        for k in range(lookback):
            t = h - k * HOUR
            candles[t] = (min(low, base), base, base, base)
        ht = h - s.get("high_age", 100) * HOUR
        candles[ht] = (base, Decimal(str(s.get("high", 100))), base, base)
        return price, candles
    return fetch


def make_kraken(values):
    return lambda: {k: Decimal(str(v)) for k, v in values.items()}


class Outbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, text):
        if self.fail:
            return False, "HTTP500"
        self.sent.append(text)
        return True, ""


OFFLINE = {"official_fn": lambda s: {}, "chain_fn": lambda now: {}, "extract_fn": lambda item: None}


def empty_rss(url, headers=None):
    return 200, "<rss><channel></channel></rss>"
