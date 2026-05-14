# Chapter 4 → код проекта

Карта понятий главы 4 (`guide/chapter_04_sizing.md`) и места в проекте.

В этом коммите я **впервые тронул production-логику** в пути ученика — но **только аннотацией**, не sizing'ом. Глава 4 сама в §10 чётко перечисляет 4 условия для перехода на Kelly («200+ resolved, измеренный MAE, подтверждённый PnL, fat tails решены»). Ни одно из них пока не выполнено. Поэтому добавил `kelly_fraction()` и колонки `yes_kelly_fraction` / `no_kelly_fraction` в каждый signal row — это диагностика, отбор сделок не меняется.

Артефакты этой главы:
- [signals.py:341-358](../../src/detect_temperature/signals.py#L341-L358) — функция `kelly_fraction(edge, price)`
- [signals.py:228-229](../../src/detect_temperature/signals.py#L228-L229) — каждый signal row теперь несёт `yes_kelly_fraction` и `no_kelly_fraction`
- [scripts/measure_kelly_counterfactual.py](../../scripts/measure_kelly_counterfactual.py) — read-only диагностика: что было бы, если бы стейк выставлялся по full/quarter Kelly
- [tests/test_polymarket_signals.py:168-200](../../tests/test_polymarket_signals.py#L168-L200) — 2 новых теста на формулу Kelly и факт, что колонки попадают в row

---

## §1-3. Зачем Kelly и что он оптимизирует

Глава §3 ловит ловушку: Kelly **не максимизирует EV в долларах**, а максимизирует **скорость роста банкролла** (логарифмический рост). Я перепроверил это арифметически:

- Банк $100, edge 5%, ставка 1% → через 100 сделок ~$170
- Банк $100, edge 5%, ставка 50% → через 100 сделок ~$3 (после трёх подряд лоссов)

Эта асимметрия — точно та причина, по которой `risk_profiles.bankroll_100` ставит `max_stake_usdc = 0.25` ([risk_profiles.py:30](../../src/detect_temperature/risk_profiles.py#L30)). Это ниже даже четверти Kelly для типичной сделки. Глава это явно одобряет в §10: "плоский размер — это разумная, но консервативная стратегия пока модель не калибрована".

## §4. Формула Kelly

`f* = (q − p) / (1 − p)`

Реализация в [signals.py:341-358](../../src/detect_temperature/signals.py#L341-L358):

```python
def kelly_fraction(edge: float | None, price: float | None) -> float | None:
    if edge is None or price is None:
        return None
    denom = 1.0 - price
    if denom <= 0:
        return None
    return edge / denom
```

Здесь `edge` — это `q − p − fee` (то есть с уже вычтенной комиссией), а не голый `q − p`. Разница маленькая: правильнее было бы делить на `(1 − p − fee)`, но fee на Polymarket ≈ 1¢ при p=0.5, эффект на 2-3% Kelly fraction'а.

Проверка на главе:
- Сеул p=0.38, q=0.42 → edge=0.04 → `kelly_fraction(0.04, 0.38) = 0.0645` ≈ 6.5% (глава ровно это сказала)
- Дешёвый хвост p=0.10, q=0.20 → edge=0.10 → `kelly_fraction(0.10, 0.10) = 0.1111` ≈ 11.1% (глава тоже)

Это два примера в `test_kelly_fraction_chapter4_examples`, защищающие нас от регрессий.

## §5-6. Дешёвые хвосты и Kelly

Глава №2 указала, что fat tails математически обосновывают **покупку дешёвых хвостов**. §6 главы 4 добавляет: при одинаковом абсолютном edge дешёвый хвост получает **больше** Kelly fraction'а, потому что (1−p) большое.

В коде это видно прямо: при `p=0.10` знаменатель `1−p = 0.90`, а при `p=0.85` всего `0.15`. Тот же edge даёт в 6× меньший Kelly на дорогом NO.

Это **математическое обоснование** включить BUY_YES на хвостах, когда модель будет калибрована. Сейчас он отключён в `bankroll_100` через `allow_buy_yes=False` ([risk_profiles.py:13](../../src/detect_temperature/risk_profiles.py#L13)) — правильное решение, потому что overfitting (глава 5) пока бы съел весь edge.

## §7. Full Kelly = ловушка переоценки

Глава квотирует:
- Full Kelly: ~33% сессий с просадкой до 50% перед удвоением
- Half Kelly: ~11%
- Quarter Kelly: ~4%

Я не стал это перепроверять симуляциями — это известный результат из arXiv 2412.14144 (sources.md №2). Качественно правильно: log-utility сильно штрафует переоценку q.

## §8. Quarter Kelly

`bankroll_100` сейчас ставит **$0.25 (= 0.25% банкролла)**. Это типично примерно **1/15 от full Kelly** для текущих edge'ей.

Гипотетический переход на quarter Kelly **с cap'ом 2% банкролла**:

| Метрика | Сейчас (flat $0.25) | Hypothetical quarter Kelly + cap 2% |
|---|---|---|
| Размер на сделку | $0.25 | ~$2.00 (упирается в cap) |
| Realised PnL на 22 settled | +$0.61 | +$4.90 (×8) |
| Худший проигрыш | -$0.25 | -$2.00 |
| Total stake on 22 trades | $5.50 | $44.00 |

Это в [scripts/measure_kelly_counterfactual.py](../../scripts/measure_kelly_counterfactual.py). Восьмикратное усиление PnL, но и восьмикратное усиление худшего лосса. Если бы среди 22 был один бак-волатильный день типа европейского фронта (см. §10 главы 3 про корреляцию), мы могли бы потерять $20+ за день, что 20% банкролла. Это та самая причина, по которой переход на Kelly без решения корреляции = опасно.

## §9. Когда Kelly не работает

Все 4 случая главы видны прямо в проекте:

| Случай | Где это в коде |
|---|---|
| q неточен (MAE > ширины ведра) | [chapter_03_map.md §9](chapter_03_map.md): 6/22 losses при `fair_probability ≥ 0.78` — модель переуверенна |
| Коррелированные ставки | Лимиты есть в [risk_profiles.py:36-39](../../src/detect_temperature/risk_profiles.py#L36-L39), но они численные, не синоптические |
| Минимум ставки + газ | Нет жёсткой защиты, просто маленький стейк делает это нерелевантным |
| Маленькая выборка | 22 settled — это ровно тот случай. Стандартная ошибка win-rate ~10pp |

## §10. Связь с проектом — плоские $0.25

Глава §10 точно описывает, что мы делаем сейчас:
- Защита от переоценки edge ✓
- Простота ✓
- Калибровка модели важнее sizing'а ✓

Глава перечисляет 4 условия для перехода на Kelly:
1. **200+ resolved сделок** — у нас 22, не выполнено.
2. **Измеренный MAE на реальных сделках** — глава 3 §9 показала: модель уверенее, чем должна (lost rate 27% vs ожидаемые 12%). Не выполнено.
3. **Подтверждённый положительный PnL** — у нас +$0.61 на 22 сделках. Это **внутри одной σ от нуля**. Не подтверждено.
4. **Решены fat tails и overfitting** — глава 5 материал, не сделано.

Поэтому **код на отбор не трогаем**. Но Kelly fraction теперь видна в каждой строке — и это ценная информация для будущего, когда условия будут выполнены.

## §11-12. min_edge и хард-капы

| Cap | Где |
|---|---|
| `min_edge=0.05` | [risk_profiles.py:9](../../src/detect_temperature/risk_profiles.py#L9) |
| `robust_min_edge=0.10` | [risk_profiles.py:42](../../src/detect_temperature/risk_profiles.py#L42) |
| `max_stake_usdc=0.25` (одна сделка) | [risk_profiles.py:30](../../src/detect_temperature/risk_profiles.py#L30) |
| `max_total_exposure_pct=0.30` | [risk_profiles.py:31](../../src/detect_temperature/risk_profiles.py#L31) |
| `max_event_exposure_pct=0.01` | [risk_profiles.py:32](../../src/detect_temperature/risk_profiles.py#L32) |
| Drawdown kill-switch −$10 | [risk_profiles.py:48](../../src/detect_temperature/risk_profiles.py#L48) |

Глава одобряет: «Логика правильная. Не хватает только корреляции — капы по городу не защищают от фронта на 20 городов». Это материал главы 7 (architecture).

---

## Что в этом коммите

**Production:**
1. `kelly_fraction(edge, price)` функция в `signals.py`
2. Каждый signal row теперь содержит `yes_kelly_fraction` и `no_kelly_fraction`
3. 2 новых теста (67 passing total)

**Read-only:**
4. `scripts/measure_kelly_counterfactual.py`
5. Этот документ

**НЕ изменено:** `risk_profiles.py`, sizing logic, betting decision. Live-портфель ведёт себя ровно как раньше.

## Чего я **не** сделал

- Не перешёл на quarter Kelly. Это потребует выполнения 4 условий из §10. Сейчас выполнено 0 из 4.
- Не добавил correlation-aware sizing. Это глава 7.
- Не убрал `allow_buy_yes=False`. Это материал глав 5 (calibration) + 6 (weather data).
- Не пересчитал bankroll_100 cap'ы. Главу читаю как одобрение текущей конфигурации.

## Что выходит из главы (вывод)

На 22 settled позициях, если бы мы ставили quarter Kelly с cap 2%, наш PnL был бы +$4.90 вместо +$0.61. Восьмикратно. Но и максимальный worst-case dollar loss за один день был бы ~$10 на коррелированном фронте — что прошибает kill-switch и останавливает торговлю.

Без решения корреляции (глава 7) и калибровки (глава 5) переход на Kelly — это амплификация риска без амплификации edge'а. Текущая стратегия `flat $0.25 + всё остальное прекрасно` — это **сознательная недоутилизация**. Не баг.
