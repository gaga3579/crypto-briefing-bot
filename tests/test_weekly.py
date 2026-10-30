"""명세 12절 필수 테스트 20, 21, 27과 주간 요약 구성."""
from datetime import datetime, timezone
from decimal import Decimal

from bot import news, state as st, weekly
from bot.briefing import KST
from bot.main import run
from conftest import OFFLINE, Outbox, make_coinbase, make_kraken

HOUR = 3600


def kst_ts(*args):
    return int(datetime(*args, tzinfo=KST).timestamp())


MON_0825 = kst_ts(2026, 11, 2, 8, 25)
WEEK_START, WEEK_END = kst_ts(2026, 10, 26, 8, 0), kst_ts(2026, 11, 2, 8, 0)

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Coinbase faces SEC lawsuit over staking</title><link>https://ex.com/a</link>
<pubDate>Wed, 28 Oct 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;The court hearing is set for Nov. 4. Bitcoin fell.&lt;/p&gt;</description></item>
<item><title>Bitcoin ETF flows rise</title><link>https://ex.com/b</link>
<pubDate>Thu, 29 Oct 2026 10:00:00 +0000</pubDate><description>Flows rose on Oct. 1 data.</description></item>
<item><title>Celebrity chef opens restaurant</title><link>https://ex.com/c</link>
<pubDate>Thu, 29 Oct 2026 11:00:00 +0000</pubDate><description>Nothing about markets.</description></item>
<item><title>No timezone item about Ethereum</title><link>https://ex.com/d</link>
<pubDate>Thu, 29 Oct 2026 12:00:00</pubDate><description></description></item>
</channel></rss>"""

SEC_RSS = """<rss><channel>
<item><title>SEC charges investment adviser</title><link>https://sec.gov/1</link>
<pubDate>Thu, 29 Oct 2026 12:42:37 -0400</pubDate><description>Fraud case.</description></item>
<item><title>SEC statement on crypto asset custody</title><link>https://sec.gov/2</link>
<pubDate>Thu, 29 Oct 2026 13:00:00 -0400</pubDate><description></description></item>
</channel></rss>"""


def fake_text(url, headers=None):
    return 200, SEC_RSS if "sec.gov" in url else RSS


def test_20_window_fixed_to_monday_0800():
    assert weekly.window(MON_0825) == (WEEK_START, WEEK_END)
    assert weekly.window(kst_ts(2026, 11, 2, 7, 59)) == (kst_ts(2026, 10, 19, 8, 0), WEEK_START)
    assert weekly.window(kst_ts(2026, 11, 8, 23, 0)) == (WEEK_START, WEEK_END)  # 일요일 밤도 같은 구간


def test_21_feed_time_without_timezone_flagged():
    t = news.parse_time("Thu, 29 Oct 2026 12:00:00")
    assert t["tz_assumed"] is True and t["raw"] == "Thu, 29 Oct 2026 12:00:00"
    t = news.parse_time("Thu, 29 Oct 2026 12:42:37 -0400")
    assert t["tz_assumed"] is False and t["ts"] == int(datetime(2026, 10, 29, 16, 42, 37, tzinfo=timezone.utc).timestamp())


def test_news_collect_filters_and_gates_regulators(cfg):
    s = st.empty()
    news.collect(s, cfg, MON_0825, fetch_text=fake_text, log=lambda *_: None)
    urls = {n["url"] for n in s["news"]}
    assert "https://ex.com/a" in urls and "https://ex.com/b" in urls
    assert "https://ex.com/c" not in urls  # 키워드 없음
    assert "https://sec.gov/1" not in urls and "https://sec.gov/2" in urls  # 규제기관은 크립토 키워드 필요
    d = next(n for n in s["news"] if n["url"] == "https://ex.com/d")
    assert d["published"]["tz_assumed"] is True


def market_spec():
    return {sym: {"price": 100} for sym in ["BTC", "ETH", "SOL", "XRP"]}


def go(cfg, state, now, out=None, force=False, stats=None, kraken=None):
    out = out or Outbox()
    kraken = {k: 100 for k in market_spec()} if kraken is None else kraken
    new = run(cfg, state, now, out, fetch_coinbase=make_coinbase(market_spec()),
              fetch_kraken=make_kraken(kraken), fetch_text=fake_text,
              run_stats=lambda ws, we: stats, force_weekly=force, log=lambda *_: None, **OFFLINE)
    return new, out


def test_first_deploy_does_not_send_weekly(cfg):
    s, out = go(cfg, st.empty(), MON_0825)
    assert out.sent == [] and s["weekly_last"] == "2026-11-02"


def test_27_missed_monday_run_sends_once(cfg):
    base = {**st.empty(), "weekly_last": "2026-10-26"}
    s1, out1 = go(cfg, base, kst_ts(2026, 11, 2, 10, 7))  # 08:07, 09:07 실행이 누락됨
    assert len(out1.sent) == 1 and out1.sent[0].startswith("<b>[주간 요약 | 10/26 − 11/02]</b>")
    s2, out2 = go(cfg, s1, kst_ts(2026, 11, 2, 11, 7))
    assert out2.sent == []


def test_weekly_content(cfg):
    base = {**st.empty(), "weekly_last": "2026-10-26",
            "briefings": [{"id": "dd-x", "ts": kst_ts(2026, 10, 31, 0, 29), "kind": "drawdown",
                           "summary": "하락 브리핑(시장 전반): BTC −10%"},
                          {"id": "dd-old", "ts": kst_ts(2026, 10, 20, 0, 0), "kind": "drawdown",
                           "summary": "지난주 것"}],
            "sources": {"coinbase": {"ok": True, "since": "2026-10-30T15:00:00Z"},
                        "kraken": {"ok": False, "since": "2026-10-30T15:00:00Z"}}}
    stats = {"success": 160, "failure": 2, "total": 162, "scheduled": 158}
    _, out = go(cfg, base, MON_0825, stats=stats, kraken={})
    assert out.sent[0].startswith("<b>[운영 경고")  # kraken이 24시간 넘게 실패 중
    msg = out.sent[1]
    assert "집계: 2026-10-26 08:00 ~ 2026-11-02 08:00 KST" in msg
    assert "BTC    +0.00%   100.00   100.00  +0.00%" in msg
    assert "10/31 00:29 하락 브리핑(시장 전반): BTC −10%" in msg and "지난주 것" not in msg
    assert "Coinbase faces SEC lawsuit over staking" in msg and "Celebrity chef" not in msg
    assert "SEC statement on crypto asset custody" in msg
    assert "The court hearing is set for Nov. 4." in msg and "Oct. 1" not in msg
    assert "실행: 성공 160 · 실패 2 (예약 실행 158회, 기대 168회 대비 누락 10회)" in msg
    assert "Kraken 10/31 00:00부터 실패 중" in msg


def test_weekly_missing_data_says_unknown(cfg):
    base = {**st.empty(), "weekly_last": "2026-10-26"}
    out = Outbox()
    run(cfg, base, MON_0825, out, fetch_coinbase=make_coinbase({**market_spec(), "BTC": {"price": 100, "fail": True}}),
        fetch_kraken=make_kraken({}), fetch_text=lambda u, headers=None: (500, None),
        force_weekly=False, log=lambda *_: None, **OFFLINE)
    msg = out.sent[0]
    assert "확인 불가: BTC 주간 시세" in msg
    assert "확인 불가: 현재 30일 고점 대비" in msg
    assert "3. 주요 뉴스</b>\n확인 불가" in msg
    assert "실행 횟수: 확인 불가" in msg


def test_run_stats_paginates_and_counts():
    pages = {1: [{"status": "completed", "conclusion": "success", "event": "schedule"}] * 100,
             2: [{"status": "completed", "conclusion": "failure", "event": "schedule"},
                 {"status": "completed", "conclusion": "success", "event": "workflow_dispatch"},
                 {"status": "in_progress", "conclusion": None, "event": "schedule"}]}

    def get(url, headers=None):
        page = int(url.rsplit("page=", 1)[1])
        assert "created=2026-10-25T23:00:00Z..2026-11-01T22:59:59Z" in url
        return 200, {"workflow_runs": pages[page]}

    stats = weekly.run_stats("o/r", "t", WEEK_START, WEEK_END, get=get)
    assert stats == {"success": 101, "failure": 1, "total": 102, "scheduled": 101}


def test_week_stats_from_candles():
    candles = {}
    for k in range(168):
        t = WEEK_START + k * HOUR
        candles[t] = (Decimal(90), Decimal(110 if k == 50 else 100), Decimal(100), Decimal(100))
    candles[WEEK_END - HOUR] = (Decimal(80), Decimal(100), Decimal(100), Decimal(95))
    ch, hi, lo = weekly.week_stats(candles, WEEK_START, WEEK_END)
    assert ch == Decimal(-5) and hi == 110 and lo == 80


def test_upcoming_ignores_dates_not_after_publication():
    pub = kst_ts(2026, 11, 3, 9, 0)
    st_ = {"news": [{"url": "u", "source": "CoinDesk", "title": "Your day-ahead look for Nov. 3, 2026",
                     "summary": "A vote is due on Nov. 5.", "published": {"ts": pub}}]}
    items = weekly.upcoming(st_, WEEK_END, pub + HOUR)
    assert [s for _, s, _ in items] == ["A vote is due on Nov. 5."]
