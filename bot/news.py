"""매체·규제기관 RSS 수집 (명세 3·6절). 키워드에 걸린 항목만 state["news"]에 쌓아 주간 요약에 쓴다.

RSS는 하루~며칠치만 담고 있으므로 매시간 모아 둔다.
"""
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import net

DAY = 86400
SUMMARY_CHARS = 400


def parse_time(raw):
    """{raw, ts, tz_assumed}. 시간대 표기가 없으면 UTC로 보고 tz_assumed=True (명세 2절)."""
    if not raw:
        return None
    raw = raw.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    assumed = dt.tzinfo is None
    if assumed:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"raw": raw, "ts": int(dt.timestamp()), "tz_assumed": assumed}


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_feed(text):
    root = ET.fromstring(text)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        title = strip_html(it.findtext("title"))
        if not link or not title:
            continue
        items.append({"url": link, "title": title,
                      "summary": strip_html(it.findtext("description"))[:SUMMARY_CHARS],
                      "published": parse_time(it.findtext("pubDate"))})
    return items


def _pattern(words):
    parts = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) + r"\b" for w in words]
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)


def score(item, cfg, regulator):
    """키워드 그룹(코인·거래소·규제)이 몇 개 걸렸는지. 규제기관 항목은 크립토 키워드가 있어야 한다."""
    kw = cfg["news"]["keywords"]
    text = item["title"] + " " + item["summary"]
    groups = [g for g, words in kw.items() if _pattern(words).search(text)]
    if regulator and "crypto" not in groups:
        return 0
    return len(groups)


def collect(state, cfg, now_ts, fetch_text=net.fetch_text, mark=None, log=print):
    nc = cfg["news"]
    store = {n["url"]: n for n in state.get("news", [])}
    for feed in nc["feeds"]:
        headers = {"User-Agent": os.environ["SEC_USER_AGENT"]} if feed["name"] == "SEC" and os.environ.get("SEC_USER_AGENT") else None
        status, text = fetch_text(feed["url"], headers=headers)
        try:
            items = parse_feed(text) if status == 200 and text else None
        except ET.ParseError:
            items = None
        if mark:
            mark(f"rss:{feed['name']}", items is not None)
        if items is None:
            log(f"RSS 실패: {feed['name']} HTTP{status}")
            continue
        for it in items:
            if it["url"] in store or not it["published"]:
                continue
            s = score(it, cfg, feed.get("regulator", False))
            if s:
                store[it["url"]] = {**it, "source": feed["name"], "score": s}
    cutoff = now_ts - nc["keep_days"] * DAY
    state["news"] = sorted((n for n in store.values() if n["published"]["ts"] >= cutoff),
                           key=lambda n: n["published"]["ts"])
