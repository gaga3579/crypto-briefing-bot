"""hourly 실행 진입점. 하락·사건 브리핑, 뉴스 수집, 주간 요약, 미발송 큐, state 커밋, heartbeat."""
import argparse
import copy
import os
import time
from datetime import datetime, timezone

from . import event_briefing, events, extract, net, news, prices, sources, state as st, telegram, weekly, writer
from .briefing import drawdown_briefing, kst, signed
from .config import load_config
from .drawdown import evaluate, market_wide, threshold_for, thresholds


def utc_iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mark_source(state, name, ok, now_ts):
    """소스 상태는 성공↔실패가 바뀔 때만 기록한다."""
    cur = state["sources"].get(name)
    if not cur or cur["ok"] != ok:
        state["sources"][name] = {"ok": ok, "since": utc_iso(now_ts)}


KEEP_BRIEFING_DAYS = 30


def deliver(state, parts, now_ts, send_fn, log):
    """같은 실행의 브리핑 조각들을 한 메시지로 보낸다 (명세 5절: 동시 발동은 1건으로 합침).
    parts: [{"id", "kind", "summary", "text", "incident"?}]"""
    parts = [p for p in parts if p["id"] not in state["sent"]]
    if not parts:
        return
    ids = [p["id"] for p in parts]
    cutoff = now_ts - KEEP_BRIEFING_DAYS * 86400
    state["briefings"] = [b for b in state["briefings"] if b["ts"] >= cutoff] + [
        {"id": p["id"], "ts": int(now_ts), "kind": p["kind"], "summary": p["summary"],
         "incident": p.get("incident")} for p in parts]
    text = event_briefing.SEP.join(p["text"] for p in parts)
    ok, why = send_fn(text)
    if ok:
        state["sent"] = (state["sent"] + ids)[-st.KEEP_SENT:]
        log(f"발송: {', '.join(ids)}")
    else:
        state["unsent"].append({"id": "+".join(ids), "ids": ids, "created": now_ts, "text": text})
        log(f"발송 실패, 미발송 큐에 저장: {', '.join(ids)} ({why})")


def flush_unsent(state, send_fn, log):
    remaining = []
    for item in state["unsent"]:
        ids = item.get("ids", [item["id"]])
        if all(i in state["sent"] for i in ids):
            continue
        note = f"<i>지연 발송 · 발생 시각 {kst(item['created'])}</i>\n\n"
        ok, why = send_fn(note + item["text"])
        if ok:
            state["sent"] = (state["sent"] + ids)[-st.KEEP_SENT:]
            log(f"지연 발송: {item['id']}")
        else:
            remaining.append(item)
            log(f"지연 발송 실패: {item['id']} ({why})")
    state["unsent"] = remaining


def collect_market(cfg, now_ts, fetch_coinbase, fetch_kraken):
    """({심볼: CoinMarket}, {심볼: 봉}, kraken 시세, 오류 목록, 소스 상태)"""
    lookback = cfg["drawdown"]["lookback_hours"]
    markets, candles, errors = {}, {}, []
    for sym in cfg["symbols"]:
        try:
            price, candles[sym] = fetch_coinbase(sym, now_ts, lookback)
            markets[sym] = prices.summarize(sym, price, candles[sym], now_ts, lookback)
        except prices.FetchError as e:
            errors.append(str(e))
    kraken = fetch_kraken()
    return markets, candles, kraken, errors, {"coinbase": not errors, "kraken": bool(kraken)}


def media_items(feeds, cfg):
    """매체 RSS 항목을 사건 항목 모양으로 (교차 확인용, 공식 아님)."""
    out = []
    for name in cfg["events"]["media"]["outlets"]:
        for it in feeds.get(name) or []:
            if it["published"]:
                out.append({"id": f"media:{it['url']}", "source": name, "entity": None, "official": False,
                            "title": it["title"], "text": it["summary"], "url": it["url"],
                            "published": it["published"], "group": None})
    return out


