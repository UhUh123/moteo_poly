# Polymarket Weather Strategy Research

Дата исследования: 2026-05-04.

Это рабочий анализ для weather/temperature markets. Он не является финансовой или юридической рекомендацией. Для live trading сначала нужна проверка доступности Polymarket в конкретной юрисдикции и аккаунте.

## Короткий вывод

Погода на Polymarket выглядит как подходящая ниша для системной стратегии, но не для стратегии "у нас есть модель температуры, значит торгуем". Деньги там, судя по публичным leaderboard данным, зарабатывают не только прогнозом, а связкой:

- точное чтение resolution rules и станции;
- быстрый сбор публичных погодных данных;
- вероятностная калибровка по диапазонам;
- учет bid/ask, taker fees, slippage и depth;
- passive execution / market making;
- строгие лимиты риска;
- paper log и последующая проверка на resolved markets.

## Что видно по рынку

Живая страница `https://polymarket.com/weather` на момент исследования показывала:

- 170 weather events в embedded Next.js state;
- 122 temperature-related events;
- 583 binary markets внутри этих temperature events;
- около 5.2M USD суммарного event volume по temperature-related events;
- daily temperature markets в основном на May 4, May 5, May 6.

На странице Polymarket указано, что weather markets торгуются как Yes/No shares, цена 0-100 cents интерпретируется как implied probability, а winning shares платят $1.

## Конкуренция и успешные кейсы

Официальный `data-api.polymarket.com/v1/leaderboard` поддерживает `category=WEATHER`.
Live snapshot all-time weather leaderboard показал:

| rank | user | PnL | volume |
| ---: | --- | ---: | ---: |
| 1 | gopfan2 | 345,941 | 4,575,064 |
| 2 | aenews2 | 277,050 | 9,972,309 |
| 3 | ColdMath | 129,007 | 9,514,851 |
| 4 | gopfan | 118,426 | 739,901 |
| 5 | bama124 | 86,601 | 410,556 |

Интерпретация: ниша реальная, но уже автоматизированная и конкурентная. Recent activity по ColdMath показал почти полностью weather-like сделки в последних 200 rows, частые entries в диапазонах low/high температуры и сделки как около экстремальных цен 1-10c, так и около 90-98c. Это похоже на системный/алгоритмический подход, но публичные данные не доказывают точную стратегию.

## Основные стратегии

### 1. Forecast edge

Модель оценивает распределение финальной температуры станции, не просто точку. Для каждого outcome считается:

`fair_prob = P(temp попадает в диапазон outcome)`

Сделка появляется только если:

`fair_prob - executable_price - fees - slippage - safety_margin > threshold`

Наивная точечная модель недостаточна, потому что рынки торгуют диапазоны.

### 2. Nowcasting / data-lock edge

Ближе к концу локального дня максимум/минимум часто уже почти известен из METAR/ASOS, Wunderground, HKO, Synoptic/NWS. Edge возникает, если рынок не успел переоценить outcome после свежего наблюдения.

Главный риск: перепутать station, local day, timezone, unit, finalization delay или rounding.

### 3. Market making

Для weather category официальная fee table показывает taker fee rate 0.05, maker fee 0 и maker rebate 25%. Это делает passive quoting потенциально лучше taker trading, особенно при широкой книге.

Риск: adverse selection. Если мы стоим лимитками без более свежей погоды, нас будут забирать те, кто знает больше.

### 4. Multi-outcome / negative-risk logic

Temperature event состоит из нескольких binary outcomes. Нужно проверять `negRisk` и сумму вероятностей по всем Yes. Возможны mispricing/arb-like ситуации, но после fees, spread, conversion mechanics и execution риск "free money" часто исчезает.

### 5. Cross-venue / cross-source

Потенциально сравнивать Polymarket с Kalshi/другими venue и с raw weather feeds. Это отдельный слой и зависит от легальной доступности venue.

## Главные риски

- География и право: Polymarket docs требуют geoblock check перед ордерами; US, NL, GB, DE, FR, IT и ряд стран указаны как blocked для order placement на polymarket.com.
- Нельзя обходить геоблоки/VPN. Read-only API можно использовать для исследования, live trading только из разрешенной юрисдикции и аккаунта.
- Resolution source risk: title не равен rules. Нужно читать конкретный market description/source.
- Data mismatch: Wunderground page может отличаться от NOAA/Open-Meteo/ERA5; HKO и weather.gov имеют свои форматы.
- Timezone/local-day risk: максимум/минимум считается за локальный день станции.
- Unit/rounding risk: Fahrenheit ranges часто по 2°F, Celsius по 1°C или "or higher/below".
- Fee/spread/slippage: displayed probability не равна executable price.
- Model risk: текущая GBM обучена на historical proxy, а не на большом наборе resolved Polymarket labels.
- Correlation risk: города не независимы; одна weather system может убить много похожих позиций.
- Latency competition: leaderboard показывает, что weather уже торгуют опытные автоматизированные участники.
- Resolution/UMA delays: undisputed resolution около 2 часов after proposal, disputed может занять дни.

