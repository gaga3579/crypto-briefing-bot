# crypto-briefing-bot

BTC·ETH·SOL·XRP 하락·사건 브리핑을 텔레그램으로 보내는 GitHub Actions 봇. 명세는 [SPEC.md](SPEC.md), 기준값은 [config.yaml](config.yaml).

현재 상태: 2단계(하락 브리핑, 템플릿만) 가동. `hourly` 워크플로가 매시 7분에 실행된다.

- `python -m bot.main --dry-run`: 발송·커밋 없이 메시지만 출력
- `python -m pytest -q tests`: 테스트
- `stage1-probe` 워크플로: 소스·텔레그램·Claude 접근 점검 (수동 실행)

Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`, (선택) `SEC_USER_AGENT`.
