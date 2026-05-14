# Chapter 6 → код проекта

Карта понятий главы 6 (`guide/chapter_06_weather.md`) и места в проекте.

Глава 6 — самая длинная и самая «физическая». Она переводит понимание из «модель прогнозирует число» в «прогноз — это решение уравнений на сетке, которое систематически расходится с точкой резолва». Главный практический вывод главы: **наш проект сидит на одном источнике (Open-Meteo) без ensemble spread и без METAR — это структурный потолок edge'а**.

В этом коммите я добавил один важный диагностический скрипт — climatology baseline. Это вопрос из главы 5 §9.5 («какой baseline?»), на который я раньше не отвечал, и глава 6 §10.2 повторяет его с большей силой.

---

## §1-2. Что такое прогноз и три класса ошибок

В коде это видно так:
- [sources/open_meteo.py](../../src/detect_temperature/sources/open_meteo.py) — один HTTP-запрос, одно число `temperature_2m_max`. Уравнения Навье-Стокса от нас спрятаны.
- [models/gbm.py](../../src/detect_temperature/models/gbm.py) пытается выучить «ошибку модели» — то есть классы 2.2 (физика) и 2.3 (точка резолва). Класс 2.1 (хаос) — нерешаем, мы можем только увеличивать σ при дальнем lead time.

Доказательство, что это работает: см. ниже §10 — GBM убирает 14.5% MAE сверх Open-Meteo. Это материал классов 2.2 и 2.3.

## §3. Главные модели мира и расхождения

В коде у нас **только** Open-Meteo. Open-Meteo — это re-aggregator GFS + ECMWF Open Data + ICON + HRRR + ARPEGE. То есть **физически** под капотом несколько моделей, но Open-Meteo нам отдаёт **одно число**. Расхождения скрыты.

Для нашего проекта это значит:
- Нет independent cross-check между моделями
- Нет «сильного» vs «слабого» сигнала по согласию моделей
- Нет signal-of-uncertainty из multi-model spread

Чтобы это получить — нужен прямой доступ к GFS/ECMWF/HRRR через NOMADS + GRIB-парсер. Глава честно говорит: это месяцы работы.

## §4. Ансамбли и ensemble spread

Глава §4.3 утверждает: **Open-Meteo не даёт ensemble spread**. Это правда — endpoint `/v1/forecast` отдаёт детерминированные `temperature_2m_max/min`, а не percentiles. У них есть отдельный endpoint `/v1/ensemble`, но он отдаёт raw членов ensemble без пост-обработки и не интегрирован в наш pipeline.

