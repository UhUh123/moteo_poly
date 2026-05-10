# HANDOFF — Polymarket Weather Paper Trading

Документ для новой AI-сессии или нового разработчика. Цель: за 10 минут понять, что происходит в проекте, что уже сделано, что делать дальше, без необходимости перечитывать всю историю переписки.

Язык общения с пользователем — русский.

---

## 0. Что это за проект

Paper-trading инфраструктура для **температурных рынков Polymarket**. Мы не торгуем реальными деньгами (платформа geoblock'нута в DE). Мы строим и калибруем модель прогноза вероятностей температурных bucket'ов, чтобы потом — если/когда live станет возможен — иметь проверенную стратегию с доказанным положительным матожиданием.

**Юзер:** `opipsy` (мак), бюджет мысленно фиксирован на $100 (профиль `bankroll_100`). Философия: «стабильность важнее доходности, не потерять деньги важнее заработать».

**Настоящий и текущий физический доступ:**
- Мак (`macbook-air`, Tailscale IP `100.88.205.53`) — рабочий узел, здесь код, здесь dashboard.
- Windows PC (`desktop-pic12cl`, пользователь `wopipsy`, Tailscale IP `100.105.99.20`) — collector 24/7 через Task Scheduler. Агент заходит по SSH с ключом `~/.ssh/poly_collector_ed25519`.

---

## 1. Философия и стратегия (обязательно прочитать)

1. **Мы предсказываем не температуру, а вероятности bucket'ов.** Модель прогнозирует `corrected_prediction_c`, затем `signals.py` переводит это в вероятность попадания в каждый bucket через нормальное распределение вокруг прогноза с параметром sigma. Edge = наша вероятность − цена рынка − комиссия.
2. **Комиссия:** `0.05 × price × (1 − price)`. Пик в 50/50, минимум у краёв.
3. **BUY_YES отключен в `bankroll_100`.** Резолв почему: на двух settled прогонах модель давала 0–3% win rate на BUY_YES — она системно переоценивает уверенность в узких bucket'ах. BUY_NO работал (60–76% win rate). До пересборки sigma по реальным данным BUY_YES не включать.
4. **Sigma захардкожена 2.5 °C.** Реальный MAE на 56 resolved событиях (2026-05-05) = 1.76 °C, training holdout давал 0.51 °C — синтетика. 2.5 — запас на реальную ошибку.
5. **Geoblock активен.** Все live-ордера запрещены, пока `data/polymarket_geoblock.json` возвращает `blocked=true`. Не обсуждать обход. VPN — категорически нет.
6. **Drawdown −$10 = стоп.** `risk_guards.DrawdownAbort` в paper_server и CLI. Если realized PnL пробил −$10 — торговля останавливается автоматически, exit code 3.

---

## 2. Архитектура и поток данных

```
Polymarket weather page (HTML + __NEXT_DATA__)
        │
        ▼
scan-polymarket-weather  ──►  data/polymarket_weather_markets.csv
                              data/polymarket_weather_events.json
                              data/polymarket_geoblock.json
        │
        ▼
build-polymarket-targets ──►  data/targets.csv  (reference + new discovery)
        │
        ▼
build-features           ──►  data/features.csv
  ├─ AviationWeatherStationCatalog (ICAO coords)
  ├─ ManualStationCatalog (HKO etc.)
  └─ OpenMeteoForecastProvider (24h forecast)
  + station_verifier.verify_target → station_verified + reason
        │
        ▼
predict-gbm              ──►  artifacts/predictions_gbm.csv
  model: artifacts/models/gbm.joblib (HistGradientBoosting bias corrector on residual)
        │
        ▼
build-market-signals     ──►  artifacts/market_signals.csv
  sigma_c, min_edge, min_yes/no_prob, max_spread, min_liquidity, allow_buy_yes
  station_verified=0 → forced NO_TRADE
        │
        ▼
fetch-clob-orderbooks    ──►  data/polymarket_orderbooks.json (CLOB /books)
        │
        ▼
run-strategy-lab         ──►  artifacts/strategy_candidates_v2.csv
                              artifacts/strategy_portfolio_v2.csv
                              artifacts/strategy_lab_summary.json
                              artifacts/strategy_lab_report.html
  stress: mean_shift × sigma × slippage
  execution: spread/liquidity/volume/orderbook depth check
  maker_profile: hypothetical limit inside spread (paper only)
  portfolio optimizer: total/event/city/date/extreme caps, diversified score
        │
        ▼
open-strategy-paper-trades  ──►  artifacts/paper_portfolio.csv
                                 artifacts/paper_portfolio.json
                                 artifacts/paper_dashboard.html
  drawdown check before open (risk_guards.check_drawdown)
        │
        ▼ (сутки позже, когда actuals доступны)
collect-actuals         ──►  data/actuals.csv (Wunderground / HKO / Synoptic)
        │
        ▼
settle-paper-trades     ──►  artifacts/paper_portfolio.csv (updated with won/lost/pnl)
                             artifacts/paper_portfolio_settled.json
                             artifacts/paper_dashboard.html
```

---

## 3. Модули (`src/detect_temperature/`)

| Файл | Назначение |
|---|---|
| [cli.py](src/detect_temperature/cli.py) | Единая точка входа, все команды. Применяет risk-profile через `_apply_risk_profile`, проверяет drawdown. |
| [pipeline.py](src/detect_temperature/pipeline.py) | Тонкие обёртки: `build_targets`, `build_features`, `predict_gbm`, `build_market_signals`, `run_strategy_lab`, и т.д. |
| [schema.py](src/detect_temperature/schema.py) | `MarketTarget` dataclass. |
| [markets.py](src/detect_temperature/markets.py) | Нормализация Polymarket events/markets в `MarketTarget`. |
| [features.py](src/detect_temperature/features.py) | Сборка feature-строки: таргет + станция + forecast + observation + **station_verified**. |
| [sources/](src/detect_temperature/sources/) | `base.py` (Protocol'ы), `aviation_weather.py` (ICAO), `manual.py`, `open_meteo.py`, `actuals.py` (Wunderground/HKO/Synoptic). |
| [models/baseline.py](src/detect_temperature/models/baseline.py) | Trivial NWP baseline: берёт forecast_temp_max_c / min. |
| [models/gbm.py](src/detect_temperature/models/gbm.py) | HistGradientBoostingRegressor + SimpleImputer. Обучается на residual = observed − baseline. |
| [evaluation.py](src/detect_temperature/evaluation.py) | MAE/RMSE/bias/within-1/2/3C, time-ordered split. |
| [signals.py](src/detect_temperature/signals.py) | Парсит bucket-интервалы из вопроса, считает normal CDF, health-check, betting decision. **Поддерживает `allow_buy_yes` и проверку `station_verified`.** |
| [strategy_lab.py](src/detect_temperature/strategy_lab.py) | Stress-scenarios, execution profile (с CLOB depth), maker profile (гипотеза), portfolio optimizer. |
| [paper.py](src/detect_temperature/paper.py) | `open_paper_portfolio`, `open_strategy_paper_portfolio`, `settle_paper_portfolio`, `render_paper_dashboard` (HTML inline). Содержит 3 кнопки в header. |
| [paper_server.py](src/detect_temperature/paper_server.py) | ThreadingHTTPServer. Endpoints: `POST /api/refresh-paper`, `POST /api/open-trades`, `POST /api/dry-run`. `run_open_trades_pipeline` — full pipeline из dashboard. `_archive_current_run` перед overwrite. |
| [polymarket.py](src/detect_temperature/polymarket.py) | `PolymarketWeatherClient` (HTML + __NEXT_DATA__), `PolymarketClobClient` (/books), `flatten_temperature_markets`. |
| [resolved_eval.py](src/detect_temperature/resolved_eval.py) | Resolved-check: MAE, rounded exact, within 1/2 unit, signal win-rate. HTML-отчёт. |
| [risk_profiles.py](src/detect_temperature/risk_profiles.py) | `bankroll_100` пресет. |
| [risk_guards.py](src/detect_temperature/risk_guards.py) | `check_drawdown` + `DrawdownAbort`. |
| [station_verifier.py](src/detect_temperature/station_verifier.py) | `verify_target` — ICAO regex, supported domains (`wunderground.com`, `weather.gov`, `weather.gov.hk`), HKO special case. |
| [units.py](src/detect_temperature/units.py) | celsius_to_fahrenheit + normalize_temperature. |

---

## 4. Текущее состояние

### Фаза 1 — завершена ✅

- [risk_profiles.bankroll_100](src/detect_temperature/risk_profiles.py) обновлён: sigma 2.5, `allow_buy_yes=False`, stake $0.25, `robust_min_edge=0.10`, stress shifts ±2, sigma 2.5–3.5, `drawdown_abort_usdc=-10.0`.
- [signals.py](src/detect_temperature/signals.py): `allow_buy_yes` проброшен через CLI/pipeline/signals; `station_verified` → forced NO_TRADE.
- [risk_guards.py](src/detect_temperature/risk_guards.py): kill-switch с exit code 3.
- [station_verifier.py](src/detect_temperature/station_verifier.py): `verify_target()` + `annotate_feature_row`.
- [paper_server.py](src/detect_temperature/paper_server.py): 3 кнопки, архивация в `artifacts/paper_runs/<ts>-<label>/`.
- Dashboard в [paper.py](src/detect_temperature/paper.py): header с 3 кнопками: **Проверить рынок** (dry-run), **Открыть сделки** (open-trades), **Обновить actuals & PnL** (refresh-paper).
- Тесты: **43 passed** (`PYTHONPATH=src python3 -m pytest -q`). Новый [test_risk_guards.py](tests/test_risk_guards.py) покрывает все guard'ы.

### Фаза 2a — модель на реальных данных ✅

- [scripts/build_historical_training.py](scripts/build_historical_training.py) — скачивает для каждой ICAO станции:
  - observed daily max/min из Open-Meteo ERA5 archive (https://archive-api.open-meteo.com/v1/archive)
  - operational forecast из Open-Meteo Historical Forecast API (https://historical-forecast-api.open-meteo.com/v1/forecast)
- [data/training_stations.json](data/training_stations.json): 51 верифицированная станция.
- [data/training_real.csv](data/training_real.csv): **124 032 строки** за 2023-01-01 … 2026-04-30 (было 3100 синтетических).
- [scripts/compare_training_sources.py](scripts/compare_training_sources.py) сравнивает модели на одном holdout.
- **Holdout-метрики на real data (18 666 событий, 2025-10-30 … 2026-04-30):**
  - Baseline (Open-Meteo raw): MAE 0.547°C, within-1C 81.2%
  - Synthetic GBM (старая): MAE 0.734°C — **ухудшала baseline**
  - Real GBM (новая): **MAE 0.455°C**, within-1C 83.8%, within-2C 96.3%
- Production model: `artifacts/models/gbm.joblib` (копия `gbm_real.joblib`). Старая сохранена как `gbm_synthetic_backup.joblib`.
- После переключения: сигналы `bankroll_100` дают 35 BUY_NO кандидатов (было 0 на устаревших снимках).

### Фаза 2b — per-station sigma ✅

- [scripts/build_station_calibration.py](scripts/build_station_calibration.py) считает per-station rolling MAE + bias на holdout (2025-10-30..2026-04-30).
- [data/station_calibration.csv](data/station_calibration.csv): 51 станция. Диапазон MAE: 0.01°C (тропики, где модель почти детерминирована) … 1.34°C (RKSI Сеул, атмосферные фронты). Bias: -0.40 .. +0.41.
- [signals.py](src/detect_temperature/signals.py): `sigma_for_station()` и `load_station_calibrations()`. Эффективная sigma = `max(1.5, 1.5 × rolling_mae)`. Каждая signal-строка получает `model_sigma_c` и `model_sigma_source` (`station_calibration` или `default`).
- [pipeline.py](src/detect_temperature/pipeline.py): по умолчанию читает `data/station_calibration.csv` (параметр `station_calibration_path`).
- После переключения на новые sigma: сигналов `bankroll_100` стало 57 BUY_NO кандидатов (с 35 при sigma=2.5 для всех). Ужесточение для noisy станций + смягчение для tropical.
- Тесты: **46 passed** (добавлено 3 новых теста на sigma_for_station, load_station_calibrations, build_market_signal use of station sigma).

### Фаза 2c — rolling bias correction ✅

- [pipeline.predict_gbm](src/detect_temperature/pipeline.py): добавлен параметр `station_calibration_path` (default `data/station_calibration.csv`). Для каждой строки вычитается `rolling_bias_c`:
  - `gbm_prediction_c` — raw output модели
  - `station_bias_c` — вычтенный bias
  - `corrected_prediction_c = gbm_prediction_c - station_bias_c` — то, что идёт в signals
  - `bias_correction_applied` — флаг 0/1
- CLI: `--station-calibration` флаг, пустая строка выключает.
- **Бэктест на том же holdout (2025-10-30..2026-04-30):**
  - MAE 0.4136 → **0.4045** (-2.2%)
  - within-1°C 85.7% → 86.3%
  - High-bias station: EHAM -0.040, EPWA -0.055, KBKF -0.032 в MAE.
  - Предупреждение: holdout == calibration → небольшой leakage. На fresh forward data прирост будет меньше.
- **Замер на 50 реальных Polymarket resolved событиях (2026-05-04/05):**
  - baseline (raw Open-Meteo): MAE 1.070°C, within-1C 58%, within-2C 88%
  - старая synthetic GBM: MAE 1.313°C (**вредила** baseline)
  - new real GBM + bias: MAE **1.079°C**, within-1C 58%, within-2C 88%
  - Вывод: мы **убрали вред**, но forward improvement ещё не видно на 50 событиях. Нужны свежие forward прогоны.
- 46 тестов зелёные (мак + Windows).

### Windows collector — работает

- `C:\poly\detect-temperature` — репо на Windows, установлен через venv, Python 3.14.
- `PolymarketCollectorRegular` (Task Scheduler): каждые 5 минут → scan + orderbooks + snapshot в `data/history/YYYY-MM-DD/HHMMSS-regular/`.
- `PolymarketCollectorHot`: каждую минуту, проверяет close-within-60-min, агрессивно снимает orderbooks только для закрывающихся рынков.
- Логи: `C:\poly\detect-temperature\logs\collector.log`.
- LastTaskResult=0 на всех прогонах, 848 рынков в snapshot.

### Верифицировано

- MAE resolved (2026-05-05, 56 событий): **1.76 °C** vs training holdout 0.51 °C — train набор синтетический.
- Settled paper: старый профиль (`bankroll_1000`, sigma=1.5, YES on) → win 44%, PnL −$90 на $1000. С новым `bankroll_100` на том же snapshot'е: **11 robust pass, 11 selected, stake $2.75, all BUY_NO, worst_edge 12.4%**.
- Drawdown kill-switch проверен вручную: `-$15 < -$10` → exit 3, paper файлы не создаются.

---

## 5. План действий

### Сейчас (неделя 1–2)

**Цель:** 50+ settled paper-позиций с новыми guard'ами, подтвердить win rate ≥ 70% для BUY_NO.

Ежедневная рутина юзера:
1. Вечером UTC открыть dashboard: `PYTHONPATH=src python3 scripts/serve_paper_dashboard.py --port 8765 --bankroll-usdc 100`.
2. Нажать **Проверить рынок** — убедиться, что pipeline проходит.
3. Нажать **Открыть сделки** (если `robust_pass > 0`) — откроется новый paper-портфель.
4. На следующий день **Обновить actuals & PnL**.

Windows collector работает сам — проверять логи раз в 2–3 дня.

### Фаза 3 — near-close re-pricing ✅ (MVP)

- [near_close.py](src/detect_temperature/near_close.py): `refined_bucket_probability` пересчитывает вероятность bucket'а с учётом (а) observed max/min до текущего часа, (б) shrink sigma по `sqrt(T_remaining/24)` с флором `0.25 × sigma`. Bucket математически резолвится, если observed max уже превысил `upper` (lose) или observed min ушёл ниже `lower` (lose), либо все оставшиеся часы почти наверняка не сдвинут исход (win).
- `fetch_intraday_max_min`: один вызов Open-Meteo forecast (hourly temperature за today), агрегирует max/min по прошедшим часам (strictly `< now_utc`).
- `pipeline.refresh_open_positions`: для каждой open paper-позиции тянет intraday, пересчитывает `refined_fair` и `refined_edge`, автоматически закрывает позицию с `resolved_early_by_observation` если `refined_fair ≤ 0.02` или `≥ 0.98`. Иначе если `refined_edge < 0` — status становится `at_risk`.
- CLI `refresh-open-positions` + HTTP `POST /api/refresh-open` + кнопка **«Мониторить позиции»** в dashboard.
- Dashboard: чип `at_risk` визуально отличается (янтарный), резолвнутые рано получают `actual_status=resolved_early_by_observation`.
- 8 новых тестов в [tests/test_near_close.py](tests/test_near_close.py). Полный suite: 54 passed.

**Когда запускать:** вручную через кнопку либо CLI в последние 2-6 часов до close, когда у большинства станций observed max/min уже отчётливо сформировался. Автоматизация (ещё один Windows task) — по желанию позднее.

### Фаза 3 (неделя 6–10)

**Near-close re-pricing.**

1. Использовать hot collector для мониторинга цен в последние 60 мин до close.
2. Добавить команду `refresh-open-positions`: для каждой open position пересчитать fair_yes с последним METAR + satellite nowcast, закрыть если edge пропал.
3. Логика edge disappeared: `abs(new_fair - old_fair) > 0.15` или `new_fair < 0.55` для BUY_NO.
4. **Метрика успеха:** снижение realized volatility на 30%, меньше катастрофических промахов.

### Фаза 4 (3–6 мес)

**Live-готовность.** Только если:
- 200+ settled paper с realized PnL > 0,
- MAE стабильно ≤ 1.5 °C,
- Drawdown не пробивал −$5 ни разу,
- Geoblock разблокирован легально (Polymarket US для DE — не доступен; ждать, не обходить).

---

## 6. Как запускать

### Dashboard на маке

```bash
cd "/Users/opipsy/Desktop/poly/detect_temperature 2"
PYTHONPATH=src python3 scripts/serve_paper_dashboard.py --host 127.0.0.1 --port 8765 --bankroll-usdc 100
```

Открыть http://127.0.0.1:8765/. Если не видны новые кнопки — удалить кешированный `artifacts/paper_dashboard.html` и перезагрузить страницу.

### CLI ручные прогоны

```bash
# Полный daily-run через CLI
PYTHONPATH=src python3 -m detect_temperature.cli scan-polymarket-weather
PYTHONPATH=src python3 -m detect_temperature.cli build-polymarket-targets
PYTHONPATH=src python3 -m detect_temperature.cli build-features --with-open-meteo \
  --manual-stations data/manual_stations.csv --stations data/stations.cache.json
PYTHONPATH=src python3 -m detect_temperature.cli predict-gbm
PYTHONPATH=src python3 -m detect_temperature.cli build-market-signals --risk-profile bankroll_100
PYTHONPATH=src python3 -m detect_temperature.cli fetch-clob-orderbooks
PYTHONPATH=src python3 -m detect_temperature.cli run-strategy-lab --risk-profile bankroll_100 \
  --orderbooks data/polymarket_orderbooks.json
PYTHONPATH=src python3 -m detect_temperature.cli open-strategy-paper-trades --risk-profile bankroll_100

# Следующий день
PYTHONPATH=src python3 -m detect_temperature.cli collect-actuals
PYTHONPATH=src python3 -m detect_temperature.cli settle-paper-trades --bankroll-usdc 100

# Post-hoc анализ
PYTHONPATH=src python3 -m detect_temperature.cli evaluate-resolved-model
```

### Windows collector (под SSH)

```bash
# С мака:
ssh -i ~/.ssh/poly_collector_ed25519 wopipsy@100.105.99.20 \
  "cd C:\\poly\\detect-temperature && .venv\\Scripts\\python.exe scripts\\windows_collector.py --mode regular"

# Проверить расписание:
ssh -i ~/.ssh/poly_collector_ed25519 wopipsy@100.105.99.20 \
  'powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName PolymarketCollectorRegular | Format-List"'

# Обновить код на Windows:
cd "/Users/opipsy/Desktop/poly/detect_temperature 2"
tar -cf - --exclude='.git' --exclude='__pycache__' --exclude='.venv' --exclude='.pytest_cache' \
  --exclude='data/history' --exclude='logs' \
  src scripts tests configs pyproject.toml | \
ssh -i ~/.ssh/poly_collector_ed25519 wopipsy@100.105.99.20 \
  "cd C:\\poly\\detect-temperature && tar -xf -"
```

---

## 7. Тесты

```bash
cd "/Users/opipsy/Desktop/poly/detect_temperature 2"
PYTHONPATH=src python3 -m pytest -q
```

Должно быть `43 passed`. Новые фичи обязательно покрывать тестами в `tests/test_*.py`.

---

## 8. Что НЕ делать

1. **Не включать BUY_YES** в `bankroll_100` до фазы 2.
2. **Не уменьшать sigma ниже 2.5** до реальной калибровки.
3. **Не коммитить данные** с API-ключами (они уже `_redacted_url` в actuals, но проверять).
4. **Не пушить в main без прогона тестов** — `pytest -q` перед коммитом.
5. **Не обходить geoblock** — ни VPN, ни прокси. Это выведет проект из зоны legal.
6. **Не трогать `artifacts/paper_runs/`** — это архив предыдущих прогонов, нужен для исследования поведения в динамике.

---

## 9. Ключевые цифры для быстрой проверки

- Активных Polymarket weather events сейчас: **~170 city daily** (5 мая snapshot).
- После фильтров `bankroll_100` остаётся: **~60 trade candidates**, **~11 robust pass**, **~8–11 selected**.
- Stake на позицию: **$0.25**.
- Максимум total exposure: **$30** из $100 (30%).
- Training holdout MAE: 0.51 °C (синтетика, не верить).
- Real resolved MAE (2026-05-05): **1.76 °C**.
- Win rate BUY_NO в старых прогонах: 60–76%.

---

## 10. Ссылки на внешние источники

- Polymarket weather page: https://polymarket.com/weather
- Polymarket docs: https://docs.polymarket.com/
- Polymarket US (CFTC regulated, not available in DE): https://docs.polymarket.us/faqs/weather-faqs
- Weather leaderboard: https://polymarket.com/leaderboard/weather/all/profit
- ColdMath (top trader): https://polymarket.com/@coldmath
- Risk investigation: [docs/weather_risk_investigation_2026-05-05.md](docs/weather_risk_investigation_2026-05-05.md)
- Model architecture: [docs/model_architecture.md](docs/model_architecture.md)
- Strategy doc: [docs/polymarket_weather_strategy.md](docs/polymarket_weather_strategy.md)

---

## 11. Контакт с юзером

- Язык: русский, простой, без перегруза.
- Не подхалимничать, не хвалить без повода. Корректировать, когда ошибается.
- Не обещать прибыль. Цифры — только с источником.
- Показывать цену действий: что делаем, что это стоит, что получаем.
- Перед серьёзными изменениями — согласовывать через AskUserQuestion.
