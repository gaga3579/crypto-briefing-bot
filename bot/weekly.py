"""주간 요약 (명세 6절). 월 08:00 KST 이후 첫 hourly 실행에서 보낸다. 집계 구간은 실행 시각과 무관하게 고정."""
import re
from datetime import datetime, timedelta, timezone
from html import escape

from . import net
from .briefing import KST, MINUS, kst

HOUR, DAY = 3600, 86400
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")


def window(now_ts):
    """(시작, 끝) epoch. 끝 = now 이전의 가장 최근 월요일 08:00 KST."""
    now = datetime.fromtimestamp(now_ts, KST)
    end = (now - timedelta(days=now.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=7)
    return int((end - timedelta(days=7)).timestamp()), int(end.timestamp())


def due(state, now_ts, force=False):
    """(보낼지, 주 키). 처음 배포된 주는 보내지 않고 기준만 잡는다."""
    _, end = window(now_ts)
    key = kst(end, "%Y-%m-%d")
    if force:
        return True, key
    last = state.get("weekly_last")
    if last is None:
        state["weekly_last"] = key
        return False, key
    return last != key, key


# ---------- 1. 가격 ----------

def fmt_compact(p):
    if p is None:
        return "n/a"
    return f"{p:,.0f}" if p >= 1000 else (f"{p:,.2f}" if p >= 10 else f"{p:,.4f}")


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def week_stats(candles, ws, we):
    """(변화율, 고가, 저가). 구간 봉이 빠졌으면 None."""
    inside = {t: c for t, c in candles.items() if ws <= t < we}
    first, last = inside.get(ws), inside.get(we - HOUR)
    if not first or not last or len(inside) < (we - ws) // HOUR * 0.9:
        return None
    change = (last[3] / first[2] - 1) * 100
    return change, max(c[1] for c in inside.values()), min(c[0] for c in inside.values())


def price_section(symbols, candles, dds, ws, we):
    lines = [f"{'COIN':<5}{'WEEK':>8}{'HIGH':>9}{'LOW':>9}{'30D-H':>8}"]
    missing = []
    for sym in symbols:
        stats = week_stats(candles.get(sym, {}), ws, we)
        dd = (dds or {}).get(sym)
        if not stats:
            missing.append(sym)
            lines.append(f"{sym:<5}{'n/a':>8}{'n/a':>9}{'n/a':>9}{fmt_pct(dd):>8}")
            continue
        ch, hi, lo = stats
        lines.append(f"{sym:<5}{fmt_pct(ch):>8}{fmt_compact(hi):>9}{fmt_compact(lo):>9}{fmt_pct(dd):>8}")
    out = ["<pre>" + escape("\n".join(lines)) + "</pre>",
           "WEEK 주간 변화율 · HIGH/LOW 주간 최고·최저(USD) · 30D-H 현재 30일 고점 대비"]
    if missing:
        out.append(f"확인 불가: {', '.join(missing)} 주간 시세")
    if dds is None:
        out.append("확인 불가: 현재 30일 고점 대비 (이번 실행 판정 보류)")
    return out


# ---------- 2. 브리핑 ----------

def briefing_section(state, ws, we):
    sent = [b for b in state.get("briefings", []) if ws <= b["ts"] < we and b["kind"] != "weekly"]
    if not sent:
        return ["이번 주 발송한 브리핑 없음"]
    return [f"• {kst(b['ts'], '%m/%d %H:%M')} {escape(b['summary'])}" for b in sent]


# ---------- 3. 뉴스 ----------

def top_news(state, ws, we, limit):
    items = [n for n in state.get("news", []) if ws <= n["published"]["ts"] < we]
    items.sort(key=lambda n: (-n["score"], -n["published"]["ts"]))
    picked, titles = [], set()
    for n in items:
        key = n["title"].lower()
        if key in titles:
            continue
        titles.add(key)
        picked.append(n)
        if len(picked) == limit:
            break
    return picked


def news_section(state, ws, we, limit):
    picked = top_news(state, ws, we, limit)
    if not picked:
        return ["확인 불가 (수집된 관련 기사 없음)"]
    return [f"• [{escape(n['source'])}] <a href=\"{escape(n['url'], quote=True)}\">{escape(n['title'])}</a>"
            for n in picked]


# ---------- 4. 다음 주 일정 ----------

ABBREV = re.compile(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mr|Ms|Dr|St|No|vs|Inc|Corp|U\.S)\.")


def sentences(text):
    """약어(Nov., U.S. 등) 뒤에서는 문장을 자르지 않는다."""
    protected = ABBREV.sub(lambda m: m.group(1).replace(".", "\x00") + "\x00", text)
    return [p.replace("\x00", ".") for p in re.split(r"(?<=[.!?])\s+", protected)]


def upcoming(state, we, now_ts):
    """뉴스 원문에서 [이번 월요일, +7일) 날짜가 명시된 문장을 그대로 인용한다.
    상대 날짜(next week 등)와 기사 발행일 이전·당일 날짜는 쓰지 않는다."""
    start = datetime.fromtimestamp(we, KST).date()
    end = start + timedelta(days=7)
    found = []
    for n in state.get("news", []):
        if not (we - 7 * DAY <= n["published"]["ts"] <= now_ts):
            continue
        pub = datetime.fromtimestamp(n["published"]["ts"], KST).date()
        for sentence in sentences(n["title"] + ". " + n["summary"]):
            for m in DATE_RE.finditer(sentence):
                month, day = MONTHS[m.group(1)[:3].lower()], int(m.group(2))
                year = pub.year + (1 if month < pub.month - 6 else 0)
                try:
                    d = datetime(year, month, day).date()
                except ValueError:
                    continue
                if start <= d < end and d > pub:  # 기사 발행일 이후의 날짜만 일정으로 본다
                    found.append((d, sentence.strip()[:200], n))
                    break
    seen, out = set(), []
    for d, sentence, n in sorted(found, key=lambda x: x[0]):
        if sentence in seen:
            continue
        seen.add(sentence)
        out.append((d, sentence, n))
    return out[:5]


def upcoming_section(state, we, now_ts):
    items = upcoming(state, we, now_ts)
    if not items:
        return ["확인 불가 (뉴스에서 다음 주 날짜가 명시된 일정을 찾지 못함)"]
    return [f"• \"{escape(s)}\" — <a href=\"{escape(n['url'], quote=True)}\">{escape(n['source'])}</a>"
            for _, s, n in items]


# ---------- 5. 봇 상태 ----------

def run_stats(repo, token, ws, we, get=net.request):
    """{success, failure, total, scheduled} 또는 None (조회 실패)."""
    if not repo or not token:
        return None
    utc = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    rng = f"{utc(ws)}..{utc(we - 1)}"
    counts = {"success": 0, "failure": 0, "total": 0, "scheduled": 0}
    for page in range(1, 6):
        s, d = get(f"https://api.github.com/repos/{repo}/actions/workflows/hourly.yml/runs"
                   f"?created={rng}&per_page=100&page={page}",
                   headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        if s != 200 or not d:
            return None
        runs = d.get("workflow_runs", [])
        for r in runs:
            if r.get("status") != "completed":
                continue
            counts["total"] += 1
            counts["scheduled"] += r.get("event") == "schedule"
            counts["success" if r.get("conclusion") == "success" else "failure"] += 1
        if len(runs) < 100:
            return counts
    return counts


SOURCE_NAMES = {"coinbase": "Coinbase", "kraken": "Kraken"}


def status_section(state, stats, cfg):
    out = []
    if stats is None:
        out.append("실행 횟수: 확인 불가 (GitHub API 조회 실패)")
    else:
        miss = max(cfg["weekly"]["expected_runs"] - stats["scheduled"], 0)
        out.append(f"실행: 성공 {stats['success']} · 실패 {stats['failure']} "
                   f"(예약 실행 {stats['scheduled']}회, 기대 {cfg['weekly']['expected_runs']}회 대비 누락 {miss}회)")
    parts = []
    for name, s in sorted(state.get("sources", {}).items()):
        label = SOURCE_NAMES.get(name, name.replace("rss:", ""))
        parts.append(f"{label} 정상" if s["ok"] else f"{label} {kst(_ts(s['since']), '%m/%d %H:%M')}부터 실패 중")
    out.append("소스: " + (" · ".join(parts) if parts else "확인 불가"))
    return out


def _ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


# ---------- 조립 ----------

def build(state, cfg, now_ts, candles, dds, stats):
    ws, we = window(now_ts)
    head = f"{kst(ws, '%m/%d')} {MINUS} {kst(we, '%m/%d')}"
    out = [f"<b>[주간 요약 | {head}]</b>",
           f"집계: {kst(ws, '%Y-%m-%d %H:%M')} ~ {kst(we, '%Y-%m-%d %H:%M')} KST", "",
           "<b>1. 가격</b>", *price_section(cfg["symbols"], candles, dds, ws, we), "",
           "<b>2. 이번 주 브리핑</b>", *briefing_section(state, ws, we), "",
           "<b>3. 주요 뉴스</b>", *news_section(state, ws, we, cfg["news"]["weekly_max"]), "",
           "<b>4. 다음 주 확인할 것</b>", *upcoming_section(state, we, now_ts), "",
           "<b>5. 봇 상태</b>", *status_section(state, stats, cfg), "",
           f"<i>{kst(now_ts)} 작성</i>"]
    return "\n".join(out)
