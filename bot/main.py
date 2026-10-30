"""hourly 실행 진입점. 2단계 범위: 하락 브리핑(템플릿), 미발송 큐, state 커밋, heartbeat."""
import argparse
import copy
import os
import time
from datetime import datetime, timezone

from . import net, prices, state as st, telegram
from .briefing import drawdown_briefing, kst
from .config import load_config
from .drawdown import evaluate, market_wide


def utc_iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mark_source(state, name, ok, now_ts):
    """소스 상태는 성공↔실패가 바뀔 때만 기록한다."""
    cur = state["sources"].get(name)
    if not cur or cur["ok"] != ok:
        state["sources"][name] = {"ok": ok, "since": utc_iso(now_ts)}


def deliver(state, brief_id, text, now_ts, send_fn, log):
    if brief_id in state["sent"]:
        return
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
    """({심볼: CoinMarket}, kraken 시세, 오류 목록, 소스 상태)"""
    lookback = cfg["drawdown"]["lookback_hours"]
    markets, errors = {}, []
    for sym in cfg["symbols"]:
        try:
            price, candles = fetch_coinbase(sym, now_ts, lookback)
            markets[sym] = prices.summarize(sym, price, candles, now_ts, lookback)
        except prices.FetchError as e:
            errors.append(str(e))
    kraken = fetch_kraken()
    return markets, kraken, errors, {"coinbase": not errors, "kraken": bool(kraken)}


def run(cfg, state, now_ts, send_fn, fetch_coinbase=prices.fetch_coinbase,
        fetch_kraken=prices.fetch_kraken, log=print):
    state = copy.deepcopy(state)
    flush_unsent(state, send_fn, log)

    markets, kraken, errors, status = collect_market(cfg, now_ts, fetch_coinbase, fetch_kraken)
    for name, ok in status.items():
        mark_source(state, name, ok, now_ts)
    if errors:
        log("가격 수집 실패, 판정 보류: " + "; ".join(errors))
        return state
    hold = []
    for sym, cm in markets.items():
        ok, why = prices.check_sanity(cm, kraken.get(sym), cfg)
        if why:
            log(why)
        if not ok:
            hold.append(sym)
    if hold:
        log(f"비정상 값으로 판정 보류: {', '.join(hold)}")
        return state

    new_coins, reached, dds = {}, {}, {}
    for sym, cm in markets.items():
        new_coins[sym], lv, dds[sym] = evaluate(state["coins"].get(sym, {}), cm, cfg)
        if lv:
            reached[sym] = lv
    state["coins"] = new_coins
    log("drawdown: " + ", ".join(f"{s} {d}%" for s, d in dds.items()))

    if reached:
        text = drawdown_briefing(markets, dds, reached, new_coins, market_wide(dds, cfg), cfg, now_ts)
        hour = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y%m%d%H")
        brief_id = f"dd-{hour}-" + "-".join(f"{s}{lv}" for s, lv in reached.items())
        deliver(state, brief_id, text, now_ts, send_fn, log)
    return state


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
    after = run(cfg, before, now_ts, send_fn)
    heartbeat(after, now_ts)
    if args.dry_run:
        return
    if st.dump(after) != st.dump(before):
        if not st.commit_and_push(after, f"state: {utc_iso(now_ts)}"):
            print("state 커밋 실패. 다음 실행에서 재발송될 수 있다(허용).")


if __name__ == "__main__":
    main()
