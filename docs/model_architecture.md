# Архитектура модели прогноза температуры

## Выжимка из статьи

Для нашей задачи важны несколько пунктов:

- целевой горизонт короткий, около 24 часов, поэтому базовый погодный прогноз уже несёт большую часть сигнала;
- для аэропортов критичны station-level наблюдения METAR/ASOS, потому что рынки часто резолвятся по конкретной станции;
- для городов нужен учет локальных эффектов: высота, тип поверхности, плотность застройки, близость воды;
- рекомендуемый практический вариант: NWP/AI weather baseline плюс XGBoost/LightGBM-подобная ML-коррекция смещений;
- качество нужно мерить не только RMSE/MAE, но и bias, особенно отдельно для `highest` и `lowest`.

## Принятая структура

```text
Polymarket markets
  -> normalized targets
  -> station resolver
  -> forecast provider
  -> feature builder
  -> baseline forecast
  -> GBM bias correction
  -> calibrated point prediction
```

## Целевая запись

Каждый рынок превращается в одну задачу:

- `target_date`: календарная дата рынка;
- `target_extreme`: `max` или `min`;
- `target_unit`: единицы resolution source;
- `city`: город из названия рынка;
- `station_id`: код станции из source URL, чаще ICAO/IATA в Wunderground;
- `resolution_source_url`: источник финального значения.

Фактическая метка для обучения должна быть сохранена как `observed_temp_c` после финализации рынка.

## Слои данных

- `markets.py`: превращает `weather_markets.json` в нормализованные targets.
- `sources/aviation_weather.py`: координаты станций и METAR-наблюдения.
- `sources/manual.py`: ручные точки для источников без ICAO-кода в URL, например `HKO`.
- `sources/open_meteo.py`: базовый forecast/NWP слой для точки и даты.
- `features.py`: объединяет target, station metadata, forecast и observations в одну строку признаков.
- `models/baseline.py`: простой baseline, который берет daily max/min из прогноза.
- `models/gbm.py`: ML-коррекция смещения на исторических labels.
- `evaluation.py`: time-ordered holdout и метрики MAE/RMSE/bias плюс доля попаданий в 1/2/3 C.
- `sources/actuals.py`: сбор финальных high/low labels из Wunderground/Weather.com, HKO и weather.gov/Synoptic.
- `polymarket.py`: read-only live snapshot weather markets, token ids, prices, spreads и geoblock check.
- `signals.py`: paper-only перевод прогноза в вероятности ranges и расчет edge после taker fee.

## Почему так

Статья прямо подталкивает к гибриду. Чистая модель по координате слишком бедная: она не знает текущей синоптики. Чистый NWP/Open-Meteo прогноз игнорирует систематические локальные ошибки станции. Поэтому первый рабочий слой должен быть baseline forecast, а первая обучаемая модель должна предсказывать поправку к нему.

## Следующие данные, которые нужно накопить

- координаты и elevation для всех `station_id`;
- daily/hourly прогнозы на момент до резолва рынка;
- свежие METAR-наблюдения перед целевой датой;
- финальные high/low из Wunderground или другого resolution source;
- rolling bias по станции, региону, месяцу и типу экстремума.

## Текущий train/evaluate цикл

Исторический датасет хранит две строки на station-date: одну для `max`, одну для `min`.
`train-gbm` сначала обучает модель на ранней части дат и оценивает на последней части дат, затем переобучает финальную модель на всех доступных строках и сохраняет ее в `artifacts/models/gbm.joblib`.

Основная метрика для простого сравнения — MAE в градусах C. Процент качества здесь считается не как абстрактная accuracy, а как доля прогнозов, попавших в коридор ошибки: `within_1c_pct`, `within_2c_pct`, `within_3c_pct`.

## Paper trading слой

`scan-polymarket-weather` не выставляет ордера. Он читает публичную weather страницу Polymarket, сохраняет все temperature binary markets в `data/polymarket_weather_markets.csv` и отдельно пишет `data/polymarket_geoblock.json`.

`build-market-signals` соединяет market snapshot с `artifacts/predictions_gbm.csv`, парсит диапазоны вроде `24°C`, `60°F or higher`, `58-59°F`, считает normal probability around `corrected_prediction_c` и пишет paper edge в `artifacts/market_signals.csv`.

`open-paper-trades` превращает лучшие paper signals в виртуальный портфель с bankroll, stake, shares, expected PnL и HTML-интерфейсом `artifacts/paper_dashboard.html`.

`open-strategy-paper-trades` делает то же самое для `artifacts/strategy_portfolio_v2.csv`: берет только Strategy Lab selected positions, сохраняет их в основной paper portfolio и добавляет в dashboard поля entry mode, execution quality, maker preference и robust reason.

`settle-paper-trades` пересчитывает этот портфель по `data/actuals.csv`, когда actuals получают `status=ok`, и показывает realized PnL.

`serve-paper-dashboard` запускает локальный HTTP server для панели. Через него кнопка `Refresh actuals & PnL` может вызвать backend: собрать actuals, оставить нерезолвнутые рынки в `open` и пересчитать realized PnL для resolved позиций.

Пока `data/polymarket_geoblock.json` возвращает `blocked=true`, любые live orders должны быть выключены.
