"""사건 판정 (명세 5절): 규칙 필터 → 추출 → 규칙 판정 → 사건 묶기 → 후속 판단. 체인 생존과 스테이블코인도 여기서 본다.

사건 state: state["incidents"][key] =
  {key, entity, event_type, opened, last, status(open|closed), closed_reason, briefed, official,
   metrics{type: [value, currency]}, withdrawal, media[{source,url,title,ts}], updates[...], ext, item}
"""
import re
from datetime import datetime, timezone

from . import net
from .news import _pattern

DAY, HOUR = 86400, 3600
KEEP_SEEN = 1000
LABELS = {"security_incident": "보안 사고", "withdrawal_suspension": "출금 중단", "chain_halt": "체인 중단",
          "stablecoin_depeg": "스테이블코인 디페깅", "other": "기타"}


# ---------- 규칙 필터 ----------

def candidate(item, cfg):
    ev = cfg["events"]
    text = f"{item['title']}\n{item['text']}"
    if not _pattern(ev["risk_keywords"]).search(text):
        return False
    if _pattern(ev["exclude_keywords"]).search(item["title"]):
        return False
    return item["official"] or canonical(text, cfg) is not None


def canonical(text, cfg):
    """글에서 대상 엔티티(주요 거래소, 4개 체인)를 찾는다. 없으면 None."""
    low = (text or "").lower()
    for ex in cfg["events"]["major_exchanges"]:
        if re.search(r"\b" + re.escape(ex.lower()) + r"\b", low):
            return ex
    for chain, aliases in cfg["events"]["chain_aliases"].items():
        if any(re.search(r"\b" + re.escape(a) + r"\b", low) for a in aliases):
            return chain
    return None


# ---------- 판정 ----------

def usd_value(value, currency, usd):
    if currency == "USD":
        return float(value)
    p = usd.get(currency)
    return float(value) * float(p) if p else None


def impact_usd(metrics, usd, cfg):
    vals = [usd_value(m["value"], m["currency"], usd) for m in metrics
            if m["type"] in cfg["events"]["impact_metric_types"]]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def withdrawal_hits(w, cfg):
    if w["status"] != "suspended":
        return False
    return w["scope"] == "all_assets" or bool({a.upper() for a in w["assets"]} & set(cfg["symbols"]))


def judge(ext, entity, official, usd, cfg):
    """(발동 여부, event_type)"""
    ev = cfg["events"]
    if ext["is_scheduled_maintenance"]:
        return False, ext["event_type"]
    t = ext["event_type"]
    if entity in ev["major_exchanges"]:
        if t == "security_incident":
            imp = impact_usd(ext["metrics"], usd, cfg)
            all_suspended = ext["withdrawal_status"]["status"] == "suspended" and \
                ext["withdrawal_status"]["scope"] == "all_assets"
            return (imp is not None and imp >= ev["major_exchange_impact_usd"]) or all_suspended, t
        if withdrawal_hits(ext["withdrawal_status"], cfg):
            return True, "withdrawal_suspension"
    if t == "chain_halt" and entity in cfg["events"]["chains"].values():
        return official, t  # 매체 보도만 있는 체인 중단은 교차 확인 경로로
    return False, t


# ---------- 사건 묶기 ----------

def find_incident(state, entity, event_type, group, now, cfg, any_type=False):
    incs = state.setdefault("incidents", {})
    if group and group in incs and incs[group]["status"] == "open":
        return incs[group]
    window = cfg["events"]["incident_window_days"] * DAY
    open_ = [i for i in incs.values() if i["status"] == "open" and i["entity"] == entity
             and now - i["opened"] <= window and not i["key"].startswith(("chain:", "depeg:"))]
    same = [i for i in open_ if i["event_type"] == event_type]
    pool = same or (open_ if any_type else [])
    return max(pool, key=lambda i: i["last"]) if pool else None


def new_incident(state, key, entity, event_type, now):
    inc = {"key": key, "entity": entity, "event_type": event_type, "opened": int(now), "last": int(now),
           "status": "open", "closed_reason": None, "briefed": False, "official": False, "metrics": {},
           "withdrawal": "unknown", "media": [], "updates": [], "ext": None, "item": None}
    state["incidents"][key] = inc
    return inc


def item_meta(item):
    return {k: item[k] for k in ("id", "source", "title", "url", "official")} | {"ts": item["published"]["ts"]}


