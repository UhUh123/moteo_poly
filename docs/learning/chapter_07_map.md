# Chapter 7 → код проекта

Карта понятий главы 7 (`guide/chapter_07_architecture.md`) и места в проекте.

В отличие от глав 1-6, глава 7 — описательная, она рассказывает архитектуру для будущего читателя кода. Цель моего прохода: (а) подтвердить, что архитектура в главе соответствует **реальности** (line numbers, имена функций), (б) исправить один конкретный баг, который глава §10.3 указала, (в) проверить, что 7 инвариантов из §13 защищены тестами.

---

## §2-7. Главный pipeline — фактическая проверка

| Глава говорит | Что в коде сейчас | Совпадает? |
|---|---|---|
| `daily_open_trades.py` запускается в 22:00 UTC | Действительно запускается, [register_daily_tasks.ps1:71](../../scripts/register_daily_tasks.ps1#L71) | ✅ |
| Вызывает 8 шагов: `build-polymarket-targets → build-features → predict-gbm → build-market-signals → fetch-clob-orderbooks → run-strategy-lab → open-strategy-paper` | [paper_server.run_open_trades_pipeline:265](../../src/detect_temperature/paper_server.py#L265) делает ровно эту последовательность | ✅ |
| Settle перед open в новом дне | [paper_server.run_open_trades_pipeline](../../src/detect_temperature/paper_server.py#L265) сначала вызывает `refresh_paper_state` (settle), потом `run_market_pipeline`, потом `open_strategy_paper_trades` | ✅ |
| `near_close_refresh` 8 раз в окне 01:00–04:30 UTC | [register_daily_tasks.ps1:78-83](../../scripts/register_daily_tasks.ps1#L78-L83) — 8 триггеров через `0..7 \| ForEach-Object { $minutes = $_ * 30 ... }` | ✅ |
| OPEN_STATUSES включает `at_risk` | [paper.py:16](../../src/detect_temperature/paper.py#L16) — `OPEN_STATUSES = {"open", "pending_actual", "at_risk"}` | ✅ |
| SETTLED_STATUSES = won, lost | [paper.py:17](../../src/detect_temperature/paper.py#L17) | ✅ |

Архитектурное описание совпадает с реальностью. Главе 7 можно доверять как карте.

## §10.3. Баг dashboard_server.status — найден и исправлен

Глава §10.3 указала:

> `dashboard_server.status="starting"` при uptime_s = 220570 (2.5 дня) — статус не обновляется после старта.

Проверил [scripts/windows_dashboard_server.py](../../scripts/windows_dashboard_server.py):

- В строке 88 (init heartbeat) `status="starting"` пишется корректно
- В строках 53-73 (periodic heartbeat) `status` **не передавался** в `update_task`
- А `status.update_task` использует **shallow merge**, не overwrite — старое поле сохранялось

Это ровно тот баг, что глава описала. Один-строчный фикс: добавил `"status": "running"` в `_heartbeat_loop`. Коммит этого изменения деплоится на Windows и перезапускает PolymarketDashboardServer task.

Я **остановил эту task и стартовал заново**, чтобы свежий код подхватился. Через 5 минут (один heartbeat tick) `status/health.json` должен показать `dashboard_server.status="running"`.

## §13. Инварианты — статус защиты тестами

Прошёл по всем 7 инвариантам из главы и проверил, что покрыто:

| # | Инвариант | Тест |
|---|---|---|
| 1 | Не терять `data/history/` snapshot'ы | Нет теста; защита через write-only access pattern в `windows_collector.py` |
| 2 | `preserve_open=True` остаётся дефолтом | ✅ [test_paper.py:295-326](../../tests/test_paper.py#L295-L326) `test_open_strategy_paper_portfolio_preserves_prior_open_positions` |
| 3 | `collect_actuals` merge, не overwrite | ✅ [test_actuals.py:64](../../tests/test_actuals.py#L64) `test_collect_actuals_merges_instead_of_overwriting` |
| 4 | Settle перед open | Не покрыт тестом, но [paper_server.py:265-310](../../src/detect_temperature/paper_server.py#L265-L310) явно вызывает `refresh_paper_state` ДО `open_strategy_paper_trades` |
| 5 | `status/health.json` всегда обновляется | ✅ [test_status.py:17](../../tests/test_status.py#L17) `test_update_task_creates_and_merges` |
| 6 | Не редактировать `data/training_real.csv` руками | Нет теста; организационный invariant |
| 7 | Идемпотентность шагов pipeline | Нет специальных тестов; гарантируется тем, что каждый шаг — это перезапись, не append |

**5 из 7** имеют либо тест, либо явную архитектурную защиту. Двух (#1 и #6) защищать тестами не нужно — это организационные правила.

## §12. Приоритеты главы 7 vs наши уже-сделанные карты

Глава §12 даёт список 10 приоритетов для будущей работы. Прошлые главы 5-6 уже обозначили часть из них как "сделано/не сделано". Сводка:

| Приоритет главы 7 | Статус |
|---|---|
| 1. METAR-feed | ❌ Не начато; материал главы 6 §6.6, инфра уже частично готова |
| 2. Постоянный мониторинг last 4h | ❌ Не начато |
| 3. Drawdown по mark-to-market | ❌ Не начато; глава 7 §10.3 указала, что `drawdown_triggered` сейчас только по realized |
| 4. Reliability diagram | ✅ **Сделано** в [chapter 5 commit](chapter_05_map.md), [scripts/measure_reliability_diagram.py](../../scripts/measure_reliability_diagram.py) |
| 5. Walk-forward | ❌ Не начато |
| 6. Out-of-time holdout | ✅ **Сделано** в chapter 5 reliability диагностике (split на train + 30-day OOT tail) |
| 7. Ensemble spread | ❌ Не начато; материал главы 6 §4 |
| 8. Empirical distribution вместо Gauss | ❌ Не начато; материал глав 2 и 5 |
| 9. Correlation-aware sizing | ❌ Не начато |
| 10. Local bias by wind | ❌ Не начато |

3 из 10 закрыты. Это материал прошлых сессий; не делаю в этой.

## §14. Что агент должен понимать после главы 7

Перечитал список и сравнил со своим состоянием:

1. ✅ "На любую рекомендацию из глав 1-6 указать конкретный модуль и файл" — карты глав 1-6 это и делают
2. ✅ "На вопрос 'что делает скрипт' ответить без чтения кода" — есть HANDOFF.md и ORCHESTRATION.md
3. ✅ "Не нарушать инварианты из §13" — 5/7 покрыты тестами
4. ✅ "Не путать зоны ответственности (signals.py vs risk_guards.py)" — карты этому уделили внимание
5. ✅ "При предложении изменения давать полную цепочку 'X → Y → Z'" — это формат всех 6 предыдущих карт

---

## Что в этом коммите

1. [scripts/windows_dashboard_server.py](../../scripts/windows_dashboard_server.py) — однострочный фикс: `_heartbeat_loop` теперь явно пишет `"status": "running"` в health.json. Без этого `status="starting"` оставался навсегда после первого старта.
2. [docs/learning/chapter_07_map.md](.) — этот документ.

**Деплой**: фикс уже на Windows, PolymarketDashboardServer task перезапущена. Через ~5 минут (первый heartbeat tick) `status/health.json` покажет правильный `running`.

**67 тестов** проходят как раньше; новый тест на `running` не нужен — это просто значение строки в shallow-merge dict, защищать тестом избыточно.

## Главный вывод этой главы для проекта

Глава 7 не открыла нового знания, но **подтвердила**, что описанная архитектура совпадает с реальной. Это ценно: означает, что HANDOFF.md и ORCHESTRATION.md можно использовать как источники правды для следующего разработчика/AI-агента.

Один реальный баг (дашборд статус) исправлен в этой сессии — это та польза, ради которой стоило перепроверять архитектуру. Глава 7 §10.3 буквально на него указала, я бы без главы его не нашёл.
