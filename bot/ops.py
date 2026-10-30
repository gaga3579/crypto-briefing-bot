"""운영 경고 (명세 9절): 소스가 24시간 이상 연속 실패하면 소스별 1회 텔레그램 경고. 복구 전까지 재발송하지 않는다.

소스 상태는 성공↔실패 전환 때만 새로 기록되므로(main.mark_source), 복구되면 alerted 표시도 함께 사라진다.
"""
from datetime import datetime
from html import escape

from .briefing import kst

IMPACT = [("coinbase", "가격 수집 실패로 하락 판정이 보류된다"),
          ("kraken", "가격 교차 검증과 스테이블코인 감시를 할 수 없다"),
          ("rss:", "주간 뉴스와 매체 교차 확인에 공백이 생긴다"),
          ("src:", "이 공식 소스의 사건 공지를 감시하지 못한다"),
          ("chain:", "이 체인의 블록 생성 지표를 감시하지 못한다"),
          ("ai:", "AI 추출·문장이 빠지고 템플릿(사건 후보)만 발송된다"),
          ("internal:", "봇 내부 오류. GitHub Actions 로그 확인 필요")]


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def impact(name):
    return next((text for prefix, text in IMPACT if name.startswith(prefix)), "")


def source_alert(state, cfg, now):
    """새로 24시간을 넘긴 실패 소스가 있으면 발송 조각 하나, 없으면 None."""
    limit = cfg["ops"]["source_failure_alert_hours"] * 3600
    failing = sorted((n, s) for n, s in state.get("sources", {}).items()
                     if not s["ok"] and not s.get("alerted") and now - _ts(s["since"]) >= limit)
    if not failing:
        return None
    lines = [f"<b>[운영 경고 | 소스 {cfg['ops']['source_failure_alert_hours']}시간 이상 실패]</b>"]
    for name, s in failing:
        s["alerted"] = True
        lines.append(f"• {escape(name)}: {kst(_ts(s['since']), '%m/%d %H:%M')}부터 실패")
        if impact(name):
            lines.append(f"  {impact(name)}")
    lines += ["", "복구되기 전까지 같은 소스로 다시 경고하지 않는다.", f"<i>{kst(now)} 기준</i>"]
    names = ", ".join(n for n, _ in failing)
    return {"id": f"ops:{int(now)}", "kind": "ops", "summary": f"운영 경고: {names}", "text": "\n".join(lines)}