def remember(inc, ext, item, now):
    inc["last"] = int(now)
    for m in ext["metrics"]:
        inc["metrics"][m["type"]] = [m["value"], m["currency"]]
    if ext["withdrawal_status"]["status"] != "unknown":
        inc["withdrawal"] = ext["withdrawal_status"]["status"]
    inc["ext"], inc["item"] = ext, item_meta(item)


def followup_reasons(inc, ext, item, cfg):
    reasons = []
    pct = cfg["events"]["followup_metric_change_pct"]
    for m in ext["metrics"]:
        prev = inc["metrics"].get(m["type"])
        if prev is None or prev[1] != m["currency"]:
            reasons.append("피해 규모 첫 공식 수치")
        elif prev[0] and abs(m["value"] / prev[0] - 1) * 100 >= pct:
            reasons.append(f"수치 변경 (±{pct}% 이상)")
    if inc["withdrawal"] == "suspended" and ext["withdrawal_status"]["status"] in ("resumed", "normal"):
        reasons.append("출금 재개")
    if ext["is_correction"]:
        reasons.append("정정·철회")
    if not inc["official"]:
        reasons.append("첫 공식 확인")
    return list(dict.fromkeys(reasons))


# ---------- 처리 ----------

class Ctx:
    """한 번의 실행에서 필요한 것들."""
    def __init__(self, state, cfg, now, usd, extract_fn, log):
        self.state, self.cfg, self.now, self.usd = state, cfg, now, usd
        self.extract_fn, self.log = extract_fn, log
        self.budget = cfg["ai"]["max_extractions_per_run"]
        self.parts = []  # 발송할 브리핑 조각 {"id","kind","summary","text","incident"}

    def extract(self, item):
        if self.budget <= 0:
            return "budget"
        self.budget -= 1
        return self.extract_fn(item)


def new_items(state, source, items, now, cfg):
    """처음 보는 항목만, 오래된 것부터. 소스를 처음 보면 오래된 항목은 본 것으로만 표시한다."""
    seen = state.setdefault("seen", {})
    first = source not in seen
    ids = seen.setdefault(source, [])
    known = set(ids)
    fresh = []
    for it in sorted(items, key=lambda i: i["published"]["ts"] if i["published"] else 0):
        if it["id"] in known or not it["published"]:
            continue
        if first and now - it["published"]["ts"] > cfg["events"]["first_seen_hours"] * HOUR:
            ids.append(it["id"])
            continue
        fresh.append(it)
    seen[source] = ids[-KEEP_SEEN:]
    return fresh


def mark_seen(state, source, item_id):
    ids = state["seen"].setdefault(source, [])
    if item_id not in ids:
        ids.append(item_id)
        state["seen"][source] = ids[-KEEP_SEEN:]


def followup_candidate(ctx, item):
    """알린 사건이 열려 있는 엔티티의 공식 공지 중 후속 키워드가 있는 것."""
    open_briefed = any(i["status"] == "open" and i["briefed"] and i["entity"] == item["entity"]
                       for i in ctx.state.get("incidents", {}).values())
    if not open_briefed or _pattern(ctx.cfg["events"]["exclude_keywords"]).search(item["title"]):
        return False
    return bool(_pattern(ctx.cfg["events"]["followup_keywords"]).search(f"{item['title']}\n{item['text']}"))


