"""최소 HTTP 도우미. 실패는 예외 대신 (상태코드, 데이터)로 돌려준다. 상태 0은 네트워크 오류."""
import json
import urllib.error
import urllib.request

UA = "crypto-briefing-bot/0.2 (+https://github.com/gaga3579/crypto-briefing-bot)"
TIMEOUT = 20


def _raw(url, body=None, method=None, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, None


def fetch_text(url, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    status, raw = _raw(url, headers=h, timeout=timeout)
    return status, raw.decode("utf-8", "replace") if raw is not None else None


def request(url, payload=None, method=None, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        h["Content-Type"] = "application/json"
    status, raw = _raw(url, body, method, h, timeout)
    if status == 0:
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None
