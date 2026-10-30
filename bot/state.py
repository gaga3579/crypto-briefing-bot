"""state.json 읽기·병합·커밋 (명세 2절).

변경이 있을 때만 커밋한다. 푸시가 충돌하면 git rebase 대신 원격 state를 읽어 코드로 병합한 뒤 다시 푸시한다.
"""
import json
import subprocess

from .config import ROOT

STATE_FILE = "state.json"
KEEP_SENT = 500


def empty():
    return {"version": 1, "coins": {}, "sent": [], "unsent": [], "sources": {}, "heartbeat": None,
            "briefings": [], "news": [], "weekly_last": None}


def load(path=ROOT / STATE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return {**empty(), **json.load(f)}
    except FileNotFoundError:
        return empty()


def dump(state):
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def merge(local, remote):
    """동시 실행으로 갈라진 두 state를 합친다. 알림 기록은 잃지 않는 쪽으로 합친다."""
    out = {**remote, **local}
    coins = {}
    for sym in set(local.get("coins", {})) | set(remote.get("coins", {})):
        lc, rc = local.get("coins", {}).get(sym), remote.get("coins", {}).get(sym)
        if lc and rc and lc.get("anchor_time") == rc.get("anchor_time"):
            coins[sym] = lc if lc.get("level", 0) >= rc.get("level", 0) else rc
        else:
            coins[sym] = lc or rc
    out["coins"] = coins
    sent = list(dict.fromkeys(remote.get("sent", []) + local.get("sent", [])))[-KEEP_SENT:]
    out["sent"] = sent
    unsent = {}
    for item in remote.get("unsent", []) + local.get("unsent", []):
        if item["id"] not in sent:
            unsent[item["id"]] = item
    out["unsent"] = list(unsent.values())
    out["sources"] = {**remote.get("sources", {}), **local.get("sources", {})}
    for key in ("heartbeat", "weekly_last"):
        out[key] = max(filter(None, [local.get(key), remote.get(key)]), default=None)
    out["briefings"] = _union(remote.get("briefings", []), local.get("briefings", []), "id", "ts")
    out["news"] = _union(remote.get("news", []), local.get("news", []), "url", lambda n: n["published"]["ts"])
    return out


def _union(a, b, key, order):
    sort_key = order if callable(order) else (lambda x: x[order])
    return sorted({x[key]: x for x in a + b}.values(), key=sort_key)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def commit_and_push(state, message, retries=3, git=_git, log=print):
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    for attempt in range(1, retries + 1):
        (ROOT / STATE_FILE).write_text(dump(state), encoding="utf-8")
        git("add", STATE_FILE)
        if git("diff", "--cached", "--quiet").returncode == 0:
            return True
        git("commit", "-q", "-m", message)
        if git("push", "-q", "origin", branch).returncode == 0:
            return True
        log(f"state 푸시 실패 ({attempt}/{retries}), 원격 state와 병합 후 재시도")
        git("fetch", "-q", "origin", branch)
        shown = git("show", f"origin/{branch}:{STATE_FILE}")
        remote = {**empty(), **json.loads(shown.stdout)} if shown.returncode == 0 else empty()
        state = merge(state, remote)
        git("reset", "-q", "--hard", f"origin/{branch}")
    return False
