from __future__ import annotations

import csv

from detect_temperature.resolved_eval import evaluate_resolved_predictions


def test_resolved_eval_reports_empty_sample_as_unknown_pct(tmp_path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    actuals_path = tmp_path / "actuals.csv"
    signals_path = tmp_path / "signals.csv"
    events_path = tmp_path / "events.csv"
    signal_results_path = tmp_path / "signal_results.csv"
    report_path = tmp_path / "report.html"

    _write_rows(predictions_path, ["slug", "corrected_prediction_resolution_value"], [])
    _write_rows(
        actuals_path,
        ["slug", "status", "observed_temp_c", "observed_resolution_value", "resolution_unit"],
        [
            {
                "slug": "resolved-but-not-in-current-predictions",
                "status": "ok",
                "observed_temp_c": "20",
                "observed_resolution_value": "20",
                "resolution_unit": "celsius",
            }
        ],
    )
    _write_rows(signals_path, ["event_slug", "paper_side"], [])

    payload = evaluate_resolved_predictions(
        predictions_path=predictions_path,
        actuals_path=actuals_path,
        signals_path=signals_path,
        event_output_path=events_path,
        signal_output_path=signal_results_path,
        report_path=report_path,
    )

    summary = payload["summary"]
    assert summary["resolved_events"] == 0
    assert summary["rounded_exact_pct"] is None
    assert summary["within_1_unit_pct"] is None
    assert summary["within_2_units_pct"] is None
    assert "Exact rounded</span><strong>-</strong>" in report_path.read_text(encoding="utf-8")


def _write_rows(path, fieldnames, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