## Минимальный бюджет

- 0 USD: research + paper trading.
- 100-300 USD: только micro live test, если юридически доступно. Цель - проверить execution/accounting, а не заработать.
- 500-2,000 USD: минимально разумный bankroll для weather basket с лимитами, потому что нужно распределяться по городам/outcomes и переживать дисперсию.
- 5,000+ USD: имеет смысл только после 100-300 paper/live signals с доказанным positive EV после fees/slippage.

Стартовый риск-лимит: не больше 0.25-1.0% bankroll на один binary outcome и не больше 3-5% на один город/дату.

## Что строить перед подключением live trading

1. Polymarket read-only scanner: events, markets, token ids, bid/ask, spread, depth, fee info, negRisk. Реализовано как `scan-polymarket-weather`.
2. Probability engine: перевод `prediction_c` и uncertainty в probabilities по всем ranges. Первый вариант реализован как normal interval model в `build-market-signals`.
3. Edge engine: fair probability vs executable price with fees/slippage. Первый вариант пишет `yes_net_edge`, `no_net_edge`, `paper_side`.
4. Paper execution log: every signal, theoretical fill, realized outcome. Первый файл: `artifacts/market_signals.csv`.
5. Risk engine: bankroll limits, correlation caps, max daily loss, no-trade when data quality is bad.
6. Live execution only after legal/geoblock check and paper validation.

## Реализованный paper-signal слой

Команды:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli scan-polymarket-weather \
  --output data/polymarket_weather_markets.csv \
  --raw-output data/polymarket_weather_events.json \
  --geoblock-output data/polymarket_geoblock.json

PYTHONPATH=src python3 -m detect_temperature.cli build-market-signals \
  --markets data/polymarket_weather_markets.csv \
  --predictions artifacts/predictions_gbm.csv \
  --output artifacts/market_signals.csv \
  --sigma-c 1.5 \
  --min-edge 0.03 \
  --weather-fee-rate 0.05
```

`betting_v2_conservative` добавляет фильтры поверх первого probability/edge слоя:

- не торговать завершенные, закрытые, неактивные рынки и рынки с wide spread;
- не покупать `NO` против bucket'а, который модель считает главным или почти главным;
- требовать минимум `8%` вероятности для `BUY_YES` и `55%` вероятности выбранной стороны для `BUY_NO`;
- писать в CSV `visible_top_bucket`, `visible_bucket_rank`, `decision_reason`, `risk_flags`.

Текущий snapshot после v2:

- `610` temperature binary markets scanned;
- `415` rows matched to existing GBM event predictions;
- `157` paper trade candidates at `min_edge=0.03`;
- `0` candidates on already-ended markets;
- `0` `BUY_NO` candidates against the model top/near-top bucket;
- current geoblock check returned `blocked=true`, so this remains paper-only.

Важно: high edge в `market_signals.csv` не означает, что надо торговать. На этом этапе это гипотезы для forward-test. Следующая проверка — дождаться resolved actuals и сравнить paper signals with outcomes.

## Paper portfolio dashboard

Добавлен слой виртуального bankroll:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli open-paper-trades \
  --signals artifacts/market_signals.csv \
  --output artifacts/paper_portfolio.csv \
  --state artifacts/paper_portfolio.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000
```

Он отбирает только pre-end paper candidates, применяет лимиты риска и создает `artifacts/paper_dashboard.html`.

Текущий запуск:

- bankroll: `1000 USDC`;
- opened positions: `100`;
- total virtual stake: `500 USDC`;
- cash left: `500 USDC`;
- все позиции пока `open`;
- dashboard: `artifacts/paper_dashboard.html`.

Завтра после `collect-actuals`:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli settle-paper-trades \
  --portfolio artifacts/paper_portfolio.csv \
  --actuals data/actuals.csv \
  --output artifacts/paper_portfolio_settled.csv \
  --state artifacts/paper_portfolio_settled.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000
