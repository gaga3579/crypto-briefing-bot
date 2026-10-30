"""명세 9절 운영: 소스 24시간 연속 실패 경고, 내부 오류 격리."""
from bot import state as st
from bot.main import run
from conftest import NOW, HOUR, OFFLINE, Outbox, empty_rss, make_coinbase, make_kraken

PRICES = {s: {"price": 100} for s in ["BTC", "ETH", "SOL", "XRP"]}


def go(cfg, state, now, kraken_ok=True, problems=None, **kw):
    out = Outbox()
    kr = {s: 100 for s in PRICES} | {"USDT": 1, "USDC": 1} if kraken_ok else {}
    opts = {**OFFLINE, **kw}
    new = run(cfg, state, now, out, fetch_coinbase=make_coinbase(PRICES), fetch_kraken=make_kraken(kr),
              fetch_text=empty_rss, problems=problems, log=lambda *_: None, **opts)
    return new, out


def alerts(out):
    return [m for m in out.sent if m.startswith("<b>[운영 경고")]


def test_source_failing_24h_alerts_once_until_recovered(cfg):
    s, out = go(cfg, st.empty(), NOW, kraken_ok=False)
    assert s["sources"]["kraken"]["ok"] is False and alerts(out) == []
    s, out = go(cfg, s, NOW + 23 * HOUR, kraken_ok=False)
    assert alerts(out) == []
    s, out = go(cfg, s, NOW + 24 * HOUR, kraken_ok=False)
    assert len(alerts(out)) == 1 and "• kraken: " in alerts(out)[0]
    s, out = go(cfg, s, NOW + 25 * HOUR, kraken_ok=False)
    assert alerts(out) == []  # 복구 전 재발송 없음
    s, out = go(cfg, s, NOW + 26 * HOUR)  # 복구
    s, out = go(cfg, s, NOW + 27 * HOUR, kraken_ok=False)
    s, out = go(cfg, s, NOW + 51 * HOUR, kraken_ok=False)
    assert len(alerts(out)) == 1  # 다시 실패하면 다시 경고


def test_internal_error_does_not_block_drawdown(cfg):
    def boom(state):
        raise RuntimeError("source parser bug")
    problems = []
    state = {**st.empty(), "coins": {}}
    prices = {**PRICES, "BTC": {"price": 85}}
    out = Outbox()
    new = run(cfg, state, NOW, out, fetch_coinbase=make_coinbase(prices),
              fetch_kraken=make_kraken({s: v["price"] for s, v in prices.items()}), fetch_text=empty_rss,
              problems=problems, log=lambda *_: None, **{**OFFLINE, "official_fn": boom})
    assert len(out.sent) == 1 and "[하락 브리핑" in out.sent[0]
    assert problems and "events: RuntimeError: source parser bug" in problems[0]
    assert new["sources"]["internal:events"]["ok"] is False


def test_telegram_failure_reported_as_problem(cfg):
    problems = []
    prices = {**PRICES, "BTC": {"price": 85}}
    out = Outbox(fail=True)
    new = run(cfg, st.empty(), NOW, out, fetch_coinbase=make_coinbase(prices),
              fetch_kraken=make_kraken({s: v["price"] for s, v in prices.items()}), fetch_text=empty_rss,
              problems=problems, log=lambda *_: None, **OFFLINE)
    assert any("텔레그램 발송 실패" in p for p in problems) and len(new["unsent"]) == 1