В коде это значит:
- [signals.py:202-211](../../src/detect_temperature/signals.py#L202-L211) использует `effective_sigma_c` из per-station calibration. Это **константная** σ для станции на любой день.
- Реальная неопределённость атмосферы для конкретного дня (turbulent vs stable régime) **полностью игнорируется**.

Это и есть та проблема, которую chapter 5 §6.3 reliability diagram показал эмпирически: PIT histogram сильно отклоняется от равномерного, потому что σ не адаптивная.

Это не баг кода. Это **ограничение источника данных**.

## §5. Open-Meteo — что он даёт и что нет

Я перепроверил §5.3 чтения по [`sources/open_meteo.py`](../../src/detect_temperature/sources/open_meteo.py):

```python
# fetch_forecast возвращает только daily max/min/mean + hourly temperature_2m
# Никаких ensemble percentiles, никаких spread
```

Это ровно то, что глава описывает. У главы есть гипотеза в §5.3:

> Архивные «исторические прогнозы» иногда **перепосчитаны задним числом** — это потенциальный leakage в обучении.

Это **нельзя проверить** из публичного API Open-Meteo — они не помечают, какие прогнозы были live, а какие reanalysis-corrected. Глава 5 §4.2(a) фиксирует это как канал утечки. Я остаюсь с тем заключением — мы не можем доказать, что training_real.csv не имеет этой утечки.

**Однако**: если бы leakage был катастрофичный, мы бы увидели огромный gap между in-time MAE и OOT MAE. Мы видим **0.018 °C gap** (см. chapter_05_map). Это означает leakage либо отсутствует, либо одинаково присутствует и в train, и в OOT — что эффективно никакого leakage в практическом смысле.

## §6. METAR — истина по которой резолвится рынок

Глава §6.6 называет это **самым важным** изменением, которое можно сделать в коде:

> Замена `fetch_intraday_max_min` на чтение METAR — это самое важное изменение, которое можно сделать в коде.

Что в проекте сейчас:
- `AviationWeatherMetarProvider` существует ([sources/aviation_weather.py:76-113](../../src/detect_temperature/sources/aviation_weather.py#L76-L113))
- Используется только в `build-features --with-metar` для **исторических** features
- В [near_close.py:77-122](../../src/detect_temperature/near_close.py#L77-L122) `fetch_intraday_max_min` использует **Open-Meteo hourly forecast**, а не METAR

Это та же находка, что я зафиксировал в [chapter_01_map.md §7 слабость 4](chapter_01_map.md). Глава 6 даёт ей физическое обоснование.

**Конкретный recipe для будущей правки:**
1. Добавить `fetch_intraday_metar(icao, target_date, now_utc) -> ObservedMaxMin` в `near_close.py`
2. Использовать `AviationWeatherMetarProvider.latest()` несколько раз за окно (но не публиковать API endpoint, в существующем провайдере есть только `latest`, нужна функция `history(icao, since)`)
3. Заменить `fetch_intraday_max_min` или сделать gradient (METAR где доступен, Open-Meteo как fallback)

Это, вероятно, день работы. Не делаю сейчас — материал главы 7 (architecture) или прямой работы.

## §7. Локальный bias — есть в проекте, но упрощённый

Глава §7.6 говорит: «Phase 2c: per-station rolling bias — это начало». Что в коде:
- [data/station_calibration.csv](../../data/station_calibration.csv) — per-station MAE и bias
- [scripts/build_station_calibration.py](../../scripts/build_station_calibration.py) — считает на скользящем окне всего training set'а

Что **не** делается:
- Bias **не разбит** по ветровым режимам (нужна wind direction в training_real, мы её не собираем)
- Bias **не разбит** по сезонам
- Используется только rolling MAE для σ, не conditional bias correction

Это конкретный вектор улучшения, оценка из главы — «5-10× больше вычислений». Не делаю.

## §8. Edge близко к закрытию

Глава §8.5 чётко перечисляет конфликт с проектом:

| Глава предлагает | Что у нас сейчас |
|---|---|
| Игнорировать утренний рынок | Открываем в **22:00 UTC** — за 8-10ч до большинства close'ов |
| Перевычисление каждые 5-10 минут в последние 4 часа | `near_close_refresh` — **8 точечных** запусков 01:00-04:30 UTC |
| METAR как источник | Open-Meteo hourly forecast |

Это структурное несоответствие нашей стратегии тому, что глава называет gopfan2-стилем. Не баг, но и не оптимально для edge.

## §9. DST и часовые пояса

Глава §9 подсвечивает реальную ловушку. Посмотрел в код:

- [markets.py](../../src/detect_temperature/markets.py) парсит даты резолва из event_slug (`on-may-14-2026`)
- Не вижу проверки часового пояса для этих дат
- Polymarket резолвит по местному времени станции (обычно), мы парсим как UTC дату

Это потенциальный риск. Если рынок Сеула указан «highest temperature on May 14», и Polymarket интерпретирует это как 14 мая по KST (UTC+9), а мы считаем это как 14 мая UTC — мы можем смотреть на **другой 24-часовой период**. Эффект: на rare days, когда Tmax происходит около границы суток, наш прогноз и его резолв смотрят на разные дни.

Не уверен, что это реально происходит, но **зафиксировано как риск для будущей проверки**. Глава этого добавила в моё внимание.

## §10. Climatology baseline — диагностический результат этой сессии

Глава §10.2 ставит вопрос напрямую:

> Какой EV дала бы простая стратегия «ставить по климатологии»? Если твоя сложная стратегия не побивает климатологию — она бесполезна.

Я добавил [scripts/measure_climatology_baseline.py](../../scripts/measure_climatology_baseline.py). Запускает на тех же OOT 30 днях, что и chapter 5 reliability:

```
=== OOT comparison on 3,060 rows (30-day holdout) ===
  Climatology (no model)         MAE=2.560 C  within-1C=28.5%  within-2C=50.2%
  Open-Meteo raw                 MAE=0.553 C  within-1C=81.5%  within-2C=94.2%
  GBM (production-equivalent)    MAE=0.473 C  within-1C=83.0%  within-2C=96.2%

  Climatology MAE = 2.56 C - the floor any honest model must beat.
  Open-Meteo MAE  = 0.55 C - what GBM has to improve on.
  GBM MAE         = 0.47 C

  Open-Meteo over climatology: +2.007 C  (+78.4%)
  GBM over climatology:        +2.087 C  (+81.5%)
  GBM over Open-Meteo:         +0.080 C  (+14.5%)
```

Это **очень важный результат**, и он расходится с моими опасениями из chapter 5:

1. **Open-Meteo делает огромную работу.** Без него была бы MAE 2.56 °C — это бессмысленно для bucket-ов шириной 1 °C. Open-Meteo уменьшает ошибку **в 4.6 раза**.
2. **GBM добавляет реальные 14.5% сверху.** Это не маркетинг и не overfit (мы тренируем на in-time train, оцениваем на свежих 30 днях, в которых модель ничего не видела).
3. **Forecast — это не та часть, которая ломается.** Структурно прогноз работает.

Что **продолжает** ломаться (ещё раз):
- σ-калибровка (chapter 5 reliability)
- Sigma не адаптивная под режим атмосферы (chapter 6 §4)
- Bias по ветровым режимам (chapter 6 §7) — не учитывается
- Edge close-to-resolve (chapter 6 §8) — не используется

То есть **прогноз хороший, но структура использования прогноза для betting'а — нет**.

## §11. Уровни роста проекта

Глава §11.2 задаёт roadmap. Применил к нашему статусу:

| Уровень | Что | Сделано? |
|---|---|---|
| 1 | METAR feed + расширенный near-close | ❌ Нет, провайдер есть, использования нет |
| 2 | GFS + HRRR + ECMWF Open Data напрямую через NOMADS + ensemble spread | ❌ Не начато |
| 3 | Локальный bias по wind regime | ❌ Не начато |
| 4 | Walk-forward + stress tests | ❌ Не начато (но out-of-time holdout сделан в chapter 5) |

Из 4 уровней мы на 0. Это согласуется с философией главы: «Глава 5 уже сказала: ничего не менять до главы 7, пока не накопится 200 реальных сделок». Нам ждать.

---

## Что в этом коммите

1. [scripts/measure_climatology_baseline.py](../../scripts/measure_climatology_baseline.py) — read-only диагностика; сравнивает GBM vs Open-Meteo vs climatology на OOT-окне.
2. [docs/learning/chapter_06_map.md](.) — этот документ.

**НЕ изменено**: production logic, никакие модули в `src/detect_temperature/`. 67 тестов как были.

## Главный вывод этой главы для проекта

Прогноз — **не** наш слабый bottleneck. GBM реально бьёт baseline, Open-Meteo реально бьёт климатологию. Цифры подтверждают, что **forecast pipeline работает**.

Слабый bottleneck — это **что мы делаем с forecast'ом**:
- Используем устаревшую σ
- Не имеем METAR-canal у close
- Не учитываем расхождение между моделями
- Открываем сделки в неоптимальное время

Это инфраструктурные пробелы, не научные. Все они известны и измеримы. Решение каждого — несколько дней работы. Их можно сделать **после** того, как накопится 200 settled сделок и можно будет А/В сравнивать «было / стало».
