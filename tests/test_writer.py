"""명세 12절 필수 테스트 16, 17, 31과 AI 문장 검사."""
from bot import extract, state as st, writer
from conftest import HOUR
from test_events import RAW_EXT, go, notice_item

VALUES = {"estimated_impact": "$6.3B", "frozen": "$3B"}


def reply(**raw):
    return lambda *a: (raw, "")


def test_31_numbers_in_any_form_rejected():
    for s in ["피해액은 63억 달러다.", "삼십억 규모의 자산이 동결됐다.", "약 30% 하락했다.", "수십억 원이 빠져나갔다.",
              "6.3 billion was affected.", "동결액은 피해의 절반이다.", "10월 30일 공지했다."]:
        assert not writer.check_sentence(s, VALUES)[0], s


def test_placeholders_and_plain_sentences_pass():
    s = "바이낸스는 추정 영향이 {M:estimated_impact} 규모라고 밝혔고 {M:frozen}가 동결됐다."
    assert writer.check_sentence(s, VALUES) == (True, "")
    assert writer.fill(s, VALUES) == "바이낸스는 추정 영향이 $6.3B 규모라고 밝혔고 $3B가 동결됐다."
    assert writer.check_sentence("바이낸스는 출금을 일시 중단했다.", VALUES)[0]


def test_unknown_placeholder_or_advice_rejected():
    assert not writer.check_sentence("탈취액은 {M:stolen}이다.", VALUES)[0]
    assert not writer.check_sentence("지금은 추가 매수를 고려할 만하다.", VALUES)[0]
    assert not writer.check_sentence("가격이 반등할 가능성이 있다.", VALUES)[0]


def summary_ok():
    return reply(sentences=["바이낸스는 해킹으로 추정 영향이 {M:estimated_impact} 규모라고 밝혔다.",
                            "출금은 보안 점검이 끝날 때까지 일시 중단됐다."])


def run_notice(cfg, ai_call, prices=None):
    item = notice_item()
    v = extract.verify(RAW_EXT, item["title"] + "\n\n" + item["text"])
    return go(cfg, st.empty(), [item], {item["id"]: v}, now=item["published"]["ts"] + HOUR,
              ai_call=ai_call, prices=prices)


def test_ai_summary_rendered_with_values_filled(cfg):
    _, out, _ = run_notice(cfg, summary_ok())
    msg = out.sent[0]
    assert "<b>요약</b> <i>(AI 작성 · 아래 원문 기반)</i>" in msg
    assert "추정 영향이 $6.3B 규모라고 밝혔다." in msg


def test_16_ai_number_not_in_source_drops_ai_text(cfg):
    _, out, _ = run_notice(cfg, reply(sentences=["바이낸스는 70억 달러 피해를 입었다."]))
    msg = out.sent[0]
    assert "<b>요약</b>" not in msg and "70억" not in msg
    assert "• 추정 영향: $6.3B" in msg  # 템플릿은 그대로


def test_17_credit_exhausted_sends_template(cfg):
    new, out, _ = run_notice(cfg, lambda *a: (None, "Claude usage limit reached"), prices={"BTC": 85})
    assert len(out.sent) == 1
    msg = out.sent[0]
    assert "[사건 브리핑 | 보안 사고 · Binance]" in msg and "<b>요약</b>" not in msg
    assert "원인: 원인 미확인" in msg
    assert new["sources"]["ai:claude"]["ok"] is False


ARTICLES = [{"source": "CoinDesk", "url": "https://cd/x", "title": "Bitcoin slides after Binance hack",
             "summary": "Bitcoin fell as traders reacted to the Binance hack, analysts said."}]


def test_cause_requires_quote_from_article():
    good = reply(found=True, article_id=0, source_quote="Bitcoin fell as traders reacted to the Binance hack",
                 cause="바이낸스 해킹 소식에 트레이더들이 반응하며 비트코인이 하락했다.")
    c = writer.drawdown_cause(ARTICLES, good, "m", log=lambda *_: None)
    assert c["source"] == "CoinDesk" and c["url"] == "https://cd/x"
    bad = reply(found=True, article_id=0, source_quote="Bitcoin fell because of the Fed", cause="연준 때문에 하락했다.")
    assert writer.drawdown_cause(ARTICLES, bad, "m", log=lambda *_: None) is None
    none = reply(found=False, article_id=-1, source_quote="", cause="")
    assert writer.drawdown_cause(ARTICLES, none, "m", log=lambda *_: None) is None
    wrong_id = reply(found=True, article_id=5, source_quote="x", cause="y")
    assert writer.drawdown_cause(ARTICLES, wrong_id, "m", log=lambda *_: None) is None


def test_cause_rendered_in_drawdown_briefing(cfg):
    from bot.briefing import cause_lines
    lines = cause_lines({"cause": "해킹 소식에 하락했다.", "quote": "Bitcoin fell", "source": "CoinDesk", "url": "u"})
    assert lines[0].startswith("원인: 해킹 소식에 하락했다. (AI 요약 · 출처 <a href=\"u\">CoinDesk</a>)")
