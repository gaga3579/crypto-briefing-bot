"""사건·후속 브리핑 템플릿 (명세 5절). 수치는 검증된 추출값을 코드가 넣고, 근거는 원문 인용으로 붙인다."""
from html import escape

from .briefing import kst, price_table
from .events import LABELS

SEP = "\n\n━━━━━━━━━━━━\n\n"
METRIC_LABELS = {"estimated_impact": "추정 영향", "stolen": "탈취", "frozen": "동결", "recovered": "회수",
                 "confirmed_loss": "확정 손실"}
WITHDRAWAL_LABELS = {"suspended": "출금 중단", "resumed": "출금 재개", "partial": "출금 일부 제한",
                     "normal": "출금 정상"}
SOURCE_LABELS = {"binance": "Binance 공지", "binance-tg": "Binance 텔레그램", "okx": "OKX 공지",
                 "bybit": "Bybit 텔레그램", "status:coinbase": "Coinbase 상태 페이지",
                 "status:kraken": "Kraken 상태 페이지", "status:solana": "Solana 상태 페이지",
                 "kraken-blog": "Kraken 블로그"}


def money(value, currency, usd=None):
    if currency == "USD":
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(value) >= div:
                return f"${f'{value / div:.2f}'.rstrip('0').rstrip('.')}{unit}"
        return f"${value:,.0f}"
    s = f"{value:,.8f}".rstrip("0").rstrip(".") + f" {currency}"
    p = (usd or {}).get(currency)
    return s + (f" (≈{money(value * float(p), 'USD')} 환산값)" if p else "")


def quote(q):
    return f"  <i>\"{escape(q.strip())}\"</i>"


def ext_lines(ext, usd):
    out = []
    if ext["metrics"]:
        out.append("<b>확인된 수치</b>")
        for m in ext["metrics"]:
            out += [f"• {METRIC_LABELS[m['type']]}: {escape(money(m['value'], m['currency'], usd))}",
                    quote(m["source_quote"])]
    w = ext["withdrawal_status"]
    if w["status"] != "unknown":
        scope = "전체 자산" if w["scope"] == "all_assets" else (", ".join(w["assets"]) or "범위 미확인")
        out += ["<b>입출금</b>", f"• {WITHDRAWAL_LABELS[w['status']]} ({escape(scope)})", quote(w["source_quote"])]
    if ext["facts"]:
        out.append("<b>원문 발췌</b>")
        out += [f"• <i>\"{escape(f['source_quote'].strip())}\"</i>" for f in ext["facts"][:5]]
    return out


def source_line(meta):
    label = SOURCE_LABELS.get(meta["source"], meta["source"])
    return (f"출처: <a href=\"{escape(meta['url'], quote=True)}\">{escape(label)}: {escape(meta['title'][:120])}</a>"
            f" · {kst(meta['ts'], '%m/%d %H:%M KST')}")


def references(state, inc, now, limit=3):
    """참고 보도: 사건에 붙은 매체 기사 + 수집된 뉴스 중 엔티티를 언급한 최근 기사 (출처 표기)."""
    seen, out = set(), []
    name = inc["entity"].lower()
    news = [n for n in reversed(state.get("news", [])) if now - n["published"]["ts"] <= 2 * 86400
            and name in (n["title"] + " " + n["summary"]).lower()]
    pool = [{"source": m["source"], "url": m["url"], "title": m["title"]} for m in reversed(inc["media"])] + news
    for n in pool:
        if n["url"] in seen:
            continue
        seen.add(n["url"])
        out.append(f"• [{escape(n['source'])}] <a href=\"{escape(n['url'], quote=True)}\">{escape(n['title'])}</a>")
        if len(out) == limit:
            break
    return out


def status_line(inc):
    if inc["status"] == "open":
        return f"진행 상태: 진행 중 ({kst(inc['opened'], '%m/%d %H:%M')} 첫 브리핑)"
    return f"진행 상태: 종료 ({escape(inc['closed_reason'] or '')})"


def render(part, state, markets, dds, usd, now):
    inc = state.get("incidents", {}).get(part.get("incident") or "")
    kind = part["kind"]
    if kind == "event-candidate":
        it = part["item"]
        return "\n".join(["<b>[사건 후보 | 자동 판정 불가]</b>",
                          "위험 키워드가 걸린 공식 공지입니다. AI 추출에 실패해 규칙 판정을 하지 못했습니다. 원문을 확인하세요.",
                          source_line({**it, "ts": it["published"]["ts"]})])
    label = LABELS[inc["event_type"]]
    if kind == "followup":
        out = [f"<b>[후속 브리핑 | {label} · {escape(inc['entity'])}]</b>",
               f"사유: {escape(', '.join(part['reasons']))}", source_line(inc["item"]), ""]
        out += ext_lines(part["ext"], usd) + ["", status_line(inc)]
        return "\n".join(out)
    out = [f"<b>[사건 브리핑 | {label} · {escape(inc['entity'])}]</b>"]
    if kind == "liveness":
        out += ["<b>공식 미확인</b> (블록 생성 지표)",
                f"마지막 블록: {part['age']:.0f}분 전 (기준 {part['limit']}분, {part['runs']}회 연속 초과)"]
    elif kind == "depeg":
        out += [f"Kraken {part['coin']}/USD {float(part['price']):.4f} "
                f"(기준 $0.97 미만, {part['runs']}회 연속 실행에서 확인)"]
    else:
        if not inc["official"]:
            outlets = part.get("outlets", [])
            out.append(f"<b>공식 미확인</b> (보도 {len(outlets)}곳: {escape(', '.join(outlets))})")
        out += [source_line(inc["item"]), ""] + ext_lines(part["ext"], usd)
    out += ["", "<b>가격 현황</b>",
            price_table(markets, dds) if markets and dds else "확인 불가 (이번 실행 가격 판정 보류)"]
    refs = references(state, inc, now)
    if refs:
        out += ["", "<b>참고 보도</b>", *refs]
    out += ["", status_line(inc), f"<i>{kst(now)} 기준</i>"]
    return "\n".join(out)


def ongoing(state):
    """하락 브리핑의 '진행 중 사건' 섹션. 없으면 None."""
    incs = [i for i in state.get("incidents", {}).values() if i["status"] == "open" and i["briefed"]]
    if not incs:
        return None
    lines = ["<b>진행 중 사건</b>"]
    for i in sorted(incs, key=lambda i: i["opened"]):
        tag = "" if i["official"] else " · 공식 미확인"
        lines.append(f"• {escape(i['entity'])} {LABELS[i['event_type']]} ({kst(i['opened'], '%m/%d')}부터{tag})")
    return "\n".join(lines)
