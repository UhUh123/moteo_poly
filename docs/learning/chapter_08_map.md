# Chapter 8 → код проекта

Карта главы 8 (`guide/chapter_08_python_reading.md`) — **не для кодинга**, а для проверки, что утверждения главы соответствуют реальности проекта. Глава — справочник по Python для чтения; она ссылается на конкретные паттерны, которые якобы есть в коде. Я их перепроверил и нашёл одну ошибку.

---

## §1-14. Что глава описывает корректно

Эти конструкции реально встречаются в проекте, проверены grep'ом:

| § | Конструкция | Подтверждено |
|---|---|---|
| 2 | dict как структура для health.json и paper rows | [paper.py:805](../../src/detect_temperature/paper.py#L805) `_position_from_signal` строит dict |
| 4 | if/elif/else для выбора `paper_side` | [signals.py:474](../../src/detect_temperature/signals.py) ветви BUY_YES / BUY_NO / NO_TRADE |
| 6 | List comprehension с фильтром `[x for x in y if z]` | [evaluation.py:36](../../src/detect_temperature/evaluation.py#L36), [polymarket.py:171](../../src/detect_temperature/polymarket.py#L171), [polymarket.py:185](../../src/detect_temperature/polymarket.py#L185) |
| 7 | Классы и dataclass | 5 dataclass'ов: [signals.py:52](../../src/detect_temperature/signals.py#L52), [evaluation.py:9](../../src/detect_temperature/evaluation.py#L9), [polymarket.py:69](../../src/detect_temperature/polymarket.py#L69), [near_close.py:49](../../src/detect_temperature/near_close.py#L49), [near_close.py:134](../../src/detect_temperature/near_close.py#L134) |
| 9 | pandas DataFrame для CSV | [models/gbm.py](../../src/detect_temperature/models/gbm.py) `pd.read_csv`, [pipeline.py](../../src/detect_temperature/pipeline.py) сохранения через DataFrame |
| 11 | Type hints `: float`, `-> dict` | Используются последовательно. [signals.py:60](../../src/detect_temperature/signals.py#L60) с `list[dict[str, Any]]` |
| 12 | `with open(...)` контекстные менеджеры | Везде, где работа с CSV/JSON |
| 13 | try/except для устойчивости | Найдено 109 try/except'ов в `src/detect_temperature/` (включая [signals.py:582](../../src/detect_temperature/signals.py#L582), [paper.py:117](../../src/detect_temperature/paper.py#L117)) |
| 14 | `@dataclass` декоратор | См. §7 выше |

## §15. **Ошибка главы — click vs argparse**

Глава §15 утверждает:

> CLI через click — структура [cli.py](...). Когда ты вызываешь `python -m detect_temperature.cli refresh-open-positions`, на самом деле работает [cli.py](...):
>
> ```python
> @click.group()
> def cli():
>     ...
> @cli.command("refresh-open-positions")
> @click.option("--bankroll-usdc", default=100.0)
> def refresh_open_positions_cmd(bankroll_usdc):
>     ...
> ```

**Реальность другая.** Я проверил [src/detect_temperature/cli.py:3](../../src/detect_temperature/cli.py#L3):

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import (...)
```

Проект использует **`argparse`**, а не `click`. `import click` нигде в `src/detect_temperature/` не встречается (`grep -rn "import click" src/`). Структура — `argparse.ArgumentParser()`, `subparsers.add_parser(...)`, `parser.add_argument(...)`. Это эквивалент по результату, но другая библиотека и другой синтаксис.

**Почему это важно для ученика:** в главе 9 (которая идёт следующей и читает конкретные модули) обещано пройти по `cli.py`. Если ты будешь искать `@click.command` — ничего не найдёшь. Правильный паттерн — `subparsers.add_parser("name")` и потом `if args.command == "name"`.

Эту ошибку **не правлю в самой главе** — это пользовательский материал в `~/Desktop/guide/`, не код проекта. Просто фиксирую для агента, который придёт после меня.

## §16. Структура файла — действительно так

Глава §16 говорит:

> 1. Импорты в начале
> 2. Константы капсом (например `SIGMA_FLOOR_C = 1.5`)
> 3. Dataclass'ы / классы
> 4. Функции
> 5. `if __name__ == "__main__":` в конце

Проверил на главных модулях:
- [signals.py](../../src/detect_temperature/signals.py): импорты → `STRATEGY_VERSION = "..."`, `SIGMA_FLOOR_C = 1.5`, `SIGMA_MAE_MULTIPLIER = 1.5` → функция `load_station_calibrations` → больше функций → нет `__main__`. ✅
- [paper.py](../../src/detect_temperature/paper.py): импорты → `OPEN_STATUSES = {...}`, `SETTLED_STATUSES = {...}` → функции → нет `__main__`. ✅
- [scripts/windows_collector.py](../../scripts/windows_collector.py): импорты → константы → классы/функции → `if __name__ == "__main__": sys.exit(main())`. ✅

## §17. Что не нужно знать

Глава перечисляет async/await, метаклассы, дескрипторы, generics, pickle. Я проверил — действительно ни async/await, ни метаклассов, ни дескрипторов в `src/detect_temperature/` нет. `joblib` для pickle используется как одна функция (`joblib.dump` / `joblib.load`) в [models/gbm.py](../../src/detect_temperature/models/gbm.py) — деталей знать не надо.

## §18. Шаблон чтения функции

Применил шаблон к реальной функции [paper.open_strategy_paper_portfolio:94](../../src/detect_temperature/paper.py#L94). Сигнатура совпадает с примером в главе **по структуре** (preserve_open=True, returns dict). Это пример, который реально работает как контрольный — глава не выдумывает.

---

## Что в этом коммите

1. [docs/learning/chapter_08_map.md](.) — этот документ.
2. **Никаких code changes.** Глава 8 — справочник, она код менять не предлагает. Единственное полезное замечание — flag click→argparse mismatch для будущих читателей.

67 тестов как были.

## Главный вывод этой главы

Глава 8 — корректный справочник за одним исключением: §15 про click. Если следующая сессия откроет `cli.py` ожидая `@click.group()`, она будет в замешательстве. Поэтому я зафиксировал это здесь в виде явного предупреждения. Когда я доберусь до главы 9, начну с `cli.py` и покажу настоящий argparse-паттерн.
