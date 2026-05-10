# Polymarket Weather Risk Investigation

Дата: 2026-05-05.

Цель: понять, как минимизировать риск в погодных рынках Polymarket при маленьком банке, где надежность важнее доходности. Это не финансовая рекомендация; live-торговля имеет риск потери денег и зависит от легальной доступности Polymarket в конкретной юрисдикции.

## Короткий вывод

Для банка $100 текущая правильная философия такая: сначала строим не "машину для ставок", а "машину для отказа от ставок". Большая часть рынков должна получать статус `NO_TRADE`.

Текущий проект уже движется в правильную сторону: есть paper режим, betting layer, risk flags, dashboard, settlement. Но для live денег он еще слишком мягкий. Главный риск не в том, что модель иногда ошибается на 1-2 градуса. Главный риск в том, что Polymarket temperature markets часто состоят из узких bucket-ов, где ошибка станции, источника, timezone или округления превращает "почти правильно" в полный проигрыш ставки.

Мой вывод: live подключать рано. Следующий безопасный шаг - ужесточить paper-стратегию под банк $100 и собрать статистику на 200-300 settled paper-сделок.

## Что было проверено

1. Страница `https://polymarket.com/weather` через браузер.
2. Свежий snapshot Weather category через Gamma API.
3. Weather leaderboard через Data API за `ALL`, `MONTH`, `WEEK`, `DAY`.
4. Последние activity rows у 16 топовых или недавно активных weather-трейдеров.
5. Публичные Polymarket docs по Gamma/Data/CLOB API, orderbook, fees, maker rebates, order lifecycle и geoblock.
6. Текущее состояние нашего проекта и тесты.

## Что удалось / не удалось

Удалось:

- Получить страницу Polymarket Weather в браузере.
- Получить current weather events из Gamma API.
- Получить leaderboard и activity через Data API.
- Получить CLOB health check.
- Получить geoblock response.
- Сохранить артефакты расследования.

Не удалось / ограничение:

- Python `urllib` в локальной среде уперся в SSL certificate verification. API-сбор переключен на `curl`; `curl` запросы прошли успешно.
- Live order placement не проверялся и не должен проверяться из текущей среды, потому что geoblock вернул `blocked=true`.

## Текущий рынок

Свежий Gamma API snapshot:

- `217` weather events.
- `172` city daily temperature events.
- `1,892` binary market rows внутри daily temperature events.
- Суммарный volume daily city temperature events: около `$5.10M`.
- Суммарная liquidity daily city temperature events: около `$6.68M`.

Разбивка daily city temperature events по `endDate`:

| Date | Events | Markets | Volume | Liquidity |
| --- | ---: | ---: | ---: | ---: |
| 2026-05-05 | 54 | 594 | $4.01M | $2.66M |
| 2026-05-06 | 59 | 649 | $0.91M | $2.37M |
| 2026-05-07 | 59 | 649 | $0.18M | $1.64M |

Качество orderbook-полей в Gamma snapshot:

- `1,619 / 1,892` market rows принимают orders.
- `1,361 / 1,892` имеют `bestBid` и `bestAsk`.
- Median spread: `0.005`.
- P90 spread: `0.05`.
- Max spread: `0.77`.

Практический вывод: в weather много ликвидности, но она неравномерная. Near-resolve рынки могут выглядеть очень "легкими" на paper, но реальная исполняемая цена, очередь maker orders и скорость обновления решают больше, чем красивая theoretical edge.

## Лидеры и что из них видно

Свежий all-time WEATHER leaderboard:

| Rank | User | PnL | Volume |
| ---: | --- | ---: | ---: |
| 1 | gopfan2 | $347,453 | $4,575,064 |
| 2 | aenews2 | $277,050 | $9,972,309 |
| 3 | ColdMath | $124,676 | $10,022,687 |
| 4 | gopfan | $118,426 | $739,901 |
| 5 | bama124 | $86,601 | $410,556 |
| 6 | Hans323 | $80,872 | $6,971,547 |
| 7 | Handsanitizer23 | $71,174 | $953,275 |
| 8 | automatedAItradingbot | $64,742 | $2,531,503 |

Activity sample:

- Проверено `16` wallets, по `250` последних activity rows.
- Всего activity rows: `3,996`.
- Temperature-like rows: `2,326`.
- Temperature-like notional: около `$1.51M`, но распределение сильно перекошено несколькими крупными wallets.

Паттерны по price buckets в temperature activity:

