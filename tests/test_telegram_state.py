"""명세 12절 필수 테스트 18, 19, 34."""
import json
import subprocess

from bot import state as st, telegram


def test_18_429_waits_retry_after_then_succeeds():
    calls, sleeps = [], []
    responses = [(429, {"ok": False, "parameters": {"retry_after": 3}}), (200, {"ok": True})]

    def post(url, payload):
        calls.append(payload)
        return responses.pop(0)

    ok, _ = telegram.send("hi", token="t", chat_id="c", post=post, sleep=sleeps.append)
    assert ok and len(calls) == 2 and sleeps == [4]


def test_18b_5xx_backoff_max_three_retries():
    sleeps = []
    ok, why = telegram.send("hi", token="t", chat_id="c", post=lambda u, p: (502, None), sleep=sleeps.append)
    assert not ok and sleeps == [2, 4, 8]


def test_19_long_message_split_with_balanced_pre():
    table = "<pre>" + "\n".join(f"ROW {i:04d} " + "x" * 60 for i in range(120)) + "</pre>"
    text = "<b>head</b>\n" + "\n".join(f"line {i} " + "y" * 50 for i in range(60)) + "\n" + table + "\ntail"
    chunks = telegram.split_message(text)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= telegram.LIMIT
        assert c.count("<pre>") == c.count("</pre>")
    joined = "\n".join(chunks)
    for i in range(120):
        assert f"ROW {i:04d}" in joined
    assert joined.endswith("tail")


def test_34_merge_keeps_both_histories():
    local = {**st.empty(), "sent": ["a", "b"], "unsent": [{"id": "c", "created": 1, "text": "x"}],
             "coins": {"BTC": {"level": 1, "anchor_high": "100", "anchor_time": 5}}, "heartbeat": "2026-10-30"}
    remote = {**st.empty(), "sent": ["a", "c"], "unsent": [{"id": "d", "created": 1, "text": "y"}],
              "coins": {"BTC": {"level": 2, "anchor_high": "100", "anchor_time": 5},
                        "ETH": {"level": 1, "anchor_high": "5", "anchor_time": 7}}, "heartbeat": "2026-10-31"}
    m = st.merge(local, remote)
    assert m["sent"] == ["a", "c", "b"]
    assert [u["id"] for u in m["unsent"]] == ["d"]  # c는 이미 발송됨
    assert m["coins"]["BTC"]["level"] == 2 and m["coins"]["ETH"]["level"] == 1
    assert m["heartbeat"] == "2026-10-31"


def test_34b_push_conflict_merges_remote_state(tmp_path, monkeypatch):
    def sh(*a, cwd):
        subprocess.run(a, cwd=cwd, check=True, capture_output=True)

    origin, a, b = tmp_path / "origin.git", tmp_path / "a", tmp_path / "b"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    for clone in (a, b):
        sh("git", "clone", "-q", str(origin), str(clone), cwd=tmp_path)
        sh("git", "config", "user.email", "t@t", cwd=clone)
        sh("git", "config", "user.name", "t", cwd=clone)
        sh("git", "checkout", "-q", "-b", "main", cwd=clone)
    (a / "state.json").write_text(st.dump(st.empty()))
    sh("git", "add", ".", cwd=a); sh("git", "commit", "-qm", "init", cwd=a); sh("git", "push", "-q", "origin", "main", cwd=a)
    sh("git", "pull", "-q", "origin", "main", cwd=b)

    def git_in(root):
        return lambda *args: subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)

    monkeypatch.setattr(st, "ROOT", a)
    assert st.commit_and_push({**st.empty(), "sent": ["from-a"]}, "a", git=git_in(a), log=lambda *_: None)
    monkeypatch.setattr(st, "ROOT", b)
    assert st.commit_and_push({**st.empty(), "sent": ["from-b"]}, "b", git=git_in(b), log=lambda *_: None)
    final = json.loads(subprocess.run(["git", "show", "origin/main:state.json"], cwd=b,
                                      capture_output=True, text=True).stdout)
    assert final["sent"] == ["from-a", "from-b"]
