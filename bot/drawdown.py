"""하락 단계 판정 (명세 4절). 입출력은 순수 데이터라 테스트에서 그대로 쓴다.

코인별 상태: {"level": 0, "anchor_high": "100000", "anchor_time": 1790000000}
level k는 k번째 단계(−10%, −20%, …)까지 알렸다는 뜻이다. level 0이면 기준 고점은 저장하지 않는다.
"""
from decimal import ROUND_HALF_UP, Decimal


def thresholds(cfg):
    dd = cfg["drawdown"]
    ths = list(dd["tiers"])
    while ths[-1] + dd["step"] > -100:
        ths.append(ths[-1] + dd["step"])
    return ths


def drawdown_pct(price, high):
    return ((price / high - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def level_of(dd_pct, ths):
    return sum(1 for t in ths if dd_pct <= t)


def evaluate(coin_state, cm, cfg):
    """(새 상태, 새로 도달한 level 또는 None, drawdown%)"""
    ths = thresholds(cfg)
    dd = drawdown_pct(cm.price, cm.high)
    cur = level_of(dd, ths)
    level = coin_state.get("level", 0)

    if dd > cfg["drawdown"]["reset_above_pct"]:
        return {"level": 0}, None, dd

    # 기준 고점이 바뀌면(30일 창에서 빠짐 등) 기록을 현재 단계 이하로 맞춘다. 이것만으로는 발송하지 않는다.
    if level and coin_state.get("anchor_time") != cm.high_time:
        level = min(level, cur)

    reached = cur if cur > level else None
    level = max(level, cur)
    if level == 0:
        return {"level": 0}, reached, dd
    return {"level": level, "anchor_high": str(cm.high), "anchor_time": cm.high_time}, reached, dd


def threshold_for(level, ths):
    return ths[level - 1]


def market_wide(dds, cfg):
    mw = cfg["drawdown"]["market_wide"]
    return sum(1 for d in dds.values() if d <= mw["threshold"]) >= mw["min_coins"]