| Price bucket | Rows |
| --- | ---: |
| <=5c | 1,146 |
| 5-10c | 127 |
| 10-25c | 178 |
| 25-75c | 285 |
| 75-90c | 72 |
| 90-95c | 19 |
| >95c | 499 |

ColdMath в последних 250 temperature rows:

- Temperature rows: `250 / 250`.
- Notional: около `$40,100`.
- Median price: `2.9c`.
- `163` rows <=10c.
- `87` rows >=90c.

Практический вывод: топы не выглядят как люди, которые просто покупают один "очевидный" outcome. Они активно торгуют хвосты, near-certain NO/YES, иногда большими размерами, и почти наверняка используют автоматизацию, точные источники данных и execution logic. Копировать их сделки после факта опасно: публичная activity показывает результат уже после того, как edge мог исчезнуть.

## Главные риски

1. Geoblock / legal risk.

   Текущий `GET https://polymarket.com/api/geoblock` вернул `{"blocked": true, "country": "DE", "region": "HE"}`. Это значит: из этой среды live order placement недоступен. Нельзя строить стратегию на обходе геоблока.

2. Resolution source risk.

   Название рынка не равно правилам резолва. Нужно знать точную station/source, локальный день, timezone, единицы и округление. Ошибка на уровне станции может дать 3-8°F разницы, а bucket часто шириной 1-2°F или 1°C.

3. Model risk.

   Наша старая проверка May 4 малая:

   - Events: `17`.
   - Exact rounded temperature: `4 / 17 = 23.5%`.
   - Within 1 unit: `7 / 17 = 41.2%`.
   - Within 2 units: `11 / 17 = 64.7%`.
   - Top bucket hit: `12 / 17 = 70.6%`.
   - MAE: `1.54`.

   Это нормально для paper-гипотезы, но мало для live денег.

4. Execution risk.

   Polymarket показывает probability, но реальная ставка исполняется по orderbook. Нужно учитывать `bestBid`, `bestAsk`, spread, depth, slippage, очередь лимиток, min order size и задержки. Market order - это фактически лимитный ордер, который сразу бьет книгу.

5. Fees.

   Weather имеет taker fee rate `0.05`; makers fee `0`; maker rebate для Weather указан как `25%`. Но maker rebate не делает торговлю бесплатной: maker orders несут adverse selection, то есть тебя часто забирают именно тогда, когда у другого участника данные свежее.

6. Correlation risk.

   20 городов в один день не являются 20 независимыми ставками. Одна ошибка модели, источник данных или weather system может ударить по пачке позиций.

7. Leaderboard survivorship bias.

   Мы видим победителей. Мы не видим всех, кто пробовал такую же стратегию и слил банк.

8. "Obvious 95c" risk.

   Ставка по 95c выглядит надежной, пока одна ошибка не стирает прибыль примерно от 19 успешных ставок такого типа. Для банка $100 это особенно опасно.

## Консервативная стратегия для $100

Базовая идея: $100 - это не капитал для полноценной диверсификации. Это капитал для обучения execution и проверки edge. Поэтому live, когда он вообще станет допустимым, должен начинаться как micro-live, а не как попытка заработать.

Режим до live:

- Минимум `200-300` settled paper trades.
- Минимум `50` resolved events.
- Отдельная статистика по source/station/city/date/time-to-close.
- Отдельная статистика для maker vs taker simulation.
- Не доверять unrealized expected PnL до settlement.

Live risk limits для $100, если когда-нибудь будет легально доступно:

- Active risk capital: максимум `$20-30`, остальное reserve.
- Stake per position: `$0.25-0.50`.
- Max per event: `$1`.
- Max per city/day: `$1`.
- Max per date: `$5`.
- Daily loss cap: `$2`.
- Weekly loss cap: `$5`.
- Total drawdown stop: `-$10`.
- Запрещен martingale и averaging down.

Trade selection:

- Только рынки с verified station/source.
- Только когда forecast ensemble и live observation не конфликтуют.
- Для live требовать robust edge минимум `8-12c` после fees, spread, stress и slippage.
- Не брать taker, если spread > `2c`.
- Не ставить maker, если spread > `5c` или нет свежего orderbook heartbeat.
- Не торговать, если нет `bestBid/bestAsk`, рынок не принимает orders, book stale или min order size больше нашего allowed stake.
- Не покупать `NO` против top/near-top bucket модели.
- Не покупать 95c+ "очевидности", если actual еще не locked in официальным источником.
- Не chase: если limit order не fill, сделка просто пропускается.