```

После settlement dashboard покажет realized PnL, win rate, выигравшие/проигравшие позиции и actual temperature.

## Strategy Lab / Robust Filter + Maker Paper Mode

Добавлен отдельный слой анализа, который не ждет resolve рынков и не меняет основной paper-портфель автоматически. Он отвечает на два вопроса:

- какие v2-сигналы остаются приемлемыми, если модель ошиблась на 1°C, uncertainty шире, а исполнение хуже на 1 cent;
- где лучше не брать taker price сразу, а поставить passive limit order внутри spread.

```bash
PYTHONPATH=src python3 -m detect_temperature.cli run-strategy-lab \
  --signals artifacts/market_signals.csv \
  --candidates-output artifacts/strategy_candidates_v2.csv \
  --portfolio-output artifacts/strategy_portfolio_v2.csv \
  --summary-output artifacts/strategy_lab_summary.json \
  --report artifacts/strategy_lab_report.html \
  --bankroll-usdc 1000 \
  --robust-min-edge 0.01 \
  --max-event-positions 2 \
  --max-city-positions 4 \
  --max-date-exposure-pct 0.30 \
  --max-execution-slippage 0.02 \
  --maker-quote-improvement 0.005 \
  --maker-min-fill-score 0.35 \
  --maker-adverse-selection-penalty 0.01
```

Текущий запуск:

- `157` v2 trade candidates;
- `78` проходят stress scenarios и execution checks;
- `59` попали в optimized robust portfolio после лимитов риска, концентрации и execution penalty;
- selected stake: `292.56 USDC` из `1000 USDC`;
- минимальный worst-case edge среди selected: `1.71%`;
- средний worst-case edge среди selected: `10.72%`;
- execution-adjusted expected PnL: `469.55 USDC`;
- maker if-filled expected PnL: `797.45 USDC`;
- maker fill-adjusted expected PnL: `438.88 USDC`;
- maker-eligible positions: `58` из `59`;
- maker-preferred positions: `6` из `59`;
- selected positions покрывают `38` событий;
- max city concentration: `4` позиции / `20 USDC`;
- max date concentration: `277.56 USDC` на May 5;
- selected execution quality: `22 good`, `36 fair`, `1 poor`.

Execution realism layer оценивает дополнительный slippage по `spread`, `liquidity`, `market_volume` и экстремальности цены. Это не заменяет настоящий orderbook depth, но уже делает paper-selection менее наивной.

Maker paper mode оценивает лимитную цену через `best_bid/best_ask`, ожидаемый шанс исполнения через spread/liquidity/volume и штрафует adverse selection. Текущий вывод простой: passive quoting полезен, но не как blanket-правило. По текущему snapshot только `6` selected позиций выглядят лучше через maker-вход; для остальных taker execution-adjusted expectation выше.

Чтобы forward-test шел через одну панель, Strategy Lab portfolio теперь можно открыть как основной paper portfolio:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli open-strategy-paper-trades \
  --strategy-portfolio artifacts/strategy_portfolio_v2.csv \
  --output artifacts/paper_portfolio.csv \
  --state artifacts/paper_portfolio.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000 \
  --execution-mode taker
```

Режим `--execution-mode taker` использует `execution_price` из Strategy Lab, то есть цену уже с estimated slippage. Режим `maker-preferred` нужен только для отдельного what-if, потому что без live orderbook log мы не знаем, была бы лимитка реально исполнена.

Это не доказательство прибыльности. Это фильтр стабильности перед forward-test: он отсекает сделки, чей edge исчезает от небольшого сдвига прогноза, ухудшения исполнения или плохого quote quality.

## Источники и артефакты

Run dir:

`/var/folders/xq/gw35p3016bn0m520bctn3m340000gn/T/codex-web-investigations/20260504-192446-polymarket-weather-strategy`

Key artifacts:

- `dom/polymarket_weather_page.html`
- `dom/polymarket_weather_next_data.json`
- `dom/weather_temperature_events_summary.json`
- `dom/data_api_weather_leaderboard_all_pnl.json`
- `dom/data_api_weather_leaderboard_month_pnl.json`
- `dom/data_api_coldmath_activity.json`
- `network/sample_clob_orderbook.json`
- `network/polymarket_geoblock.json`
- `network/endpoint_inventory.md`

Primary sources:

- https://polymarket.com/weather
- https://docs.polymarket.com/api-reference/introduction
- https://docs.polymarket.com/market-data/fetching-markets
- https://docs.polymarket.com/concepts/prices-orderbook
- https://docs.polymarket.com/trading/orderbook
- https://docs.polymarket.com/trading/fees
- https://docs.polymarket.com/concepts/resolution
- https://docs.polymarket.com/api-reference/geoblock
- https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
