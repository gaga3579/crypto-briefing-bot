"""최소 HTTP 도우미. 실패는 예외 대신 (상태코드, 데이터)로 돌려준다. 상태 0은 네트워크 오류."""
import json
import urllib.error
import urllib.request

UA = "crypto-briefing-bot/0.2 (+https://github.com/gaga3579/crypto-briefing-bot)"
TIMEOUT = 20


def request(url, payload=None, method=None, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception:
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None
