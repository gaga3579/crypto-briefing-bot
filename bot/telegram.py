"""텔레그램 발송 (명세 8절): HTML, 4096자 분할, 429는 retry_after, 5xx는 백오프 3회."""
import os
import time

from . import net

LIMIT = 4096
MAX_429_RETRIES = 5
MAX_5XX_RETRIES = 3


def split_message(text, limit=LIMIT):
    """줄 단위로 나누되 <pre> 블록이 잘리면 닫고 다음 조각에서 다시 연다."""
    chunks, cur, in_pre = [], "", False
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        after = _pre_state(line, in_pre)
        sep = "" if cur in ("", "<pre>") else "\n"
        if len(cur) + len(sep) + len(line) + (6 if after else 0) <= limit:
            cur, in_pre, i = cur + sep + line, after, i + 1
            continue
        reopen = "<pre>" if in_pre else ""
        if cur not in ("", "<pre>"):
            chunks.append(cur + ("</pre>" if in_pre else ""))
            cur = reopen
            continue
        # 한 줄이 한도보다 긴 경우: 강제로 자른다
        room = limit - len(cur) - (6 if in_pre else 0)
        chunks.append(cur + line[:room] + ("</pre>" if in_pre else ""))
        lines[i], cur = line[room:], reopen
    if cur not in ("", "<pre>"):
        chunks.append(cur)
    return chunks


def _pre_state(line, in_pre):
    opens, closes = line.count("<pre>"), line.count("</pre>")
    return in_pre if opens == closes else opens > closes


def send_chunk(token, chat_id, text, post=net.request, sleep=time.sleep):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    tries_429 = tries_5xx = 0
    while True:
        status, data = post(url, payload)
        if status == 200 and data and data.get("ok"):
            return True, ""
        if status == 429 and tries_429 < MAX_429_RETRIES:
            tries_429 += 1
            sleep(int(((data or {}).get("parameters") or {}).get("retry_after", 5)) + 1)
            continue
        if (status == 0 or status >= 500) and tries_5xx < MAX_5XX_RETRIES:
            tries_5xx += 1
            sleep(2 ** tries_5xx)
            continue
        return False, f"HTTP{status} {(data or {}).get('description', '')}".strip()


def send(text, token=None, chat_id=None, post=net.request, sleep=time.sleep):
    """모든 조각을 보냈으면 (True, ""). 하나라도 실패하면 (False, 사유)."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "텔레그램 비밀값 없음"
    for chunk in split_message(text):
        ok, why = send_chunk(token, chat_id, chunk, post=post, sleep=sleep)
        if not ok:
            return False, why
    return True, ""