def run(cfg, state, now_ts, send_fn, fetch_coinbase=prices.fetch_coinbase,
        fetch_kraken=prices.fetch_kraken, fetch_text=net.fetch_text,
        run_stats=lambda ws, we: None, force_weekly=False, official_fn=None, chain_fn=None,
        extract_fn=None, ai_call=None, log=print):
    state = copy.deepcopy(state)
    mark = lambda n, ok: mark_source(state, n, ok, now_ts)  # noqa: E731
    base_call = ai_call or (lambda *a: extract.ai_call(*a, log=log))

    def call(*args):
        raw, err = base_call(*args)
        mark("ai:claude", raw is not None)
        return raw, err
    flush_unsent(state, send_fn, log)

    markets, candles, kraken, errors, status = collect_market(cfg, now_ts, fetch_coinbase, fetch_kraken)
    for name, ok in status.items():
        mark(name, ok)
    dds, reached = run_drawdown(cfg, state, markets, kraken, errors, log)

    feeds = news.collect(state, cfg, now_ts, fetch_text, mark=mark, log=log)

    official = (official_fn or (lambda s: sources.collect_official(s, fetch_text=fetch_text)))(state)
    for name, items in official.items():
        mark(f"src:{name}", items is not None)
    ages = (chain_fn or events.chain_ages)(now_ts)
    for sym, age in ages.items():
        mark(f"chain:{sym}", age is not None)
    usd = {sym: m.price for sym, m in markets.items()} | {c: kraken[c] for c in ("USDT", "USDC") if c in kraken}
    parts = events.run_events(state, cfg, now_ts, usd, official, media_items(feeds, cfg), kraken, ages,
                              extract_fn or (lambda item: extract.extract(item, cfg, call, log)), log)
    for p in parts:
        if p.get("ext") and p["kind"] in ("event", "followup"):
            inc = state["incidents"][p["incident"]]
            p["ai_text"] = writer.event_summary(p["ext"], inc["entity"], events.LABELS[inc["event_type"]],
                                                event_briefing.metric_values(p["ext"], usd), call,
                                                cfg["ai"]["write_model"], log)
        p["text"] = event_briefing.render(p, state, markets, dds, usd, now_ts)
    if reached:
        cause = writer.drawdown_cause(cause_articles(state, now_ts), call, cfg["ai"]["write_model"], log)
        parts.append(drawdown_part(cfg, state, markets, dds, reached, now_ts, cause))
    deliver(state, parts, now_ts, send_fn, log)

    send_weekly, key = weekly.due(state, now_ts, force_weekly)
    if send_weekly:
        text = weekly.build(state, cfg, now_ts, candles, dds, run_stats(*weekly.window(now_ts)))
        brief_id = f"weekly-{key}" + (f"-manual-{int(now_ts)}" if force_weekly else "")
        deliver(state, [{"id": brief_id, "kind": "weekly", "summary": f"주간 요약 ({key})", "text": text}],
                now_ts, send_fn, log)
        state["weekly_last"] = max(filter(None, [state.get("weekly_last"), key]))
    return state


def cause_articles(state, now_ts, hours=24, limit=12):
    """원인 판단에 넘길 최근 기사 (수집된 뉴스 중 코인·규제 키워드에 걸린 것)."""
    recent = [n for n in state.get("news", []) if now_ts - n["published"]["ts"] <= hours * 3600]
    return [{k: n[k] for k in ("source", "url", "title", "summary")} for n in recent[-limit:]]


def drawdown_part(cfg, state, markets, dds, reached, now_ts, cause=None):
    wide = market_wide(dds, cfg)
    text = drawdown_briefing(markets, dds, reached, state["coins"], wide, cfg, now_ts,
                             events_text=event_briefing.ongoing(state), cause=cause)
    hour = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y%m%d%H")
    ths = thresholds(cfg)
    return {"id": f"dd-{hour}-" + "-".join(f"{s}{lv}" for s, lv in reached.items()), "kind": "drawdown",
            "summary": (f"하락 브리핑({'시장 전반' if wide else '개별 코인'}): "
                        + ", ".join(f"{s} {signed(threshold_for(lv, ths))}" for s, lv in reached.items())),
            "text": text}


def run_drawdown(cfg, state, markets, kraken, errors, log):
    """하락 판정. (판정했으면 {심볼: drawdown%} 아니면 None, {새로 도달한 심볼: level})"""
    if errors:
        log("가격 수집 실패, 판정 보류: " + "; ".join(errors))
        return None, {}
    hold = []
    for sym, cm in markets.items():
        ok, why = prices.check_sanity(cm, kraken.get(sym), cfg)
        if why:
            log(why)
        if not ok:
            hold.append(sym)
    if hold:
        log(f"비정상 값으로 판정 보류: {', '.join(hold)}")
        return None, {}

    new_coins, reached, dds = {}, {}, {}
    for sym, cm in markets.items():
        new_coins[sym], lv, dds[sym] = evaluate(state["coins"].get(sym, {}), cm, cfg)
        if lv:
            reached[sym] = lv
    state["coins"] = new_coins
    log("drawdown: " + ", ".join(f"{s} {d}%" for s, d in dds.items()))

    return dds, reached


def heartbeat(state, now_ts, log=print):
    """하루 1회 heartbeat를 갱신하고 workflow enable API를 호출한다 (60일 비활성 대비)."""
    today = kst(now_ts, "%Y-%m-%d")
    if state.get("heartbeat") == today:
        return
    state["heartbeat"] = today
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if repo and token:
        s, _ = net.request(f"https://api.github.com/repos/{repo}/actions/workflows/hourly.yml/enable",
                           method="PUT", headers={"Authorization": f"Bearer {token}",
                                                  "Accept": "application/vnd.github+json"})
        log(f"workflow enable API: HTTP{s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="발송·커밋 없이 메시지를 출력만 한다")
    ap.add_argument("--force-weekly", action="store_true", help="주간 요약을 지금 보낸다")
    args = ap.parse_args()

    cfg = load_config()
    before = st.load()
    now_ts = time.time()
    if args.dry_run:
        def send_fn(text):
            print("----- 메시지 -----\n" + text + "\n------------------")
            return True, ""
    else:
        send_fn = telegram.send
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    force = args.force_weekly or os.environ.get("FORCE_WEEKLY") == "true"
    after = run(cfg, before, now_ts, send_fn, force_weekly=force,
                run_stats=lambda ws, we: weekly.run_stats(repo, token, ws, we))
    heartbeat(after, now_ts)
    if args.dry_run:
        return
    if st.dump(after) != st.dump(before):
        if not st.commit_and_push(after, f"state: {utc_iso(now_ts)}"):
            print("state 커밋 실패. 다음 실행에서 재발송될 수 있다(허용).")


if __name__ == "__main__":
    main()
