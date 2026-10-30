"""hourly 실행 진입점. 하락 브리핑(템플릿), 뉴스 수집, 주간 요약, 미발송 큐, state 커밋, heartbeat."""
import argparse
import copy
import os
import time
from datetime import datetime, timezone

from . import net, news, prices, state as st, telegram, weekly
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


def deliver(state, brief_id, text, now_ts, send_fn, log, kind, summary):
    if brief_id in state["sent"]:
        return
    cutoff = now_ts - KEEP_BRIEFING_DAYS * 86400
    state["briefings"] = [b for b in state["briefings"] if b["ts"] >= cutoff] + [
        {"id": brief_id, "ts": int(now_ts), "kind": kind, "summary": summary}]
    ok, why = send_fn(text)
    if ok:
        state["sent"] = (state["sent"] + [brief_id])[-st.KEEP_SENT:]
        log(f"발송: {brief_id}")
    else:
        state["unsent"].append({"id": brief_id, "created": now_ts, "text": text})
        log(f"발송 실패, 미발송 큐에 저장: {brief_id} ({why})")


def flush_unsent(state, send_fn, log):
    remaining = []
    for item in state["unsent"]:
        if item["id"] in state["sent"]:
            continue
        note = f"<i>지연 발송 · 발생 시각 {kst(item['created'])}</i>\n\n"
        ok, why = send_fn(note + item["text"])
        if ok:
            state["sent"] = (state["sent"] + [item["id"]])[-st.KEEP_SENT:]
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


def run(cfg, state, now_ts, send_fn, fetch_coinbase=prices.fetch_coinbase,
        fetch_kraken=prices.fetch_kraken, fetch_text=net.fetch_text,
        run_stats=lambda ws, we: None, force_weekly=False, log=print):
    state = copy.deepcopy(state)
    flush_unsent(state, send_fn, log)

    markets, candles, kraken, errors, status = collect_market(cfg, now_ts, fetch_coinbase, fetch_kraken)
    for name, ok in status.items():
        mark_source(state, name, ok, now_ts)
    dds = run_drawdown(cfg, state, now_ts, send_fn, markets, kraken, errors, log)

    news.collect(state, cfg, now_ts, fetch_text, mark=lambda n, ok: mark_source(state, n, ok, now_ts), log=log)

    send_weekly, key = weekly.due(state, now_ts, force_weekly)
    if send_weekly:
        text = weekly.build(state, cfg, now_ts, candles, dds, run_stats(*weekly.window(now_ts)))
        brief_id = f"weekly-{key}" + (f"-manual-{int(now_ts)}" if force_weekly else "")
        deliver(state, brief_id, text, now_ts, send_fn, log, "weekly", f"주간 요약 ({key})")
        state["weekly_last"] = max(filter(None, [state.get("weekly_last"), key]))
    return state


def run_drawdown(cfg, state, now_ts, send_fn, markets, kraken, errors, log):
    """하락 판정과 발송. 판정했으면 {심볼: drawdown%}, 보류했으면 None."""
    if errors:
        log("가격 수집 실패, 판정 보류: " + "; ".join(errors))
        return None
    hold = []
    for sym, cm in markets.items():
        ok, why = prices.check_sanity(cm, kraken.get(sym), cfg)
        if why:
            log(why)
        if not ok:
            hold.append(sym)
    if hold:
        log(f"비정상 값으로 판정 보류: {', '.join(hold)}")
        return None

    new_coins, reached, dds = {}, {}, {}
    for sym, cm in markets.items():
        new_coins[sym], lv, dds[sym] = evaluate(state["coins"].get(sym, {}), cm, cfg)
        if lv:
            reached[sym] = lv
    state["coins"] = new_coins
    log("drawdown: " + ", ".join(f"{s} {d}%" for s, d in dds.items()))

    if reached:
        wide = market_wide(dds, cfg)
        text = drawdown_briefing(markets, dds, reached, new_coins, wide, cfg, now_ts)
        hour = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y%m%d%H")
        brief_id = f"dd-{hour}-" + "-".join(f"{s}{lv}" for s, lv in reached.items())
        ths = thresholds(cfg)
        summary = (f"하락 브리핑({'시장 전반' if wide else '개별 코인'}): "
                   + ", ".join(f"{s} {signed(threshold_for(lv, ths))}" for s, lv in reached.items()))
        deliver(state, brief_id, text, now_ts, send_fn, log, "drawdown", summary)
    return dds


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
