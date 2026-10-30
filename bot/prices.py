"""가격 수집(Coinbase 기본, Kraken 교차 검증)과 비정상 값 판정."""
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from . import net

HOUR = 3600
COINBASE = "https://api.exchange.coinbase.com"
MAX_CANDLES_PER_REQUEST = 300  # 1단계 실측: 301개 요청은 HTTP 400
KRAKEN_TICKER = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD,ETHUSD,SOLUSD,XRPUSD,USDTUSD,USDCUSD"
KRAKEN_KEYS = {"BTC": "XXBTZUSD", "ETH": "XETHZUSD", "SOL": "SOLUSD", "XRP": "XXRPZUSD",
               "USDT": "USDTZUSD", "USDC": "USDCUSD"}  # 1단계 실측 응답 키


class FetchError(Exception):
    pass


def D(x):
    return Decimal(str(x))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CoinMarket:
    symbol: str
    price: Decimal
    high: Decimal          # 30일 고점 (현재가 포함)
    high_time: int         # 고점 봉의 시작 시각(UTC epoch). 현재가가 고점이면 현재 시각
    prev_close: Decimal    # 직전 완성 봉 종가 (비정상 값 판정 기준)
    change_24h: Decimal | None
    change_7d: Decimal | None


def fetch_coinbase(symbol, now_ts, lookback_hours, get=net.request, pause=0.2):
    """현재가와 {봉 시작시각: (저가, 고가, 시가, 종가)}를 돌려준다."""
    product = f"{symbol}-USD"
    s, d = get(f"{COINBASE}/products/{product}/ticker")
    if s != 200 or not d or "price" not in d:
        raise FetchError(f"coinbase ticker {product} HTTP{s}")
    price = D(d["price"])
    hour_start = int(now_ts) // HOUR * HOUR
    candles = {}
    span = MAX_CANDLES_PER_REQUEST - 1
    end = hour_start
    while end > hour_start - lookback_hours * HOUR:
        start = end - span * HOUR
        s, d = get(f"{COINBASE}/products/{product}/candles?granularity=3600&start={iso(start)}&end={iso(end)}")
        if s != 200 or not isinstance(d, list):
            raise FetchError(f"coinbase candles {product} HTTP{s}")
        for t, low, high, open_, close, _vol in d:
            candles[int(t)] = (D(low), D(high), D(open_), D(close))
        end = start - HOUR
        time.sleep(pause)
    return price, candles


def fetch_kraken(get=net.request):
    """{심볼: 현재가}. 실패하면 빈 dict."""
    s, d = get(KRAKEN_TICKER)
    if s != 200 or not d or d.get("error"):
        return {}
    res = d.get("result", {})
    return {sym: D(res[key]["c"][0]) for sym, key in KRAKEN_KEYS.items() if key in res}


def summarize(symbol, price, candles, now_ts, lookback_hours):
    hour_start = int(now_ts) // HOUR * HOUR
    window = {t: c for t, c in candles.items() if t > hour_start - lookback_hours * HOUR}
    if not window:
        raise FetchError(f"{symbol} 봉 데이터 없음")
    high_time = min((t for t in window), key=lambda t: (-window[t][1], t))
    high = window[high_time][1]
    if price > high:
        high, high_time = price, int(now_ts)
    prev = window.get(hour_start - HOUR)

    def change(hours):
        c = window.get(hour_start - (hours + 1) * HOUR)
        return (price / c[3] - 1) * 100 if c and c[3] > 0 else None

    return CoinMarket(symbol, price, high, high_time, prev[3] if prev else None, change(24), change(24 * 7))


def check_sanity(cm, kraken_price, cfg):
    """(정상 여부, 사유). 0·결측은 보류, 직전 봉 대비 급변은 Kraken과 일치할 때만 인정."""
    ps = cfg["price_sanity"]
    if cm.price is None or cm.price <= 0:
        return False, f"{cm.symbol} 현재가 0 또는 결측"
    if cm.prev_close is None or cm.prev_close <= 0:
        return False, f"{cm.symbol} 직전 봉 없음"
    jump = abs(cm.price / cm.prev_close - 1) * 100
    if jump <= ps["max_jump_pct"]:
        return True, ""
    if kraken_price and abs(cm.price / kraken_price - 1) * 100 <= ps["cross_check_tolerance_pct"]:
        return True, f"{cm.symbol} 직전 봉 대비 {jump:.1f}% 변동, Kraken과 일치하여 인정"
    return False, f"{cm.symbol} 직전 봉 대비 {jump:.1f}% 변동, Kraken 교차 검증 실패"
