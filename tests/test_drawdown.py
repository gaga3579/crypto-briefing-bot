"""명세 12절 필수 테스트 1~9, 22~26 (하락 브리핑 범위)."""
from bot import state as st
from bot.main import run
from conftest import NOW, HOUR, OFFLINE, Outbox, empty_rss, make_coinbase, make_kraken

FLAT = {"ETH": 100, "SOL": 100, "XRP": 100}


def spec(**prices):
    s = {sym: {"price": p} for sym, p in {**FLAT, "BTC": 100, **prices}.items()}
    return s


def go(cfg, state, s, now=NOW, out=None, kraken=None):
    out = out or Outbox()
    coinbase = make_coinbase(s)
    kr = make_kraken(kraken or {k: v["price"] for k, v in s.items()})
    new = run(cfg, state, now, out, fetch_coinbase=coinbase, fetch_kraken=kr, fetch_text=empty_rss,
              log=lambda *_: None, **OFFLINE)
    return new, out


def test_01_minus_9_9_no_send(cfg):
    _, out = go(cfg, st.empty(), spec(BTC=90.1))
    assert out.sent == []


def test_02_minus_10_sends_tier(cfg):
    _, out = go(cfg, st.empty(), spec(BTC=90))
    assert len(out.sent) == 1
    assert "발동: BTC −10% 단계" in out.sent[0]


def test_03_first_seen_minus_23_sends_only_20(cfg):
    _, out = go(cfg, st.empty(), spec(BTC=77))
    assert len(out.sent) == 1
    fired = out.sent[0].split("\n")[1]
    assert fired == "발동: BTC −20% 단계"


def test_04_same_tier_not_resent(cfg):
    s1, _ = go(cfg, st.empty(), spec(BTC=88))
    _, out = go(cfg, s1, spec(BTC=85), now=NOW + HOUR)
    assert out.sent == []


def test_05_recover_above_minus_5_then_resend(cfg):
    s1, _ = go(cfg, st.empty(), spec(BTC=89))
    s2, out2 = go(cfg, s1, spec(BTC=96), now=NOW + HOUR)
    assert out2.sent == [] and s2["coins"]["BTC"]["level"] == 0
    _, out3 = go(cfg, s2, spec(BTC=90), now=NOW + 2 * HOUR)
    assert len(out3.sent) == 1 and "BTC −10% 단계" in out3.sent[0]


def test_06_btc_eth_same_run_one_message_with_four_coins(cfg):
    _, out = go(cfg, st.empty(), spec(BTC=80, ETH=79))
    assert len(out.sent) == 1
    msg = out.sent[0]
    assert "BTC −20% 단계, ETH −20% 단계" in msg
    for sym in ["BTC*", "ETH*", "SOL ", "XRP "]:
        assert sym in msg


def test_07_market_wide_label(cfg):
    _, out = go(cfg, st.empty(), spec(BTC=90, ETH=89, SOL=88))
    assert "[하락 브리핑 | 시장 전반 하락]" in out.sent[0]
    _, out = go(cfg, st.empty(), spec(BTC=90, ETH=89))
    assert "[하락 브리핑 | 개별 코인 하락]" in out.sent[0]


def test_08_intra_run_low_not_used(cfg):
    s1, _ = go(cfg, st.empty(), spec(BTC=89))
    s = spec(BTC=82)
    s["BTC"]["low"] = 79  # 실행 사이에 −21% 터치
    s["BTC"]["prev_close"] = 82
    _, out = go(cfg, s1, s, now=NOW + HOUR)
    assert out.sent == []


def test_09_price_failure_or_abnormal_holds(cfg):
    s = spec(BTC=70)
    s["ETH"]["fail"] = True
    new, out = go(cfg, st.empty(), s)
    assert out.sent == [] and new["coins"] == {}
    assert new["sources"]["coinbase"]["ok"] is False
    s = spec(BTC=0)
    s["BTC"]["prev_close"] = 100
    _, out = go(cfg, st.empty(), s)
    assert out.sent == []


def test_22_commit_failure_resends_not_drops(cfg):
    base = st.empty()
    _, out1 = go(cfg, base, spec(BTC=85))
    # 커밋 실패로 state가 저장되지 않음 → 다음 실행은 이전 state로 시작
    _, out2 = go(cfg, base, spec(BTC=85), now=NOW + HOUR)
    assert len(out1.sent) == 1 and len(out2.sent) == 1


def test_23_big_jump_confirmed_by_kraken(cfg):
    s = spec(BTC=45)
    s["BTC"]["prev_close"] = 100
    _, out = go(cfg, st.empty(), s, kraken={"BTC": 45.5, "ETH": 100, "SOL": 100, "XRP": 100})
    assert len(out.sent) == 1 and "BTC −50% 단계" in out.sent[0]


def test_24_big_jump_kraken_disagrees_holds(cfg):
    s = spec(BTC=45)
    s["BTC"]["prev_close"] = 100
    _, out = go(cfg, st.empty(), s, kraken={"BTC": 47.3, "ETH": 100, "SOL": 100, "XRP": 100})
    assert out.sent == []


def test_25_anchor_rolls_out_resets_without_send(cfg):
    s1, _ = go(cfg, st.empty(), spec(BTC=78))  # −22% → level 2
    assert s1["coins"]["BTC"]["level"] == 2
    s = spec(BTC=78)
    s["BTC"].update(high=85, high_age=50)  # 기존 고점이 창 밖으로, 새 고점 85 → −8.24%
    s2, out2 = go(cfg, s1, s, now=NOW + HOUR)
    assert out2.sent == [] and s2["coins"]["BTC"]["level"] == 0
    s = spec(BTC=76.5)
    s["BTC"].update(high=85, high_age=50)  # 새 고점 대비 −10%
    _, out3 = go(cfg, s2, s, now=NOW + 2 * HOUR)
    assert len(out3.sent) == 1 and "BTC −10% 단계" in out3.sent[0]


def test_25b_anchor_change_but_deeper_still_sends(cfg):
    s1, _ = go(cfg, st.empty(), spec(BTC=89))  # level 1
    s = spec(BTC=60)
    s["BTC"].update(high=80, high_age=50, prev_close=60)  # 새 고점 대비 −25%
    _, out = go(cfg, s1, s, now=NOW + HOUR)
    assert len(out.sent) == 1 and "BTC −20% 단계" in out.sent[0]


def test_26_unsent_queue_resent_next_run(cfg):
    s1, out1 = go(cfg, st.empty(), spec(BTC=85), out=Outbox(fail=True))
    assert out1.sent == [] and len(s1["unsent"]) == 1
    s2, out2 = go(cfg, s1, spec(BTC=85), now=NOW + HOUR)
    assert len(out2.sent) == 1 and out2.sent[0].startswith("<i>지연 발송 · 발생 시각 2026-10-31 00:07 KST</i>")
    assert s2["unsent"] == [] and len(s2["sent"]) == 1
