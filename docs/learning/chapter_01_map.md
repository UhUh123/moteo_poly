# Chapter 1 → код проекта

Карта, которая связывает концепты из `guide/chapter_01_why_money.md` с конкретными файлами/строками проекта. Нужна для главы 9 (code walkthrough), но полезна сразу — чтобы, читая главу, можно было сразу увидеть, где в коде живёт тот или иной тезис.

Это не правки, это справка. Логика модели не изменена.

---

## §1. YES/NO-акция, цена = вероятность

Где в коде мы **читаем** цену как вероятность и считаем edge:

- [signals.py:203-220](../../src/detect_temperature/signals.py#L203-L220) — строки `yes_buy_price = _first_float(...)`, `yes_edge = fair_yes - yes_buy_price - yes_fee`. Здесь код говорит буквально: наше `fair_yes` минус цена минус комиссия = edge.
- [signals.py:326-328](../../src/detect_temperature/signals.py#L326-L328) — `fee_per_share(price, fee_rate)` = `fee_rate * price * (1 - price)`. Это динамическая комиссия Polymarket: пик в 50/50, минимум у краёв. Это та самая «первая комиссия», про которую говорит глава.

Где мы **пишем** наше мнение как вероятность:

- [signals.py:313-325](../../src/detect_temperature/signals.py#L313-L325) — `normal_interval_probability(mean, sigma, lower, upper)`. Вход: прогноз средней температуры и sigma. Выход: `P(lower ≤ T < upper)` по нормальному распределению.

## §2-3. YES + NO ≈ $1, спред, арбитраж

Мы не арбитражим, но мы **измеряем** спред:

- [signals.py:474-490](../../src/detect_temperature/signals.py#L474-L490) — `_market_health_block`. Если `spread > max_spread`, сигнал становится `NO_TRADE`. В профиле `bankroll_100` порог `max_spread = 0.03` ([risk_profiles.py:11](../../src/detect_temperature/risk_profiles.py#L11)).
- Вместе с комиссией в `fee_per_share` это всё «трение рынка», про которое пишет глава 1 §5 и §6.

## §4. Zero-sum, откуда берутся деньги

В коде этого **не видно напрямую** — он про edge, а не про то, с чьего кошелька деньги. Но:

- В профиле `bankroll_100` стоит `max_stake_usdc = 0.25` ([risk_profiles.py:30](../../src/detect_temperature/risk_profiles.py#L30)). Это признание §5 главы: средний retail проигрывает, поэтому размер ставки такой, чтобы хватило на ~400 сделок прежде, чем банк обнулится.
- `drawdown_abort_usdc: -10.0` в том же профиле — явный kill-switch. Эта защита потому и нужна, что мы признаём: edge может оказаться отрицательным.

## §5. Почему большинство проигрывает — как это отражено в риск-профиле

Четыре причины из §5 и их контр-меры в коде:

| Причина | Контр-мера в проекте |
|---|---|
| Zero-sum, средний игрок в минусе | Маленький стейк $0.25, drawdown −$10 |
| Информационная асимметрия (боты с NOAA feed) | **Нет контр-меры.** Это и есть риск, про который глава предупреждает |
| Эмоциональная торговля | Полная автоматизация, нет ручных ставок |
| Спред + комиссии съедают даже нулевой edge | `min_edge = 0.05`, `max_spread = 0.03` ([risk_profiles.py:9-11](../../src/detect_temperature/risk_profiles.py#L9-L11)); `robust_min_edge = 0.10` в Strategy Lab ([risk_profiles.py:42](../../src/detect_temperature/risk_profiles.py#L42)) — требуем, чтобы edge пережил стресс-сценарии с запасом ≥10 центов |

## §6. Специфика weather-рынков

- **Ведра:** Polymarket ставит ведра шириной 1°F (США) или 1°C (Европа/Азия). Мы это парсим в [signals.py:250-310](../../src/detect_temperature/signals.py#L250-L310) — `parse_temperature_interval()` достаёт `lower/upper` из формулировок "be 17°C", "be 60°F or higher", "58-59°F", etc.
- **Объективный резолв:** [sources/actuals.py](../../src/detect_temperature/sources/actuals.py) — три провайдера по source_domain: Wunderground для большинства, HKO для Гонконга, Synoptic/weather.gov для ряда US-станций.
- **Station verifier:** [station_verifier.py](../../src/detect_temperature/station_verifier.py) проверяет, что мы знаем, по какой именно станции резолвится рынок. Если нет — `NO_TRADE` в [signals.py:183-188](../../src/detect_temperature/signals.py#L183-L188).

## §7. Заявленный edge проекта — и его слабости

### Слабость 1: Normal CDF игнорирует толстые хвосты

Самая системная проблема, которую поднимает глава. Код:

- [signals.py:313-325](../../src/detect_temperature/signals.py#L313-L325) — `normal_interval_probability` использует чистую гауссиану.
- [signals.py:563-565](../../src/detect_temperature/signals.py#L563-L565) — `_normal_cdf(x) = 0.5 * (1.0 + erf(x / sqrt(2)))`. Это математически корректная нормальная CDF, но температурная ошибка **не** нормальна.

Что это значит практически. У нас есть калибрация sigma per station ([data/station_calibration.csv](../../data/station_calibration.csv), 51 станция). Но в резолв-метриках уже виден Сиэтл на −6.3°F и Шэньчжэнь на +4.8°C. Для нормального распределения с sigma = 1.5 °C такое событие имеет вероятность меньше 0.1%. А они случаются регулярно. Значит, наши `fair_yes` на хвостах недооценены, и модель системно переоценивает вероятность центрального ведра.

Что с этим делать — материал главы 2 (fat tails) и главы 5 (calibration). Сейчас не трогаем.

### Слабость 2: Источник данных совпадает с рыночным

- [sources/open_meteo.py](../../src/detect_temperature/sources/open_meteo.py) — единственный forecast-provider в pipeline. Open-Meteo агрегирует GFS + ECMWF, то есть те же NWP-модели, которые уже учтены в рыночной цене.
- [models/gbm.py](../../src/detect_temperature/models/gbm.py) — GBM учится на `(station, date) → observed` парах. Это bias-corrector над raw-прогнозом.
- Вывод, который делает глава: если Open-Meteo ≈ то, что смотрят трейдеры на Polymarket, то GBM-коррекция даёт edge только если **наш bias-corrector** специфичнее/точнее их. На 50 реальных событиях — улучшение 2%, в пределах шума.

Проверенный мной источник (Kalshi-эксперимент из `guide/sources.md`): автор признал, что 100% win-rate за 14 дней — artefact, долгосрок ожидаемо 60-70%. Реальный edge у него сидел в **двух месяцах локальной станционной истории**, не в модели GFS.

### Слабость 3: Корреляция ставок

- [risk_profiles.py:36-39](../../src/detect_temperature/risk_profiles.py#L36-L39) уже имеет лимиты: `max_city_positions=2`, `max_date_exposure_pct=0.03`, `max_extreme_exposure_pct=0.10`. Но это **числовые** лимиты, они не учитывают синоптику.
- Реальный сценарий: холодный фронт 12 мая двинул Warsaw, Madrid, Paris — все три ушли в BUY_NO проигрыш одновременно. Лимиты сработали (максимум $3 на дату), но ожидаемая «диверсификация 29 позиций» оказалась реально 3-4 независимыми ставками, не 29.
- Где в коде можно было бы контролировать: geographic region field в `_market_context` ([strategy_lab.py:1098-1114](../../src/detect_temperature/strategy_lab.py#L1098-L1114)). Пока такого поля нет — материал будущих глав.

### Слабость 4: Intraday-наблюдения ≠ METAR

Интересное место — **инфраструктура уже есть, но используется не там, где могла бы дать edge**:

- [sources/aviation_weather.py:76-113](../../src/detect_temperature/sources/aviation_weather.py#L76-L113) — `AviationWeatherMetarProvider.latest(station_id)` возвращает свежий METAR-репорт с aviationweather.gov. Доступен **бесплатно, без API-ключа**, пересчитывается каждые 5 минут реального времени.
- [cli.py:297](../../src/detect_temperature/cli.py#L297) — используется только в `build-features` с флагом `--with-metar`, и только чтобы положить latest temp в feature row. Это не intraday трекинг.
- [near_close.py:77-122](../../src/detect_temperature/near_close.py#L77-L122) — `fetch_intraday_max_min` использует **Open-Meteo hourly forecast** для прошедших часов. То есть наши «наблюдения» в near-close — это **прогноз задним числом**, а не настоящие METAR-наблюдения.

Это та самая слабость, которую глава называет «слабой копией настоящего сигнала». Конкретно: у нас уже есть провайдер, он подключён к CLI, но в near-close цикл идёт через Open-Meteo. Концептуально несложно заменить источник в `fetch_intraday_max_min` на METAR, но это **стратегическое** решение, которое влияет на edge. Материал главы 6 (погодные модели) или главы 9 (code walkthrough).

### Слабость 5: Нет стратегии «дешёвых хвостов»

- [risk_profiles.py:5-13](../../src/detect_temperature/risk_profiles.py#L5-L13) — `min_no_probability=0.70`, `allow_buy_yes=False`. Мы **сознательно** шортим центр (BUY_NO по 60-80¢).
- Стратегия gopfan2-inspired («купи дёшево — продай на 45¢») требует BUY_YES на хвостах. Мы это явно выключили из-за 0-3% win-rate в прошлых прогонах.
- Глава ставит вопрос: возможно, отключение было преждевременным? Возможно, win-rate 0-3% был следствием Normal CDF игнорирующей хвосты (см. слабость 1) — он переоценивал наши YES, а реальная вероятность была ниже. После честного учёта хвостов YES мог бы стать играемым. Это чистая гипотеза, проверка — матерал глав 4-5.

## §8. Реальные источники edge у профессионалов

Три пункта из главы и их статус у нас:

| Пункт профи | У нас |
|---|---|
| Локальный bias станции из 2+ месяцев наблюдений | ЕСТЬ rolling_bias per station в `data/station_calibration.csv`, но только для общего bias, не условного (ветер, давление) |
| Intraday METAR наблюдения | **Провайдер есть, но не используется в near-close** (см. слабость 4) |
| Покупка дешёвых хвостов | Явно отключено (см. слабость 5) |

## §9. Честный прогноз — сверка с realtime

Цифры главы на момент написания:
- 8 реально settled, −$0.81 PnL, 50% win-rate.

Актуальные цифры на 13 мая 16:10 UTC (из `status/health.json` на Windows):
- 17 settled, +$0.03 PnL, 64.7% win-rate (11 won / 6 lost).
- Выигрыши по ~$0.13 каждый (вход 60-70¢), потери по −$0.25 каждая (полный stake).

Это **внутри шума**, как и прогноз главы. Математика (64.7% × $0.13) − (35.3% × $0.25) ≈ −$0.005 на сделку — buffered by small sample noise. На 200 сделках это будет −$1, на 500 — уже виден тренд.

---

## Что не нужно делать сейчас (и почему)

Глава вскрывает 5 слабостей. Соблазн — «давайте пофиксим». Почему я этого не делаю:

1. **Fat tails (Normal → Student-t / ensemble).** Требует переоценки всего signal pipeline. Глава 2.
2. **Собственный источник данных (ECMWF напрямую).** Мы не сможем торговать быстрее бота с прямым NOAA feed. Edge не в скорости, edge в понимании, что **мы делаем не так**.
3. **Regional correlation limits.** Нужна геоклассификация станций, потом — переоценка лимитов Strategy Lab. Глава 7 (архитектура).
4. **METAR в near-close.** Технически один день работы, **но** это стратегическое изменение: меняет природу сигнала, который смотрит near_close.refresh. Материал главы 6.
5. **Cheap tails (BUY_YES на хвостах).** Нужна перекалибровка sigma под не-нормальные хвосты. Иначе включишь YES и заплатишь за слабость 1. Глава 4 (sizing) + глава 5 (calibration).

## Что можно делать прямо сейчас

- Продолжать копить реальные resolved сделки. Каждая добавляет сигнал в калибровку.
- Читать главу 2. К следующей главе вернёмся с ответом: что делать с хвостами.
- Смотреть `status/health.json` раз в день. Если `drawdown_triggered=true` — остановиться и разобраться.

---

## Источники, использованные при составлении карты

- `guide/chapter_01_why_money.md` — глава
- `guide/konspekty/konspekt_01_why_money.md` — конспект с цифрами и упражнением
- `guide/sources.md` — список внешних источников
- Первичная проверка: [weatherstationadvisor Kalshi experiment](https://weatherstationadvisor.com/home-weather-station-prediction-market/) — все цифры главы (станционный bias +1.1°F под южным ветром, 5/5 divergent picks, 14/14 hit-rate, 60-70% long-run ожидание) подтверждены.
- Cross-references в код делались против master на commit `695731e`.
