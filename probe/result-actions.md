### 1단계 검증 (GitHub Actions, 2026-10-30 15:20 UTC)

| 구분 | 항목 | 결과 | 상세 |
|---|---|---|---|
| 가격 | Coinbase Exchange | 성공 | BTC=77707.06, ETH=2247.82, SOL=95.3, XRP=1.2553 / 300봉 요청→301개, 301봉 요청→HTTP400 granularity too small for the requested time range. Count of aggregations requested exceeds 300 |
| 가격·스테이블 | Kraken | 성공 | {'XETHZUSD': '2247.45000', 'SOLUSD': '95.31000', 'USDCUSD': '0.99970000', 'USDTZUSD': '0.99854000', 'XXBTZUSD': '77733.80000', 'XXRPZUSD': '1.25567000'} / 1시간봉 반환 721개 |
| 사건 | 바이낸스 CMS API | 성공 | apex/v1/public/apex/cms/article/list/query → 48:New Cryptocurrency Listing(20), 49:Latest Binance News(20), 93:Latest Activities(20), 161:Delisting(20), 157:Maintenance Updates(20), 51:API Updates(20), 128:Crypto Airdrop(20) |
| 사건 | 바이낸스 t.me/s 미리보기 | 성공 | HTTP200, 메시지 20개 |
| 사건 | Coinbase 상태 페이지 | 성공 | incident 50개, 최신 2026-10-20T08:14:15.633-07:00 |
| 사건 | Kraken 상태 페이지 | 성공 | incident 50개, 최신 2026-10-30T02:42:59.000Z |
| 사건 | Solana 상태 페이지 | 성공 | incident 30개, 최신 2024-02-06T10:22:42.053Z |
| 사건 | OKX 공지 API | 성공 | 20개, 최신: OKX to support VELODROME crypto migration |
| 사건 | Bybit 공지 API | 실패 | HTTP403  |
| 사건 | Kraken 블로그 | 성공 | https://blog.kraken.com/feed → 항목 10개 |
| 사건 | Coinbase 블로그 | 성공 | https://medium.com/feed/the-coinbase-blog → 항목 10개 |
| 체인 | BTC (mempool.space) | 성공 | 높이 973835, 블록 시각 7분 전 |
| 체인 | ETH (공개 RPC) | 성공 | ethereum-rpc.publicnode.com 블록 시각 0분 전 |
| 체인 | XRPL (공개 서버) | 성공 | s1.ripple.com:51234 원장 마감 0분 전 |
| 체인 | SOL (공개 RPC) | 성공 | 슬롯 463333629, 블록 시각 0분 전 |
| 맥락 | CoinDesk RSS | 성공 | https://www.coindesk.com/arc/outboundfeeds/rss/ → 항목 25개 |
| 맥락 | The Block RSS | 성공 | https://www.theblock.co/rss.xml → 항목 20개 |
| 맥락 | Decrypt RSS | 성공 | https://decrypt.co/feed → 항목 38개 |
| 맥락 | SEC 보도자료 | 성공 | https://www.sec.gov/news/pressreleases.rss → 항목 25개 |
| 맥락 | CFTC 보도자료 | 성공 | https://www.cftc.gov/RSS/RSSGP/rssgp.xml → 항목 10개 |
| 발송 | 텔레그램 sendMessage | 성공 | HTTP200 |
| AI | Claude (claude -p, Haiku) | 성공 | 응답='OK', 모델=claude-haiku-5-5, total_cost_usd=0.00058069, apiKeySource=? |