def process_official(ctx, item):
    """처리를 끝냈으면 True (본 것으로 표시), 예산 초과로 미뤘으면 False."""
    if not candidate(item, ctx.cfg) and not followup_candidate(ctx, item):
        return True
    ext = ctx.extract(item)
    if ext == "budget":
        return False
    if ext is None:  # AI 실패: 판정 없이 후보만 알린다 (누락보다 중복이 낫다)
        ctx.parts.append({"id": f"cand:{item['id']}", "kind": "event-candidate",
                          "summary": f"사건 후보(자동 판정 불가): {item['title'][:60]}",
                          "item": item, "ext": None, "incident": None})
        return True
    if ext["is_scheduled_maintenance"]:
        return True
    entity = canonical(ext["entity"], ctx.cfg) or item["entity"]
    resume = ext["withdrawal_status"]["status"] in ("resumed", "normal") or ext["is_correction"]
    inc = find_incident(ctx.state, entity, ext["event_type"], item.get("group"), ctx.now, ctx.cfg, any_type=resume)
    if inc and inc["briefed"]:
        reasons = followup_reasons(inc, ext, item, ctx.cfg)
        update = item_meta(item) | {"reasons": reasons, "sent": bool(reasons)}
        inc["updates"].append(update)
        remember(inc, ext, item, ctx.now)
        inc["official"] = True
        if reasons:
            ctx.parts.append({"id": f"fu:{item['id']}", "kind": "followup", "incident": inc["key"],
                              "summary": f"후속 브리핑: {entity} {LABELS[inc['event_type']]} ({', '.join(reasons)})",
                              "item": item, "ext": ext, "reasons": reasons})
        if "출금 재개" in reasons or ext["is_correction"]:
            inc["status"], inc["closed_reason"] = "closed", ("출금 재개" if "출금 재개" in reasons else "정정·철회")
        return True
    fire, etype = judge(ext, entity, True, ctx.usd, ctx.cfg)
    if not fire:
        return True
    if not inc:
        inc = new_incident(ctx.state, item.get("group") or f"{entity}:{etype}:{int(ctx.now)}", entity, etype, ctx.now)
    remember(inc, ext, item, ctx.now)
    inc["briefed"] = inc["official"] = True
    ctx.parts.append({"id": f"ev:{item['id']}", "kind": "event", "incident": inc["key"],
                      "summary": f"사건 브리핑: {entity} {LABELS[etype]}", "item": item, "ext": ext})
    return True


def process_media(ctx, item):
    if not candidate(item, ctx.cfg):
        return True
    entity = canonical(item["title"] + " " + item["text"], ctx.cfg)
    briefed = [i for i in ctx.state.get("incidents", {}).values()
               if i["status"] == "open" and i["entity"] == entity and i["briefed"]]
    if briefed:  # 이미 알린 사건이면 참고 보도로만 붙인다 (추출 생략)
        inc = max(briefed, key=lambda i: i["last"])
        inc["media"].append(item_meta(item))
        return True
    ext = ctx.extract(item)
    if ext == "budget":
        return False
    if ext is None or ext["is_scheduled_maintenance"]:
        return True
    entity = canonical(ext["entity"], ctx.cfg) or entity
    official_like = ext["event_type"] == "chain_halt" and entity in ctx.cfg["events"]["chains"].values()
    fire, etype = judge(ext, entity, False, ctx.usd, ctx.cfg)
    if not (fire or official_like):
        return True
    inc = find_incident(ctx.state, entity, etype, None, ctx.now, ctx.cfg) or \
        new_incident(ctx.state, f"{entity}:{etype}:{int(ctx.now)}", entity, etype, ctx.now)
    inc["media"].append(item_meta(item))
    inc["last"] = int(ctx.now)
    if not inc["ext"] or len(ext["metrics"]) >= len(inc["ext"]["metrics"]):
        inc["ext"], inc["item"] = ext, item_meta(item)
    m = ctx.cfg["events"]["media"]
    latest = max(x["ts"] for x in inc["media"])
    outlets = {x["source"] for x in inc["media"] if latest - x["ts"] <= m["window_hours"] * HOUR}
    if len(outlets) >= m["min_outlets"]:
        inc["briefed"] = True
        ctx.parts.append({"id": f"ev:{inc['key']}", "kind": "event", "incident": inc["key"],
                          "summary": f"사건 브리핑(공식 미확인, 보도 {len(outlets)}곳): {entity} {LABELS[etype]}",
                          "item": item, "ext": inc["ext"], "outlets": sorted(outlets)})
    return True


def process_items(ctx, source, items, handler):
    for it in new_items(ctx.state, source, items, ctx.now, ctx.cfg):
        if handler(ctx, it):
            mark_seen(ctx.state, source, it["id"])


# ---------- 체인 생존·스테이블코인 (AI 없음) ----------

def _age_min(ts, now):
    return (now - ts) / 60


