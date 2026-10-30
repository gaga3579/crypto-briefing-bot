"""1단계 환경 검증: 데이터 소스 접근, 텔레그램 발송, Claude 인증을 확인하고 표로 출력한다.

비밀값은 환경변수로만 받고 출력하지 않는다.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = "crypto-briefing-bot/0.1 (stage1 probe)"
SEC_UA = os.environ.get("SEC_USER_AGENT") or UA
TIMEOUT = 20


def fetch(url, data=None, headers=None, ua=UA):
    h = {"User-Agent": ua, "Accept": "*/*"}
    h.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # 네트워크 오류는 상태 0으로 기록
        return 0, str(e).encode()


def fetch_json(url, **kw):
    status, raw = fetch(url, **kw)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def age(ts):
    return f"{(time.time() - ts) / 60:.0f}분 전"


# ---------- 가격 ----------

def coinbase():
    out = []
    for sym in ["BTC", "ETH", "SOL", "XRP"]:
        s, d = fetch_json(f"https://api.exchange.coinbase.com/products/{sym}-USD/ticker")
        out.append(f"{sym}={d.get('price')}" if s == 200 and d else f"{sym}:HTTP{s}")
    ok = all("=" in x for x in out)
    # 요청당 최대 봉 개수: 300봉은 되고 301봉은 거부되는지 본다
    now = int(time.time()) // 3600 * 3600
    def candles(n):
        start = datetime.fromtimestamp(now - n * 3600, timezone.utc).isoformat()
        end = datetime.fromtimestamp(now, timezone.utc).isoformat()
        return fetch_json(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&start={start}&end={end}")
    s300, d300 = candles(300)
    s301, d301 = candles(301)
    n300 = len(d300) if isinstance(d300, list) else f"HTTP{s300}"
    r301 = len(d301) if isinstance(d301, list) else f"HTTP{s301} {(d301 or {}).get('message', '')}"
    return ok, f"{', '.join(out)} / 300봉 요청→{n300}개, 301봉 요청→{r301}"


def kraken():
    s, d = fetch_json("https://api.kraken.com/0/public/Ticker?pair=USDTUSD,USDCUSD,XBTUSD,ETHUSD,SOLUSD,XRPUSD")
    if s != 200 or not d or d.get("error"):
        return False, f"HTTP{s} {d and d.get('error')}"
    prices = {k: v["c"][0] for k, v in d["result"].items()}
    s2, d2 = fetch_json("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60")
    n = len(next(v for k, v in d2["result"].items() if k != "last")) if s2 == 200 and d2 and not d2.get("error") else f"HTTP{s2}"
    return True, f"{prices} / 1시간봉 반환 {n}개"


# ---------- 사건: 공식 소스 ----------

def binance_cms():
    urls = [
        "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=20",
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20",
    ]
    notes = []
    for u in urls:
        s, d = fetch_json(u)
        cats = (d or {}).get("data", {}).get("catalogs") if isinstance(d, dict) and isinstance(d.get("data"), dict) else None
        if s == 200 and cats:
            names = [f"{c.get('catalogId')}:{c.get('catalogName')}({len(c.get('articles', []))})" for c in cats]
            return True, f"{u.split('/bapi/')[1].split('?')[0]} → " + ", ".join(names)
        notes.append(f"HTTP{s}")
    return False, " / ".join(notes)


def binance_tg():
    s, raw = fetch("https://t.me/s/binance_announcements")
    txt = raw.decode("utf-8", "ignore")
    n = txt.count("tgme_widget_message_text")
    return s == 200 and n > 0, f"HTTP{s}, 메시지 {n}개"


def statuspage(base):
    def f():
        s, d = fetch_json(f"{base}/api/v2/incidents.json")
        if s != 200 or not d:
            return False, f"HTTP{s}"
        inc = d.get("incidents", [])
        latest = inc[0]["created_at"] if inc else "-"
        return True, f"incident {len(inc)}개, 최신 {latest}"
    return f


def okx():
    s, d = fetch_json("https://www.okx.com/api/v5/support/announcements")
    if s == 200 and d and d.get("code") == "0":
        items = d["data"][0].get("details", []) if d.get("data") else []
        return True, f"{len(items)}개, 최신: {items[0].get('title', '')[:50] if items else '-'}"
    return False, f"HTTP{s} {(d or {}).get('msg', '')}"


def bybit():
    s, d = fetch_json("https://api.bybit.com/v5/announcements/index?locale=en-US&limit=10")
    if s == 200 and d and d.get("retCode") == 0:
        items = d["result"]["list"]
        return True, f"{len(items)}개, 최신: {items[0]['title'][:50] if items else '-'}"
    return False, f"HTTP{s} {(d or {}).get('retMsg', '')}"


def feed(urls, ua=UA):
    def f():
        notes = []
        for u in urls:
            s, raw = fetch(u, ua=ua)
            txt = raw.decode("utf-8", "ignore")
            n = txt.count("<item") + txt.count("<entry")
            if s == 200 and n:
                return True, f"{u} → 항목 {n}개"
            notes.append(f"{u.split('//')[1][:40]}:HTTP{s}/항목{n}")
        return False, " / ".join(notes)
    return f


# ---------- 체인 생존 ----------

def btc_tip():
    s, d = fetch_json("https://mempool.space/api/v1/blocks")
    if s == 200 and d:
        return True, f"높이 {d[0]['height']}, 블록 시각 {age(d[0]['timestamp'])}"
    return False, f"HTTP{s}"


def eth_tip():
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": ["latest", False]}
    notes = []
    for u in ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com"]:
        s, d = fetch_json(u, data=body)
        if s == 200 and d and d.get("result"):
            return True, f"{u.split('//')[1]} 블록 시각 {age(int(d['result']['timestamp'], 16))}"
        notes.append(f"{u.split('//')[1]}:HTTP{s}")
    return False, " / ".join(notes)


def xrpl_tip():
    body = {"method": "ledger", "params": [{"ledger_index": "validated"}]}
    notes = []
    for u in ["https://s1.ripple.com:51234/", "https://xrplcluster.com/"]:
        s, d = fetch_json(u, data=body)
        led = (d or {}).get("result", {}).get("ledger", {})
        if s == 200 and led.get("close_time_iso"):
            ts = datetime.fromisoformat(led["close_time_iso"].replace("Z", "+00:00")).timestamp()
            return True, f"{u.split('//')[1].rstrip('/')} 원장 마감 {age(ts)}"
        notes.append(f"{u.split('//')[1]}:HTTP{s}")
    return False, " / ".join(notes)


def sol_tip():
    u = "https://api.mainnet-beta.solana.com"
    s, d = fetch_json(u, data={"jsonrpc": "2.0", "id": 1, "method": "getSlot"})
    if s != 200 or not d or "result" not in d:
        return False, f"HTTP{s}"
    s2, d2 = fetch_json(u, data={"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [d["result"] - 50]})
    if s2 == 200 and d2 and d2.get("result"):
        return True, f"슬롯 {d['result']}, 블록 시각 {age(d2['result'])}"
    return False, f"getBlockTime HTTP{s2}"


# ---------- 발송·AI ----------

def telegram():
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return None, "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 없음 (건너뜀)"
    where = "GitHub Actions 러너" if os.environ.get("GITHUB_ACTIONS") else "로컬"
    s, d = fetch_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": f"[1단계 검증] {where}에서 보낸 테스트 메시지입니다."})
    return (s == 200 and d and d.get("ok")), f"HTTP{s}" + ("" if s == 200 else f" {(d or {}).get('description', '')}")


def claude():
    if not shutil.which("claude"):
        return None, "claude CLI 없음 (건너뜀)"
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None, "CLAUDE_CODE_OAUTH_TOKEN 없음 (건너뜀)"
    cmd = ["claude", "-p", "Reply with exactly: OK", "--model", "haiku", "--output-format", "json",
           "--tools", "", "--strict-mcp-config"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return False, str(e)[:200]
    try:
        d = json.loads(r.stdout)
    except Exception:
        return False, f"exit {r.returncode}: {(r.stderr or r.stdout)[:200]}"
    models = ",".join((d.get("modelUsage") or {}).keys())
    return (not d.get("is_error") and "OK" in str(d.get("result"))), (
        f"응답={str(d.get('result'))[:20]!r}, 모델={models}, "
        f"total_cost_usd={d.get('total_cost_usd')}, apiKeySource={d.get('apiKeySource', '?')}")


CHECKS = [
    ("가격", "Coinbase Exchange", coinbase),
    ("가격·스테이블", "Kraken", kraken),
    ("사건", "바이낸스 CMS API", binance_cms),
    ("사건", "바이낸스 t.me/s 미리보기", binance_tg),
    ("사건", "Coinbase 상태 페이지", statuspage("https://status.coinbase.com")),
    ("사건", "Kraken 상태 페이지", statuspage("https://status.kraken.com")),
    ("사건", "Solana 상태 페이지", statuspage("https://status.solana.com")),
    ("사건", "OKX 공지 API", okx),
    ("사건", "Bybit 공지 API", bybit),
    ("사건", "Kraken 블로그", feed(["https://blog.kraken.com/feed"])),
    ("사건", "Coinbase 블로그", feed(["https://www.coinbase.com/blog/rss.xml", "https://www.coinbase.com/blog/feed",
                                     "https://medium.com/feed/the-coinbase-blog"])),
    ("체인", "BTC (mempool.space)", btc_tip),
    ("체인", "ETH (공개 RPC)", eth_tip),
    ("체인", "XRPL (공개 서버)", xrpl_tip),
    ("체인", "SOL (공개 RPC)", sol_tip),
    ("맥락", "CoinDesk RSS", feed(["https://www.coindesk.com/arc/outboundfeeds/rss/"])),
    ("맥락", "The Block RSS", feed(["https://www.theblock.co/rss.xml"])),
    ("맥락", "Decrypt RSS", feed(["https://decrypt.co/feed"])),
    ("맥락", "SEC 보도자료", feed(["https://www.sec.gov/news/pressreleases.rss"], ua=SEC_UA)),
    ("맥락", "CFTC 보도자료", feed(["https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "https://www.cftc.gov/rss/pressreleases.xml"])),
    ("발송", "텔레그램 sendMessage", telegram),
    ("AI", "Claude (claude -p, Haiku)", claude),
]


def main():
    only = set(sys.argv[1:])
    where = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "로컬"
    lines = [f"### 1단계 검증 ({where}, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", "",
             "| 구분 | 항목 | 결과 | 상세 |", "|---|---|---|---|"]
    for group, name, fn in CHECKS:
        if only and name not in only:
            continue
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"[:200]
        mark = "건너뜀" if ok is None else ("성공" if ok else "실패")
        detail = str(detail).replace("|", "/").replace("\n", " ")
        lines.append(f"| {group} | {name} | {mark} | {detail} |")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(report + "\n")
    os.makedirs("probe", exist_ok=True)
    with open(f"probe/result-{'actions' if where == 'GitHub Actions' else 'local'}.md", "w") as f:
        f.write(report + "\n")


if __name__ == "__main__":
    main()
