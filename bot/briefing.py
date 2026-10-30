"""브리핑 템플릿. 모든 숫자는 코드가 넣는다 (명세 4·7절)."""
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from .drawdown import threshold_for, thresholds

KST = ZoneInfo("Asia/Seoul")
MINUS = "−"


def kst(ts_or_dt, fmt="%Y-%m-%d %H:%M KST"):
    dt = ts_or_dt if isinstance(ts_or_dt, datetime) else datetime.fromtimestamp(ts_or_dt, timezone.utc)
    return dt.astimezone(KST).strftime(fmt)


def fmt_price(p):
    return f"{p:,.2f}" if p >= 10 else f"{p:,.4f}"


def fmt_pct(p, width=0):
    s = "n/a" if p is None else f"{Decimal(p):+.2f}%"
    return s.rjust(width)


def signed(p):
    """본문용 퍼센트: −20%처럼 유니코드 마이너스."""
    return f"{MINUS}{abs(p)}%" if p < 0 else f"{p}%"


def price_table(markets, dds, marked=()):
    lines = [f"{'COIN':<5}{'PRICE':>11}{'30D-H':>9}{'24H':>8}{'7D':>8}"]
    for sym, cm in markets.items():
        name = sym + ("*" if sym in marked else "")
        lines.append(f"{name:<5}{fmt_price(cm.price):>11}{fmt_pct(dds[sym], 9)}"
                     f"{fmt_pct(cm.change_24h, 8)}{fmt_pct(cm.change_7d, 8)}")
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def drawdown_briefing(markets, dds, reached, states, wide, cfg, now, events_text=None):
    ths = thresholds(cfg)
    title = "시장 전반 하락" if wide else "개별 코인 하락"
    fired = ", ".join(f"{s} {signed(threshold_for(lv, ths))} 단계" for s, lv in reached.items())
    out = [f"<b>[하락 브리핑 | {title}]</b>", f"발동: {escape(fired)}", "",
           price_table(markets, dds, marked=reached.keys()),
           "* 발동 코인. 30D-H는 30일 고점 대비.", "",
           "원인: 원인 미확인"]
    if events_text:
        out += ["", events_text]
    out += ["", "<b>다음 알림 조건</b>"]
    for sym, cm in markets.items():
        lv = states[sym].get("level", 0)
        if lv >= len(ths):
            out.append(f"{sym}: 추가 단계 없음")
            continue
        t = ths[lv]
        trigger = cm.high * (1 + Decimal(t) / 100)
        out.append(f"{sym}: {signed(t)} 단계, ${fmt_price(trigger)} 이하 "
                   f"(30일 고점 ${fmt_price(cm.high)}, {kst(cm.high_time, '%m/%d %H시')})")
    out += ["", f"<i>{kst(now)} 기준 · Coinbase 시세</i>"]
    return "\n".join(out)