def chain_ages(now, get=net.request):
    """{심볼: 마지막 블록 경과 분 또는 None(조회 실패)}"""
    ages = {}
    s, d = get("https://mempool.space/api/v1/blocks")
    ages["BTC"] = _age_min(d[0]["timestamp"], now) if s == 200 and d else None
    s, d = get("https://ethereum-rpc.publicnode.com",
               {"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": ["latest", False]})
    ages["ETH"] = _age_min(int(d["result"]["timestamp"], 16), now) if s == 200 and d and d.get("result") else None
    s, d = get("https://s1.ripple.com:51234/", {"method": "ledger", "params": [{"ledger_index": "validated"}]})
    led = ((d or {}).get("result") or {}).get("ledger") or {}
    ages["XRP"] = _age_min(datetime.fromisoformat(led["close_time_iso"].replace("Z", "+00:00")).timestamp(), now) \
        if s == 200 and led.get("close_time_iso") else None
    rpc = "https://api.mainnet-beta.solana.com"
    s, d = get(rpc, {"jsonrpc": "2.0", "id": 1, "method": "getSlot"})
    if s == 200 and d and "result" in d:
        s2, d2 = get(rpc, {"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [d["result"] - 50]})
        ages["SOL"] = _age_min(d2["result"], now) if s2 == 200 and d2 and d2.get("result") else None
    else:
        ages["SOL"] = None
    return ages


def check_liveness(ctx, ages):
    cl = ctx.cfg["chain_liveness"]
    counts = ctx.state.setdefault("liveness", {})
    for sym, age in ages.items():
        if age is None:
            continue
        key = f"chain:{sym}"
        stale = age > cl["max_block_age_minutes"][sym]
        if not stale:
            counts.pop(sym, None)
            inc = ctx.state.get("incidents", {}).get(key)
            if inc and inc["status"] == "open":
                inc["status"], inc["closed_reason"] = "closed", "블록 생성 재개"
            continue
        counts[sym] = counts.get(sym, 0) + 1
        inc = ctx.state.setdefault("incidents", {}).get(key)
        if counts[sym] >= cl["consecutive_runs"] and not (inc and inc["status"] == "open"):
            chain = ctx.cfg["events"]["chains"][sym]
            inc = new_incident(ctx.state, key, chain, "chain_halt", ctx.now)
            inc["briefed"] = True
            ctx.parts.append({"id": f"ev:{key}:{int(ctx.now)}", "kind": "liveness", "incident": key,
                              "summary": f"사건 브리핑(공식 미확인, 블록 생성 지표): {chain} 체인 중단",
                              "symbol": sym, "age": age, "limit": cl["max_block_age_minutes"][sym],
                              "runs": counts[sym]})


def check_depeg(ctx, kraken):
    sc = ctx.cfg["stablecoin"]
    counts = ctx.state.setdefault("depeg", {})
    for coin in ("USDT", "USDC"):
        price = kraken.get(coin)
        if price is None:
            continue
        key = f"depeg:{coin}"
        if float(price) >= sc["depeg_price"]:
            counts.pop(coin, None)
            inc = ctx.state.get("incidents", {}).get(key)
            if inc and inc["status"] == "open":
                inc["status"], inc["closed_reason"] = "closed", f"${sc['depeg_price']} 이상 회복"
            continue
        counts[coin] = counts.get(coin, 0) + 1
        inc = ctx.state.setdefault("incidents", {}).get(key)
        if counts[coin] >= sc["depeg_consecutive_runs"] and not (inc and inc["status"] == "open"):
            inc = new_incident(ctx.state, key, coin, "stablecoin_depeg", ctx.now)
            inc["briefed"] = inc["official"] = True
            ctx.parts.append({"id": f"ev:{key}:{int(ctx.now)}", "kind": "depeg", "incident": key,
                              "summary": f"사건 브리핑: {coin} 디페깅", "coin": coin, "price": price,
                              "runs": counts[coin]})


def prune(state, now, keep_days=30):
    incs = state.get("incidents", {})
    for k in [k for k, i in incs.items() if i["status"] == "closed" and now - i["last"] > keep_days * DAY]:
        del incs[k]
    for k in [k for k, i in incs.items() if not i["briefed"] and now - i["last"] > 2 * DAY]:
        del incs[k]  # 교차 확인되지 않은 매체 단독 후보


def run_events(state, cfg, now, usd, official, media, kraken, ages, extract_fn, log=print):
    """발송할 브리핑 조각 목록을 돌려준다. state는 제자리에서 갱신한다."""
    ctx = Ctx(state, cfg, now, usd, extract_fn, log)
    for source, items in official.items():
        if items is not None:
            process_items(ctx, source, items, process_official)
    if media:
        process_items(ctx, "media", media, process_media)
    check_liveness(ctx, ages)
    check_depeg(ctx, kraken)
    prune(state, now)
    return ctx.parts
