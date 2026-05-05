from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .paper import _contains_interval
from .units import celsius_to_fahrenheit


def evaluate_resolved_predictions(
    predictions_path: str | Path,
    actuals_path: str | Path,
    signals_path: str | Path,
    event_output_path: str | Path,
    signal_output_path: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    predictions = {row.get("slug", ""): row for row in _read_csv(predictions_path)}
    actuals = [row for row in _read_csv(actuals_path) if row.get("status") == "ok"]
    signals = _read_csv(signals_path)

    actuals_by_slug = {row.get("slug", ""): row for row in actuals}
    top_buckets = _top_bucket_results(signals, actuals_by_slug)
    event_rows = [
        _event_result(row, predictions.get(row.get("slug", "")), top_buckets.get(row.get("slug", "")))
        for row in actuals
    ]
    event_rows = [row for row in event_rows if row is not None]
    signal_rows = _signal_results(signals, actuals_by_slug)

    summary = _summary(event_rows, signal_rows)
    _write_csv(event_rows, event_output_path)
    _write_csv(signal_rows, signal_output_path)
    payload = {
        "summary": summary,
        "events": event_rows,
        "signals": signal_rows,
    }
    if report_path:
        render_resolved_report(payload, report_path)
    return payload


def render_resolved_report(payload: dict[str, Any], path: str | Path) -> None:
    summary = payload["summary"]
    event_rows = payload["events"]
    signal_rows = payload["signals"]
    top_events = "\n".join(_event_row_html(row) for row in event_rows)
    top_signals = "\n".join(_signal_row_html(row) for row in signal_rows[:80])
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resolved Model Check</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #fff;
      --text: #17202a;
      --muted: #637083;
      --line: #d8dee8;
      --good: #0f7a45;
      --bad: #b42318;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      padding: 18px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0; font-size: 20px; }}
    h2 {{ margin: 18px 0 10px; font-size: 16px; }}
    main {{ padding: 18px 24px 28px; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 10px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 21px; }}
    .note {{
      margin: 10px 0 16px;
      color: var(--muted);
      max-width: 960px;
    }}
    .table-wrap {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
    }}
    table {{ width: 100%; min-width: 960px; border-collapse: collapse; }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      white-space: nowrap;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-size: 12px; background: #fafbfc; position: sticky; top: 0; }}
    td.title {{ white-space: normal; min-width: 280px; }}
    .good {{ color: var(--good); font-weight: 650; }}
    .bad {{ color: var(--bad); font-weight: 650; }}
  </style>
</head>
<body>
  <header>
    <h1>Проверка модели на resolved рынках</h1>
    <div class="note">Это не PnL. Здесь проверяется, насколько прогноз модели совпал с фактической температурой и правильным outcome bucket.</div>
  </header>
  <main>
    <div class="metrics">
      {_metric("Resolved events", summary["resolved_events"])}
      {_metric("MAE", f'{summary["mae_resolution"]:.2f} {summary["primary_unit"]}' if summary["mae_resolution"] is not None else "-")}
      {_metric("Exact rounded", _pct_or_dash(summary["rounded_exact_pct"]))}
      {_metric("Within 1 unit", _pct_or_dash(summary["within_1_unit_pct"]))}
      {_metric("Within 2 units", _pct_or_dash(summary["within_2_units_pct"]))}
      {_metric("Visible bucket hit", _pct_or_dash(summary["visible_top_bucket_hit_pct"]))}
      {_metric("Signal win rate", _pct_or_dash(summary["signal_win_rate_pct"]))}
    </div>
    <p class="note">`Exact rounded` значит: округленный точечный прогноз совпал с фактическим resolved значением. `Within 1/2 unit` значит: модель ошиблась не больше чем на 1/2 градуса в единицах рынка. `Visible bucket hit` проверяет только bucket'ы, которые были видны в текущем Polymarket snapshot; если часть диапазонов уже пропала из снимка, эту метрику нельзя считать полной точностью outcome'ов. `Signal win rate` считает, выиграла ли выбранная моделью сторона BUY_YES/BUY_NO.</p>
    <h2>Event-level результат</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Дата</th><th>Рынок</th><th>Прогноз</th><th>Факт</th><th>Ошибка</th><th>Rounded</th><th>Top bucket</th></tr></thead>
        <tbody>{top_events}</tbody>
      </table>
    </div>
    <h2>Outcome/signal результат</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Результат</th><th>Сторона</th><th>Fair</th><th>Outcome</th><th>Факт</th><th>Рынок</th></tr></thead>
        <tbody>{top_signals}</tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(doc, encoding="utf-8")


def _event_result(
    actual: dict[str, str],
    prediction: dict[str, str] | None,
    top_bucket: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if prediction is None:
        return None
    unit = actual.get("resolution_unit") or prediction.get("target_unit") or "celsius"
    predicted = _as_float(prediction.get("corrected_prediction_resolution_value"))
    actual_value = _as_float(actual.get("observed_resolution_value"))
    if predicted is None or actual_value is None:
        return None
    error = predicted - actual_value
    rounded_prediction = round(predicted)
    return {
        "slug": actual.get("slug", ""),
        "title": prediction.get("title", actual.get("slug", "")),
        "target_date": actual.get("target_date", ""),
        "unit": unit,
        "prediction": round(predicted, 4),
        "rounded_prediction": rounded_prediction,
        "actual": round(actual_value, 4),
        "error": round(error, 4),
        "abs_error": round(abs(error), 4),
        "rounded_exact": int(rounded_prediction == round(actual_value)),
        "top_bucket": top_bucket.get("group_item_title", "") if top_bucket else "",
        "top_bucket_probability": top_bucket.get("fair_yes_probability") if top_bucket else None,
        "top_bucket_hit": int(bool(top_bucket and top_bucket.get("hit"))),
    }


def _top_bucket_results(
    signals: list[dict[str, str]],
    actuals_by_slug: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    candidates_by_slug: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        slug = signal.get("event_slug", "")
        actual = actuals_by_slug.get(slug)
        if actual is None:
            continue
        fair_yes = _as_float(signal.get("fair_yes_probability"))
        actual_c = _as_float(actual.get("observed_temp_c"))
        if fair_yes is None or actual_c is None:
            continue
        unit = signal.get("interval_unit") or actual.get("resolution_unit") or "celsius"
        actual_value = celsius_to_fahrenheit(actual_c) if unit == "fahrenheit" else actual_c
        hit = _contains_interval(
            actual_value,
            lower=_as_optional_float(signal.get("interval_lower")),
            upper=_as_optional_float(signal.get("interval_upper")),
        )
        candidates_by_slug.setdefault(slug, []).append(
            {
                "event_slug": slug,
                "group_item_title": signal.get("group_item_title", ""),
                "fair_yes_probability": round(fair_yes, 6),
                "hit": hit,
            }
        )
    return {
        slug: max(rows, key=lambda row: row["fair_yes_probability"])
        for slug, rows in candidates_by_slug.items()
        if rows
    }


def _signal_results(signals: list[dict[str, str]], actuals_by_slug: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for signal in signals:
        slug = signal.get("event_slug", "")
        actual = actuals_by_slug.get(slug)
        if actual is None:
            continue
        side = signal.get("paper_side")
        if side not in {"BUY_YES", "BUY_NO"}:
            continue
        actual_c = _as_float(actual.get("observed_temp_c"))
        if actual_c is None:
            continue
        unit = signal.get("interval_unit") or actual.get("resolution_unit") or "celsius"
        actual_value = celsius_to_fahrenheit(actual_c) if unit == "fahrenheit" else actual_c
        yes_won = _contains_interval(
            actual_value,
            lower=_as_optional_float(signal.get("interval_lower")),
            upper=_as_optional_float(signal.get("interval_upper")),
        )
        won = yes_won if side == "BUY_YES" else not yes_won
        rows.append(
            {
                "event_slug": slug,
                "event_title": signal.get("event_title", ""),
                "market_slug": signal.get("market_slug", ""),
                "question": signal.get("question", ""),
                "group_item_title": signal.get("group_item_title", ""),
                "side": side,
                "fair_probability": _round_or_none(_as_float(signal.get("paper_fair_probability"))),
                "model_edge": _round_or_none(_as_float(signal.get("paper_net_edge"))),
                "actual_value": round(actual_value, 4),
                "actual_unit": unit,
                "won": int(won),
            }
        )
    rows.sort(key=lambda row: (row["won"], row["fair_probability"] or 0), reverse=True)
    return rows


def _summary(event_rows: list[dict[str, Any]], signal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    mae = None
    rounded_exact_pct = None
    within_1_unit_pct = None
    within_2_units_pct = None
    if event_rows:
        mae = sum(row["abs_error"] for row in event_rows) / len(event_rows)
        rounded_exact_pct = sum(row["rounded_exact"] for row in event_rows) / len(event_rows) * 100.0
        within_1_unit_pct = sum(row["abs_error"] <= 1.0 for row in event_rows) / len(event_rows) * 100.0
        within_2_units_pct = sum(row["abs_error"] <= 2.0 for row in event_rows) / len(event_rows) * 100.0
    top_bucket_events = [row for row in event_rows if row.get("top_bucket")]
    visible_top_bucket_hit_pct = (
        sum(row["top_bucket_hit"] for row in top_bucket_events) / len(top_bucket_events) * 100.0
        if top_bucket_events
        else None
    )
    signal_win_rate = (
        sum(row["won"] for row in signal_rows) / len(signal_rows) * 100.0
        if signal_rows
        else None
    )
    units = {row["unit"] for row in event_rows}
    primary_unit = next(iter(units)) if len(units) == 1 else "resolution units"
    return {
        "resolved_events": len(event_rows),
        "resolved_signals": len(signal_rows),
        "mae_resolution": round(mae, 4) if mae is not None else None,
        "primary_unit": primary_unit,
        "rounded_exact_pct": round(rounded_exact_pct, 2) if rounded_exact_pct is not None else None,
        "within_1_unit_pct": round(within_1_unit_pct, 2) if within_1_unit_pct is not None else None,
        "within_2_units_pct": round(within_2_units_pct, 2) if within_2_units_pct is not None else None,
        "visible_top_bucket_hit_pct": round(visible_top_bucket_hit_pct, 2) if visible_top_bucket_hit_pct is not None else None,
        "top_bucket_hit_pct": round(visible_top_bucket_hit_pct, 2) if visible_top_bucket_hit_pct is not None else None,
        "signal_win_rate_pct": round(signal_win_rate, 2) if signal_win_rate is not None else None,
    }


def _pct_or_dash(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"
def _event_row_html(row: dict[str, Any]) -> str:
    rounded_class = "good" if row["rounded_exact"] else "bad"
    bucket_class = "good" if row.get("top_bucket_hit") else "bad"
    bucket_text = row.get("top_bucket") or "-"
    return (
        "<tr>"
        f"<td>{html.escape(str(row['target_date']))}</td>"
        f"<td class=\"title\">{html.escape(str(row['title']))}</td>"
        f"<td>{row['prediction']} {html.escape(str(row['unit']))}</td>"
        f"<td>{row['actual']} {html.escape(str(row['unit']))}</td>"
        f"<td>{row['error']:+.2f}</td>"
        f"<td class=\"{rounded_class}\">{'yes' if row['rounded_exact'] else 'no'}</td>"
        f"<td class=\"{bucket_class}\">{html.escape(str(bucket_text))}</td>"
        "</tr>"
    )


def _signal_row_html(row: dict[str, Any]) -> str:
    klass = "good" if row["won"] else "bad"
    return (
        "<tr>"
        f"<td class=\"{klass}\">{'won' if row['won'] else 'lost'}</td>"
        f"<td>{html.escape(str(row['side']))}</td>"
        f"<td>{(row['fair_probability'] or 0) * 100:.1f}%</td>"
        f"<td>{html.escape(str(row['group_item_title']))}</td>"
        f"<td>{row['actual_value']} {html.escape(str(row['actual_unit']))}</td>"
        f"<td class=\"title\">{html.escape(str(row['event_title']))}</td>"
        "</tr>"
    )


def _metric(label: str, value: Any) -> str:
    return f'<section class="metric"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></section>'


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(records: list[dict[str, Any]], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        if not records:
            fh.write("")
            return
        fieldnames = []
        seen = set()
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _as_float(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    return _as_float(value)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 6)
