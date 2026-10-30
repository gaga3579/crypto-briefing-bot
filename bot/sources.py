"""사건 소스 수집 (명세 3절). 모든 항목을 같은 모양으로 바꾼다:

{"id", "source", "entity", "official", "title", "text", "url", "published": {raw, ts, tz_assumed}, "group"}
group은 같은 사건을 묶는 소스 고유 키(상태 페이지 incident ID)이며 없으면 None.
"""
import html
import json
import re

from . import net
from .news import parse_time, strip_html

BINANCE_LIST = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=20"
BINANCE_DETAIL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode="
BINANCE_CATALOGS = {49, 157}  # Latest Binance News, Maintenance Updates (1단계 실측)
OKX_ANN = "https://www.okx.com/api/v5/support/announcements"
STATUSPAGES = {"Coinbase": "https://status.coinbase.com", "Kraken": "https://status.kraken.com",
               "Solana": "https://status.solana.com"}
TELEGRAM = {"Binance": "binance_announcements", "Bybit": "Bybit_Announcements"}
KRAKEN_BLOG = "https://blog.kraken.com/feed"
MAX_TEXT = 12000


def item(source, entity, official, id_, title, text, url, published, group=None):
    return {"id": f"{source}:{id_}", "source": source, "entity": entity, "official": official,
            "title": title.strip(), "text": text.strip()[:MAX_TEXT], "url": url,
            "published": published, "group": group}


def ms_time(ms):
    from datetime import datetime, timezone
    raw = str(ms)
    dt = datetime.fromtimestamp(int(ms) / 1000, timezone.utc)
    return {"raw": raw, "ts": int(dt.timestamp()), "tz_assumed": False}


def binance_body_text(body):
    """CMS 본문(JSON 노드 트리)을 줄 단위 텍스트로."""
    out = []

    def walk(n):
        if isinstance(n, list):
            for c in n:
                walk(c)
        elif isinstance(n, dict):
            if n.get("node") == "text":
                out.append(n.get("text", ""))
            for c in n.get("child") or []:
                walk(c)
            if n.get("tag") in ("p", "li", "h1", "h2", "h3", "h4", "br", "tr", "div"):
                out.append("\n")
    walk(json.loads(body))
    text = html.unescape("".join(out)).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", text)


def binance(seen, get=net.request):
    s, d = get(BINANCE_LIST)
    cats = ((d or {}).get("data") or {}).get("catalogs") if s == 200 else None
    if not cats:
        return None
    out = []
    for c in cats:
        if c.get("catalogId") not in BINANCE_CATALOGS:
            continue
        for a in c.get("articles", []):
            iid = f"binance:{a['code']}"
            text = ""
            if iid not in seen:
                s2, d2 = get(BINANCE_DETAIL + a["code"])
                body = ((d2 or {}).get("data") or {}).get("body") if s2 == 200 else None
                text = binance_body_text(body) if body else ""
            out.append(item("binance", "Binance", True, a["code"], a["title"], text,
                            f"https://www.binance.com/en/support/announcement/detail/{a['code']}",
                            ms_time(a["releaseDate"])))
    return out


def okx(seen, get=net.request):
    s, d = get(OKX_ANN)
    if s != 200 or not d or d.get("code") != "0" or not d.get("data"):
        return None
    return [item("okx", "OKX", True, a["url"].rsplit("/", 1)[-1], a["title"], "", a["url"], ms_time(a["pTime"]))
            for a in d["data"][0].get("details", [])]


def telegram_channel(source, entity, channel, fetch_text=net.fetch_text):
    s, page = fetch_text(f"https://t.me/s/{channel}")
    if s != 200 or not page:
        return None
    out = []
    for block in page.split('data-post="')[1:]:
        post = block.split('"', 1)[0]
        m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        t = re.search(r'<time datetime="([^"]+)"', block)
        if not m or not t:
            continue
        text = strip_html(m.group(1).replace("<br/>", "\n"))
        out.append(item(source, entity, True, post.rsplit("/", 1)[-1], text.split("\n")[0][:200], text,
                        f"https://t.me/{post}", parse_time(t.group(1))))
    return out


def statuspage(entity, base, get=net.request):
    s, d = get(f"{base}/api/v2/incidents.json")
    if s != 200 or not d:
        return None
    src = f"status:{entity.lower()}"
    out = []
    for inc in d.get("incidents", []):
        for u in inc.get("incident_updates", []):
            text = f"{inc['name']}\nStatus: {u.get('status')}\n{u.get('body', '')}"
            out.append(item(src, entity, True, f"{inc['id']}:{u['id']}", inc["name"], text,
                            inc.get("shortlink") or base, parse_time(u.get("created_at")), group=f"{src}:{inc['id']}"))
    return out


def kraken_blog(fetch_text=net.fetch_text):
    from .news import parse_feed
    s, text = fetch_text(KRAKEN_BLOG)
    if s != 200 or not text:
        return None
    try:
        feed = parse_feed(text)
    except Exception:
        return None
    return [item("kraken-blog", "Kraken", True, f["url"], f["title"], f["title"] + "\n" + f["summary"], f["url"],
                 f["published"]) for f in feed if f["published"]]


def collect_official(state, get=net.request, fetch_text=net.fetch_text):
    """({소스: 항목 목록 또는 None(실패)})"""
    seen = set(i for ids in state.get("seen", {}).values() for i in ids)
    res = {"binance": binance(seen, get)}
    if res["binance"] is None:  # 막히면 텔레그램 미리보기로 대체
        res["binance"] = telegram_channel("binance-tg", "Binance", TELEGRAM["Binance"], fetch_text)
    res["okx"] = okx(seen, get)
    res["bybit"] = telegram_channel("bybit", "Bybit", TELEGRAM["Bybit"], fetch_text)
    for entity, base in STATUSPAGES.items():
        res[f"status:{entity.lower()}"] = statuspage(entity, base, get)
    res["kraken-blog"] = kraken_blog(fetch_text)
    return res
