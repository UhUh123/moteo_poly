# Chapter 2 → код проекта

Карта понятий главы 2 (`guide/chapter_02_probability.md`) и места, где они живут или должны жить в коде. Не правки, а справка.

Принцип того, что мы делаем сейчас и что не делаем: глава поднимает важные проблемы (vig, fat tails), но **сама по себе не даёт инструмента их пофиксить**. Mixture-распределения, эмпирические кривые, нормализация рыночных вероятностей — всё это материал главы 5. Поэтому в этом коммите я добавил один read-only диагностический скрипт ([scripts/measure_residual_distribution.py](../../scripts/measure_residual_distribution.py)) и эту карту. Логику сигналов не трогал.

---

## §1. Вероятность как число от 0 до 1

В коде вероятности всегда `float ∈ [0, 1]`. Видно явно:

- `fair_yes_probability`, `fair_no_probability` в [signals.py:148-150](../../src/detect_temperature/signals.py#L148-L150) — десятичные дроби.
- `_normal_cdf(x)` в [signals.py:563-565](../../src/detect_temperature/signals.py#L563-L565) — возвращает значение `[0, 1]`.
- В dashboard эти числа умножаются на 100 для рендера: `_percent_from_prob` в [paper.py:1130-1133](../../src/detect_temperature/paper.py#L1130-L1133).

## §2. Frequentist vs Bayesian

Обе точки зрения смешаны в нашей логике, как и говорит глава:

- **Частотная** часть: `data/training_real.csv` — 124 440 пар (forecast, observed) за 2023–2026. Из них считается per-station MAE и bias в [scripts/build_station_calibration.py](../../scripts/build_station_calibration.py).
- **Байесовская** часть: для конкретного завтрашнего дня в Сеуле модель не имеет «частоты», она использует калиброванные параметры (sigma, bias) как априорные, а Open-Meteo прогноз как точечное среднее, и через `normal_interval_probability` ([signals.py:313-325](../../src/detect_temperature/signals.py#L313-L325)) превращает это в распределение по будущему конкретному дню.

## §3. Сумма вероятностей по взаимоисключающим ведрам = 1

В коде это **не проверяется** для рыночных цен. Каждое ведро в [signals.py:60-110](../../src/detect_temperature/signals.py#L60-L110) обрабатывается как независимый рынок. Свойство «сумма по event_slug ≈ 1» нигде не верифицируется.

Это нормально для нашей оценки модели (она автоматически даёт consistent распределение через нормальную CDF), но это **проблема для рыночных цен**: см. §4.

## §4. Vig — где мы его не учитываем

**Это слабое место.** Глава прямо говорит: «прежде чем кричать "нашёл edge", вычти vig». Сейчас мы этого не делаем.

Где edge сравнивается с ценой:

- [signals.py:219-220](../../src/detect_temperature/signals.py#L219-L220):
  ```python
  yes_edge = fair_yes - yes_buy_price - (yes_fee or 0.0)
  no_edge  = fair_no  - no_buy_price  - (no_fee or 0.0)
  ```
  Здесь `yes_buy_price` берётся прямо из `best_ask` рынка ([signals.py:214](../../src/detect_temperature/signals.py#L214)). **Ни один шаг не нормирует цены так, чтобы сумма по event_slug была ровно 1.**

- В [signals.py:330-385](../../src/detect_temperature/signals.py#L330-L385) есть `_apply_event_context`, который группирует строки по `event_slug` и считает `visible_top_bucket_probability`. Но **наши** fair-вероятности там не нормируются, и **рыночные** тоже.

Что это значит на практике. Если Polymarket даёт overround +5% (типичный vig для liquid weather buckets), наши edge-цифры **систематически завышены на 5 центов**. `min_edge=0.05` в `bankroll_100` ([risk_profiles.py:9](../../src/detect_temperature/risk_profiles.py#L9)) после vig фактически = 0%. Это объясняет часть «PnL около нуля» на 17 settled.

Конкретный фикс — это материал главы 5 (там объяснят, как нормализовать перед сравнением). Сейчас фиксирую: **наш `min_edge` несравним с `min_edge` из академических работ — у них уже vig вычтен, у нас нет**.

## §5. Условная вероятность

`refresh_open_positions` именно про неё:

- [near_close.py:60-122](../../src/detect_temperature/near_close.py#L60-L122) — `refined_bucket_probability(NearCloseInput)`. Точная формула P(bucket | observed_so_far, hours_remaining).
- Логика «если наблюдённый max уже вышел за upper — bucket impossible»: [near_close.py:84-87](../../src/detect_temperature/near_close.py#L84-L87). Это математически корректное условное выражение.
- Shrink sigma: [near_close.py:73-80](../../src/detect_temperature/near_close.py#L73-L80) — sigma уменьшается с `sqrt(T_remaining/24)`. Это эвристика, не вывод.

Слабое место главы 2 §5.5: «наблюдения» приходят из Open-Meteo hourly forecast, а не METAR.

- [near_close.py:96-122](../../src/detect_temperature/near_close.py#L96-L122) — `fetch_intraday_max_min` ходит в `https://api.open-meteo.com/v1/forecast?hourly=temperature_2m`.
- Бесплатный METAR-провайдер уже есть в проекте: [sources/aviation_weather.py:76-113](../../src/detect_temperature/sources/aviation_weather.py#L76-L113). В цепочку near-close он не подключён.

Конкретный гэп без необходимости трогать математику: **в `fetch_intraday_max_min` можно сделать опциональный параметр `prefer_metar=False`**. Когда True — пытаемся вытащить max/min из METAR-репортов через `AviationWeatherMetarProvider`, при ошибке падаем на Open-Meteo. Это локальный, измеримый, проверяемый фикс. Делать **сейчас** не буду — это касается стратегического сигнала, и мы договорились, что такие изменения после главы 6 (про NWP). Записал как кандидат в §15 ниже.

## §6. Точечный прогноз vs распределение

В коде граница чёткая:

- **Точка** (μ): `corrected_prediction_c` в [predictions_gbm.csv](../../artifacts/predictions_gbm.csv) — это точечный прогноз. Это «куда модель ткнёт».
- **Распределение** строится на лету в [signals.py:198-211](../../src/detect_temperature/signals.py#L198-L211): берётся μ = `corrected_prediction_c`, σ = `sigma_for_station(...)`, и через нормальную CDF получается P(lower ≤ T < upper).

Распределение **не сохраняется в файлах**. Оно живёт ровно один тик внутри `build_market_signal`. Это чисто прагматическое решение, но стоит держать в голове: если мы захотим заменить Normal на эмпирическое — единственное место, которое надо поменять, это `normal_interval_probability` ([signals.py:313-325](../../src/detect_temperature/signals.py#L313-L325)) и параллельная функция в [near_close.py](../../src/detect_temperature/near_close.py#L46-L57).

## §7-8. Гауссиана и толстые хвосты — **проверено эмпирически на наших данных**

Глава делает количественное заявление: «по нормальному распределению с σ = 1.5 °C промах 6.54 °C должен случаться раз в 1 из 4 миллионов случаев. А у тебя он один раз на 50.»

**Я проверил это сам**, потому что цифра подозрительно круглая. Результат:

- При σ = 1.5 °C, гауссиана даёт `P(|err| > 6.54) = 1.30e-5`, то есть **1 в ~77 000**, не 1 в 4 миллиона. Глава завысила гауссовскую редкость в **26 раз**.
- Качественный вывод по-прежнему правильный: 6.54 °C miss при σ = 1.5 — это всё равно событие, которое гауссиана считает практически невозможным, а в реальности оно случается чаще.

Чтобы получить **честные** цифры на наших данных, я написал [scripts/measure_residual_distribution.py](../../scripts/measure_residual_distribution.py) — read-only diagnostic. Запуск против `data/training_real.csv` (124 440 пар, 51 станция, 2023–2026):

```
mean (bias)     = -0.182 C
sigma           = 1.079 C
excess kurtosis = +11.90    (Gaussian = 0)

|err| >     observed         Gaussian            ratio
 3*sigma   1 in 47          1 in 370             7.8x
 4*sigma   1 in 128         1 in 15,787          123x
 5*sigma   1 in 280         1 in 1,744,278       6,224x
 6*sigma   1 in 534         1 in 506,797,346     948,921x
```

Это и есть «толстые хвосты» в эмпирике. На 5σ реальность в 6 тысяч раз чаще, чем гауссиана. На 6σ — почти миллион раз.

**Дополнительный нюанс, которого нет в главе:**

```
By target_extreme = 'max':  skewness = +1.38, excess kurtosis = 15.7
By target_extreme = 'min':  skewness = -1.12, excess kurtosis =  6.2
```

То есть распределение **асимметричное**: для max-температур толстый хвост *сверху* (наблюдённый максимум часто оказывается выше прогноза), для min-температур толстый хвост *снизу* (наблюдённый минимум часто холоднее прогноза). Это согласуется с физикой главы §9: тёплая адвекция / heat dome для max, холодная адвекция / radiation cooling для min — оба явления режимные и однонаправленные.

Гауссиана этого асимметричного fat tail вообще не моделирует. Mixture или Student-t в одиночку тоже не идеально — нужна skew-распределение или раздельные параметры для левого/правого хвоста. Это уже глубокая математика, материал главы 5.

## §9. Почему именно у погоды толстые хвосты

В коде ничего не моделирует frontal regime / sea breeze / advection. У нас есть фичи `target_day_of_year_sin/cos`, `target_month`, `target_is_weekend`, `forecast_temp_spread_c` ([gbm.py](../../src/detect_temperature/models/gbm.py#L11-L31)). Все они — статичные. Никакого синоптического режима.

GBM это умеет _неявно_, но только если учить на огромном корпусе с явными фронтовыми днями — а они в данных не размечены. Поэтому GBM ловит общий паттерн, но крайние режимы пропускает. Эмпирика выше это подтверждает: GBM даёт MAE ≈ 0.45 °C, но excess kurtosis 11.9 остаётся.

## §10. Что делать (по плану)

Глава предлагает три варианта (эмпирическое распределение / mixture / покупка хвостов). По договорённости из плана — не делаем сейчас. Но фиксирую кандидатов:

| Кандидат | Где менять | Цена изменения | Когда |
|---|---|---|---|
| Empirical CDF вместо Normal CDF | [signals.py:313-325](../../src/detect_temperature/signals.py#L313-L325), [near_close.py:46-57](../../src/detect_temperature/near_close.py#L46-L57) | Низкая (один файл, есть тесты) | Глава 5 |
| Vig normalization перед `min_edge` | [signals.py:330-385](../../src/detect_temperature/signals.py#L330-L385) `_apply_event_context` | Средняя (групп. по event_slug) | Глава 5 |
| METAR в `fetch_intraday_max_min` | [near_close.py:96-122](../../src/detect_temperature/near_close.py#L96-L122) | Низкая, инфра есть | Глава 6 |
| Skew-aware sigma per side (max/min) | [signals.py:39-50](../../src/detect_temperature/signals.py#L39-L50) `sigma_for_station` уже есть на станцию, но не на side | Низкая | Глава 5 |
| BUY_YES re-enable strategy на хвостах | [risk_profiles.py:13](../../src/detect_temperature/risk_profiles.py#L13) | Высокая (стратегия) | Глава 4 + калибровка |

## §11. Связь с проектом — поправка из конспекта

Конспект [§9](../../../guide/konspekty/konspekt_02_probability.md) делает важную поправку: «низкий win-rate BUY_YES — это, скорее всего, **overfitting**, не fat tails». Это два разных механизма:

- **Overfitting** ломает центральную оценку — модель занижает sigma на trainset, в production sigma подскакивает.
- **Fat tails** ломают **края** — даже честно оценив sigma, гауссиана даёт слишком тонкий хвост.

Оба явления реальны на наших данных. Доказательства:
- Overfitting косвенно: training holdout MAE 0.45, реальный Polymarket MAE ~1.20.
- Fat tails прямо: excess kurtosis 11.9 на 124k residuals (см. §7-8 выше).

## §12. Что в этом коммите

1. [scripts/measure_residual_distribution.py](../../scripts/measure_residual_distribution.py) — диагностика (read-only)
2. [docs/learning/chapter_02_map.md](.) — этот документ

Никакой production-логики не тронуто. 65 тестов как были.

---

## Источники, которыми я **проверил** утверждения главы

- Math `1 / 4 million` claim → расчёт через `math.erf`. Реальное значение `1 / 77 000` (см. §7-8 выше). Глава завысила в 26x; общий вывод правильный.
- Empirical kurtosis → собственный диагностический скрипт против 124k наших данных. Excess = +11.9.
- Asymmetric tails (max vs min) → собственный диагностический. Skew +1.38 для max, -1.12 для min.
- Kalshi-эксперимент (источник из chapter 1) уже подтверждался ранее — никаких новых проверок не понадобилось.

## Чего **не** делал

- Не искал академические Q-Q plots (web search возвращал круги по тем же EMOS-статьям; решил, что собственная эмпирика на 124k наших данных сильнее, чем чужая на других данных).
- Не трогал `signals.py`, `near_close.py`, `risk_profiles.py`. Глава 2 этого не требует.
