# Detect Temperature

Каркас модели для точечного прогноза максимальной/минимальной температуры по погодным рынкам Polymarket.

Статья в корне проекта сводится к практической архитектуре: не пытаться предсказывать температуру только из названия города, а строить двухуровневую гибридную модель:

1. Базовый прогноз погоды/NWP для точки и даты.
2. ML-коррекция смещения по геопризнакам, станции, календарю и свежим METAR/ASOS-наблюдениям.

## Что уже есть

- `scrape_polymarket_weather.py` собирает температурные рынки и resolution source.
- `weather_markets.json` и `weather_markets.csv` содержат текущие рынки.
- `src/detect_temperature` добавляет нормализацию рынков, источники данных, признаки и модельный слой.

## Текущий статус данных

- `data/targets.csv`: 86 точечных high/low рынков.
- `data/stations.cache.json`: каталог AviationWeather для координат аэропортов.
- `data/manual_stations.csv`: ручные станции для не-ICAO источников, сейчас `HKO`.
- `data/features.csv`: 86 строк, все с координатами и Open-Meteo baseline-прогнозом.
- `artifacts/predictions.csv`: 86 baseline-предсказаний в Celsius и в единицах resolution source.
- `data/actuals.csv`: фактические resolved значения; сейчас строки `pending`, потому что целевые даты ещё не прошли с лагом финализации.
- `data/historical_observed.csv` и `data/training.csv`: исторический тестовый слой для обучения поправки к baseline.
- `artifacts/models/gbm.joblib`: обучаемая модель поправки, создается командой `train-gbm`.
- `data/polymarket_weather_markets.csv`: read-only snapshot live temperature markets с Polymarket.
- `artifacts/market_signals.csv`: paper-only сигналы; это не ордера и не live trading.

## Первый запуск

Из корня проекта:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli build-targets \
  --input weather_markets.json \
  --csv data/targets.csv \
  --jsonl data/targets.jsonl
```

Это создаст таблицу целевых задач: `slug`, город, дата рынка, тип экстремума (`max`/`min`), единицы разрешения, station id из Wunderground/weather.gov и исходная ссылка.

Дальше можно собрать статические признаки без сети:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli build-features \
  --targets data/targets.csv \
  --output data/features.csv
```

Когда нужен базовый NWP-прогноз, сначала обогащаем станции координатами через AviationWeather station cache, затем подключаем Open-Meteo:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli refresh-stations \
  --output data/stations.cache.json

PYTHONPATH=src python3 -m detect_temperature.cli build-features \
  --targets data/targets.csv \
  --manual-stations data/manual_stations.csv \
  --stations data/stations.cache.json \
  --with-open-meteo \
  --output data/features.csv

PYTHONPATH=src python3 -m detect_temperature.cli predict-baseline \
  --features data/features.csv \
  --output artifacts/predictions.csv
```

`data/manual_stations.csv` нужен для не-авиационных resolution source. Сейчас там есть `HKO` для рынков Hong Kong Observatory.

Фактические значения для обучения собираются отдельной командой. По умолчанию она ждёт минимум один день после даты рынка, чтобы не записать неполный дневной максимум/минимум:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli collect-actuals \
  --targets data/targets.csv \
  --stations data/stations.cache.json \
  --manual-stations data/manual_stations.csv \
  --output data/actuals.csv
```

Исторический датасет уже можно использовать для первой обучаемой модели. Команда ниже считает holdout-качество и сохраняет финальную модель:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli train-gbm \
  --training data/training.csv \
  --model artifacts/models/gbm.joblib \
  --metrics artifacts/model_metrics.json \
  --holdout-predictions artifacts/holdout_predictions.csv \
  --report artifacts/model_report.md
```

После этого можно сделать corrected-прогнозы для текущих рынков:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli predict-gbm \
  --features data/features.csv \
  --model artifacts/models/gbm.joblib \
  --output artifacts/predictions_gbm.csv
```

Read-only Polymarket scanner и paper signals:

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

`data/polymarket_geoblock.json` нужно проверять перед любыми live-ордерами. Если `blocked=true`, система должна оставаться в read-only/paper режиме.

Открыть бумажный портфель с виртуальным bankroll и dashboard:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli open-paper-trades \
  --signals artifacts/market_signals.csv \
  --output artifacts/paper_portfolio.csv \
  --state artifacts/paper_portfolio.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000
```

Strategy Lab проверяет текущие paper-сигналы на устойчивость: пересчитывает edge при сдвиге прогноза, более широкой sigma и ухудшенном исполнении. Сначала он делает отдельный отчет:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli run-strategy-lab \
  --signals artifacts/market_signals.csv \
  --candidates-output artifacts/strategy_candidates_v2.csv \
  --portfolio-output artifacts/strategy_portfolio_v2.csv \
  --summary-output artifacts/strategy_lab_summary.json \
  --report artifacts/strategy_lab_report.html \
  --bankroll-usdc 1000 \
  --robust-min-edge 0.01 \
  --max-city-positions 4 \
  --max-date-exposure-pct 0.30 \
  --max-execution-slippage 0.02 \
  --maker-quote-improvement 0.005 \
  --maker-min-fill-score 0.35 \
  --maker-adverse-selection-penalty 0.01
```

Отчет также считает paper maker-mode: где лучше ставить лимитку внутри spread, какой шанс исполнения у такой лимитки и где maker-вход выглядит лучше taker-входа после slippage/fee.

После этого выбранный robust portfolio можно открыть в основной paper-панели. Это текущий предпочтительный forward-test, потому что dashboard будет отслеживать не все сырые сигналы, а только отфильтрованные Strategy Lab позиции:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli open-strategy-paper-trades \
  --strategy-portfolio artifacts/strategy_portfolio_v2.csv \
  --output artifacts/paper_portfolio.csv \
  --state artifacts/paper_portfolio.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000 \
  --execution-mode taker
```

Для отдельного what-if можно заменить `--execution-mode taker` на `maker-preferred`; тогда позиции, где Strategy Lab предпочитает лимитку, будут записаны как maker paper-вход. Это гипотеза об исполнении, а не доказанный fill.

После того как `collect-actuals` начнет писать строки `status=ok`, пересчитать paper PnL:

```bash
PYTHONPATH=src python3 -m detect_temperature.cli settle-paper-trades \
  --portfolio artifacts/paper_portfolio.csv \
  --actuals data/actuals.csv \
  --output artifacts/paper_portfolio_settled.csv \
  --state artifacts/paper_portfolio_settled.json \
  --dashboard artifacts/paper_dashboard.html \
  --bankroll-usdc 1000
```

Интерактивная панель с кнопкой обновления actuals/PnL:

```bash
PYTHONPATH=src python3 scripts/serve_paper_dashboard.py \
  --host 127.0.0.1 \
  --port 8765 \
  --bankroll-usdc 1000
```

После запуска открыть `http://127.0.0.1:8765/`. Кнопка `Refresh actuals & PnL` собирает новые actuals, оставляет нерезолвнутые позиции открытыми и пересчитывает выигрыш/проигрыш для тех позиций, где фактическая температура уже доступна.

## Дальнейший путь

- Сохранить фактические resolved high/low значения из Wunderground в `observed_temp_c`.
- Накапливать rolling station bias: `observed_temp_c - forecast_baseline_c`.
- Сравнивать baseline и `BiasCorrectedGBM` на новых resolved рынках, когда `data/actuals.csv` начнет получать строки `ok`.
- Валидировать `artifacts/market_signals.csv` на resolved рынках до подключения реальных ордеров.
- Добавить геослои из статьи: DEM/SRTM, land cover, built-up density, distance to water.
