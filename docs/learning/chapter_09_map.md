# Chapter 9 → код проекта

Карта главы 9 (`guide/chapter_09_code_walkthrough.md`) — финальная техническая глава цикла. Глава проводит по 6 ключевым модулям. Я перепроверил каждое утверждение против реального кода. Главные расхождения и подтверждения — ниже.

---

## §0. Карта чтения — line counts

Глава приводит таблицу размеров. Реальные размеры:

| Модуль | Глава говорит | Реально |
|---|---|---|
| risk_guards.py | 59 | 59 ✓ |
| risk_profiles.py | 73 | 73 ✓ |
| status.py | 148 | 148 ✓ |
| near_close.py | 203 | 203 ✓ |
| signals.py | 639 | **668** (написалось +29 строк после Phase 2c + chapter 4 kelly_fraction) |
| cli.py | 682 | 682 ✓ |
| paper.py | 1243 | 1243 ✓ |
| strategy_lab.py | 1303 | 1303 ✓ |

`signals.py` подрос на 29 строк по сравнению с моментом написания главы. Логика та же, но цифра в главе устарела. Не ошибка автора — просто snapshot из прошлого.

## §1. risk_profiles.py — корректно

Глава перечисляет ключевые числа `bankroll_100`. Все **подтверждаются** в [risk_profiles.py:5-50](../../src/detect_temperature/risk_profiles.py#L5-L50). Я уже это анализировал в [chapter_04_map.md](chapter_04_map.md), повторять не буду.

## §2. risk_guards.py — корректно

Глава §2 говорит «одна функция `check_drawdown(state_paths, abort_usdc)`, читает `summary.realized_pnl_usdc`». Проверил [risk_guards.py:12](../../src/detect_temperature/risk_guards.py#L12) — да, ровно так. 67 строк (включая комментарии и docstring).

## §3. signals.py — есть **серьёзная неточность** в §3

Глава §3 говорит:

> **Fee хардкодится в 0.05** — это вес weather-маркета, но если Polymarket меняет тариф, надо менять код.

**Реально fee — параметр.** В [signals.py:336-337](../../src/detect_temperature/signals.py#L336-L337):

```python
def fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)
```

`fee_rate` принимается аргументом. Дефолт `0.05` стоит в `risk_profiles.bankroll_100["build-market-signals"]["weather_fee_rate"]`, но это **risk profile**, а не хардкод в `signals.py`. Если Polymarket поменяет тариф — поменять одно число в risk_profiles.

Тонкая разница: то, что глава называет «hardcoded», — это значение по умолчанию в риск-профиле. Архитектурно `fee_rate` уже выведено наружу. Глава ошибочно описала это как уязвимость.

## §3 signals.py — другие пункты корректны

| Пункт главы | Реальность |
|---|---|
| `SIGMA_FLOOR_C = 1.5` | ✓ [signals.py:14](../../src/detect_temperature/signals.py#L14) |
| `SIGMA_MAE_MULTIPLIER = 1.5` | ✓ [signals.py:15](../../src/detect_temperature/signals.py#L15) |
| `STRATEGY_VERSION = "betting_v2_conservative"` | ✓ [signals.py:13](../../src/detect_temperature/signals.py#L13) |
| `fair_yes = normal_interval_probability(...)` | ✓ [signals.py:211](../../src/detect_temperature/signals.py#L211) — это и есть точка fat tails из chapter 2 |
| `yes_edge = fair_yes - yes_buy_price - yes_fee` | ✓ [signals.py:219](../../src/detect_temperature/signals.py#L219) |

## §4. near_close.py — корректно

Глава §4 точно описывает logic. `shrink_sigma(sigma_c, hours_remaining)` с floor `0.25 × σ_calibrated` — да, это в [near_close.py](../../src/detect_temperature/near_close.py). `fetch_intraday_max_min` использует Open-Meteo, а не METAR — да, это и есть слабое место из chapter 6.

## §5. paper.py — глава **смешала две функции**

Это самое важное расхождение в главе 9.

Глава §5 говорит:

> ### Sizing — `_candidate_stake`
> ```python
> def _candidate_stake(row, bankroll_usdc, max_stake_usdc):
>     edge = paper_net_edge
>     suggested = bankroll_usdc * edge / 20.0  # ← попытка edge-weighted
>     return min(suggested, max_stake_usdc)
> ```
> Сейчас `max_stake_usdc=0.25` — поэтому почти всегда работает кэп, не edge-weighted. То есть `_candidate_stake` теоретически готов к Kelly-стилю, но клампится в плоский размер.

**Это неточно.** Я перепроверил оба места:

[paper.py:1051-1054](../../src/detect_temperature/paper.py#L1051-L1054) (актуальная функция в продакшне):
```python
def _candidate_stake(row, bankroll_usdc, max_stake_usdc):
    suggested = _as_float(row.get("suggested_max_stake_usdc"))
    stake = suggested if suggested > 0 else bankroll_usdc * 0.0025
    return round(min(stake, max_stake_usdc), 4)
```

То есть `_candidate_stake` **читает поле `suggested_max_stake_usdc` из row**, а **не считает** `edge / 20`. Эта функция тонкая обёртка вокруг суммы, которую кто-то другой уже посчитал.

Сама формула `edge / 20.0` (эту глава имела в виду) живёт в **другой** функции, [signals.py:575](../../src/detect_temperature/signals.py#L575):

```python
risk_fraction = min(0.005, max(0.001, edge / 20.0))
```

То есть процесс такой:
1. `signals.py` для каждого кандидата считает `suggested_max_stake_usdc = bankroll × clamp(edge/20, 0.1%, 0.5%)`
2. `paper.py:_candidate_stake` потом кэпит этот suggested в `max_stake_usdc=0.25`

Глава 9 описала это как одну функцию в `paper.py`, что путает. Реальный путь Kelly-перехода: править **обе** — `signals._suggest_stake` (формулу) **и** `paper._candidate_stake` (cap). Поднять `max_stake_usdc` в `risk_profiles` без правки signals не даст Kelly.

Это важно для будущего реального перехода на Kelly. Зафиксировал.

## §5 paper.py — settle логика корректна

Описание в §5 settle совпадает с [paper.settle_paper_portfolio](../../src/detect_temperature/paper.py#L185-L213). `OPEN_STATUSES` и `SETTLED_STATUSES` константы правильные, я проверял в [chapter_07_map.md](chapter_07_map.md).

## §6. strategy_lab.py — diversity score формула неверная

Глава §6 говорит:

> ```
> diversity_score = 1 / (1 + города_с_позицией)
> ```

**Реальная формула** в [strategy_lab.py:622-635](../../src/detect_temperature/strategy_lab.py#L622-L635):

```python
def _diversified_score(row, city_positions, date_exposure, city_cap, date_cap):
    raw = _as_float(row.get("robust_score"), -999.0) or -999.0
    city_penalty = 0.012 * city_positions.get(city, 0)
    date_penalty = 0.01 * (date_exposure.get(date, 0.0) / date_cap) if date_cap > 0 else 0.0
    cap_bonus = 0.004 if city_positions.get(city, 0) == 0 else 0.0
    return raw - city_penalty - date_penalty + cap_bonus
```

Это **аддитивная** штрафная функция, а не множитель `1 / (1 + n)`. Эффект качественно похож (наказание дублирующихся городов), но числа другие. Глава упростила.

## §6 strategy_lab.py — стресс-сценарии корректно

Глава говорит "27 сценариев = 3×3×3 (mean × sigma × slippage)". Это совпадает с [risk_profiles.py:50-52](../../src/detect_temperature/risk_profiles.py#L50-L52):

```python
"mean_shifts_c": "-2.0,0,2.0",
"sigma_values_c": "2.5,3.0,3.5",
"slippage_values": "0,0.01,0.02",
```

3 × 3 × 3 = 27. Совпадает.

## §7 status.py — корректно (atomic write подтверждено)

Глава говорит:

> Atomic write через temp file → rename. Защита от corruption.

[status.py:142-147](../../src/detect_temperature/status.py#L142-L147) — да, это там:
```python
tmp_path = p.with_suffix(p.suffix + ".tmp")
tmp_path.parent.mkdir(parents=True, exist_ok=True)
with tmp_path.open("w", encoding="utf-8") as fh:
    ...
os.replace(tmp_path, p)
```

Используется `os.replace`, а не просто `tmp.rename` — это правильный atomic операция и на Linux, и на Windows.

## §7 cli.py — глава снова говорит о click

Глава §7 повторяет: «Обёртка над всем через click». Это снова неверно (см. [chapter_08_map.md §15](chapter_08_map.md)). Реально argparse.

## §8. Таблица "где менять X" — проверка

Глава §8 даёт таблицу из 9 пунктов. Прошёл по каждому, скорректировал:

| Изменение | Глава говорит | Уточнение |
|---|---|---|
| Гауссиану на эмпирическое | signals.py | ✓ [signals.py:323-336](../../src/detect_temperature/signals.py) `normal_interval_probability` |
| Open-Meteo → METAR в near_close | near_close.py | ✓ `fetch_intraday_max_min` |
| Sizing flat → quarter Kelly | paper.py + risk_profiles.py | ⚠️ **Также signals.py** — формула `edge/20` живёт там, а `_candidate_stake` только cap'ит |
| Drawdown по MTM | risk_guards.py | ✓ |
| Ensemble spread | signals.py | ✓ `sigma_for_station` |
| Wind-regime bias | features.py + signals.py | ✓ |
| Расширить near-close | scripts/register_daily_tasks.ps1 | ✓ |
| Walk-forward | новый scripts/walk_forward.py | ✓ |
| Reliability diagram | новый | ✓ **Уже сделано** в [scripts/measure_reliability_diagram.py](../../scripts/measure_reliability_diagram.py) (chapter 5) |
| Correlation cap | risk_guards.py + новый correlation.py | ✓ |

---

## Что в этом коммите

1. [docs/learning/chapter_09_map.md](.) — этот документ.
2. **Никаких изменений в код проекта.** Только верификация документации главы.

67 тестов как были.

## Главный вывод этой главы

Глава 9 — финальный мост между концептуальными главами 1-6 и реальным кодом. **Большинство утверждений главы верны**, но в трёх местах глава упрощает или путает:

1. `fee_rate` не «hardcoded», это аргумент функции с дефолтом в risk profile.
2. `_candidate_stake` в `paper.py` **не считает** `edge / 20`, эта формула в `signals._suggest_stake`. Глава смешала две функции.
3. `_diversified_score` — аддитивная penalty, а не множитель `1/(1+n)` как пишет глава.

Эти расхождения **не катастрофичны**, но они важны для следующей сессии. Если новый агент откроет `paper.py` и будет искать `bankroll * edge / 20` — он не найдёт. Карта это явно фиксирует.

Содержательно: глава точно показывает, **где** в коде живёт каждое решение из глав 1-6. Архитектура читается как заявленная. Глава 9 готовит читателя к фазе изменений — но фаза ещё не наступила (нужно ≥200 settled сделок).

## Завершение цикла учебных глав

Главы 1-9 пройдены. Каждая получила свою карту в `docs/learning/`. Главу 10 (стратегическое решение) не пишу — она пишется только при ≥200 settled позициях, у нас 22.

Следующий рост проекта — это **накопление данных**, не новый код. Когда выборка достигнет нужного размера, эти карты + диагностические скрипты, которые я добавил по ходу (`measure_residual_distribution`, `measure_settled_ev`, `measure_kelly_counterfactual`, `measure_reliability_diagram`, `measure_climatology_baseline`), превратятся из учебного материала в инструмент принятия решения главы 10.