## Что нужно доработать в проекте

Приоритет 1: `risk_profile_100`.

- Отдельный config для банка `$100`.
- Стейки `$0.25-0.50`, а не текущие paper-позиции по `$2.50`.
- Жесткие caps по event/city/date.
- Автоматический статус `NO_TRADE`, если risk profile нарушен.

Приоритет 2: orderbook depth и live-like fills.

- Забрать CLOB `/books` по token ids.
- Считать executable price для размера ставки.
- В paper не считать сделку filled, если в реальности она не могла исполниться.
- Для maker paper: fill только если следующий trade реально прошел через нашу limit price.

Приоритет 3: station/source verifier.

- Парсить description/rules.
- Извлекать station/source/timezone/unit/rounding.
- Если station/source не распознаны - `NO_TRADE`.

Приоритет 4: calibration report.

- Winrate по edge buckets.
- Winrate по cities.
- Winrate по source domains.
- Ошибка по time-to-close.
- Отдельно exact bucket, within 1 unit, within 2 units.

Приоритет 5: no-trade first dashboard.

- В панели показывать не только открытые ставки, но и сколько сигналов было отклонено и почему.
- Для надежности это важнее, чем список "красивых" сделок.

## Endpoint and Request Pattern Findings

| Method | Endpoint | Purpose | Auth | Artifact |
| --- | --- | --- | --- | --- |
| GET | `https://polymarket.com/weather` | Browser check of Weather category | Public | `dom/weather_page_snapshot.yml`, `screenshots/weather_page.png` |
| GET | `https://gamma-api.polymarket.com/events?active=true&closed=false&limit=250&tag_slug=weather&order=volume_24hr&ascending=false` | Current weather event discovery | Public | `dom/gamma_weather_events_curl.json` |
| GET | `https://data-api.polymarket.com/v1/leaderboard?category=WEATHER&timePeriod=<ALL|MONTH|WEEK|DAY>&orderBy=PNL&limit=25` | Weather leaderboard | Public | `dom/leaderboard_weather_*_pnl_curl.json` |
| GET | `https://data-api.polymarket.com/activity?user=<wallet>&limit=250` | Trader activity | Public | `dom/activity_*.json` |
| GET | `https://polymarket.com/api/geoblock` | Region/order eligibility check | Public | `network/geoblock_curl.json` |
| GET | `https://clob.polymarket.com/ok` | CLOB health check | Public | `network/clob_ok_curl.txt` |
| POST | `https://clob.polymarket.com/books?token_ids` | Browser-observed orderbook batch request | Public read endpoint | `network/browser_requests.txt` |
| POST | `https://clob.polymarket.com/last-trades-prices` | Browser-observed last trades request | Public read endpoint | `network/browser_requests.txt` |

## Artifact Index

Run directory:

`/var/folders/xq/gw35p3016bn0m520bctn3m340000gn/T/codex-web-investigations/20260505-171650-polymarket-weather-risk`

Important files:

- `screenshots/weather_page.png`
- `dom/weather_page_snapshot.yml`
- `dom/gamma_weather_events_curl.json`
- `dom/leaderboard_weather_all_pnl_curl.json`
- `dom/leaderboard_weather_month_pnl_curl.json`
- `dom/leaderboard_weather_week_pnl_curl.json`
- `dom/leaderboard_weather_day_pnl_curl.json`
- `dom/activity_ColdMath_0x594edb91.json`
- `network/browser_requests.txt`
- `network/curl_manifest.tsv`
- `network/activity_manifest.json`
- `network/geoblock_curl.json`
- `notes/leader_activity_summary.csv`

The run directory has `.keep`, so it should not be auto-cleaned by the investigation TTL.

## Sources

- Polymarket API overview: https://docs.polymarket.com/api-reference/introduction
- Polymarket market fetching docs: https://docs.polymarket.com/market-data/fetching-markets
- Polymarket orderbook docs: https://docs.polymarket.com/trading/orderbook
- Polymarket fees docs: https://docs.polymarket.com/trading/fees
- Polymarket maker rebates docs: https://docs.polymarket.com/market-makers/maker-rebates
- Polymarket order lifecycle docs: https://docs.polymarket.com/concepts/order-lifecycle
- Polymarket authentication docs: https://docs.polymarket.com/api-reference/authentication
- Polymarket geoblock docs: https://docs.polymarket.com/cn/api-reference/geoblock
- Polymarket Weather page: https://polymarket.com/weather
