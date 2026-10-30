# crypto-briefing-bot

BTC·ETH·SOL·XRP 하락·사건 브리핑을 텔레그램으로 보내는 GitHub Actions 봇.

현재 상태: 1단계(환경 검증). `stage1-probe` 워크플로를 수동 실행하면 결과가 실행 요약과 `probe/result-actions.md`에 남는다.

Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`, (선택) `SEC_USER_AGENT`.
