# Orchestration — how the Windows automation fits together

This document is the single map of the automated pipeline. If a future
agent is asked **"what is the system doing right now?"**, reading this file
and then `status/health.json` should be enough.

Everything below runs on the Windows PC at `100.105.99.20` (Tailscale hostname
`desktop-pic12cl`, user `wopipsy`), inside `C:\poly\detect-temperature\`.
The mac is only the development / dashboard host.

---

## TL;DR

Six scheduled tasks cover the full paper-trading loop plus one long-running
dashboard server. The mac does not need to be online for any of them.

```
             ┌────────────────────────────────────────────────────┐
             │  Windows Task Scheduler (timezone: PC local)       │
             └────────────────────────────────────────────────────┘

every 5 min ┃ PolymarketCollectorRegular     → scan + books + snapshot
every 1 min ┃ PolymarketCollectorHot         → books for markets closing <60m

22:00 daily ┃ PolymarketDailyOpenTrades      → full pipeline + open paper
01–04:30 ┃    PolymarketNearCloseRefresh     → refined-probability re-check
            ┃ (30-min interval, 8 firings)
06:00 daily ┃ PolymarketDailySettle          → collect actuals + settle PnL
Mon  04:17  ┃ PolymarketCalibrationRefresh   → retrain GBM + recalibrate

always      ┃ PolymarketDashboardServer      → http://100.105.99.20:8765/
```

The dashboard is reachable from the mac at **http://100.105.99.20:8765/**
over Tailscale. No local server required on mac.

Each task writes one section of `status/health.json` at the end of its
run. That JSON is the answer to "how is it going?".

---

## Where everything lives

| Path on Windows | What it is |
|---|---|
| `C:\poly\detect-temperature\` | Repo checkout. `git pull` to update (but usually we `scp` from mac). |
| `.venv\` | Python 3.14 virtual environment. Do not commit. |
| `data\polymarket_weather_markets.csv` | Latest full market snapshot (refreshed every 5 min by collector). |
| `data\polymarket_orderbooks.json` | Latest CLOB depth snapshot. |
| `data\targets.csv` / `targets.jsonl` | Normalized markets with station/date/unit. |
| `data\features.csv` | Built for predict_gbm: target features + forecast + observation + station_verified. |
| `data\station_calibration.csv` | Per-station rolling MAE + bias. Drives sigma in signals. |
| `data\training_real.csv` | Canonical training set (124k rows, 51 stations). Grows with weekly refresh. |
| `data\actuals.csv` | Resolved temperatures from Wunderground/HKO/Synoptic. |
| `data\history\YYYY-MM-DD\HHMMSS-{regular,hot}\` | Every collector snapshot, forever. This is the real dataset we build. |
| `data\history\_state\<sha12>\<filename>` | Content-addressed pool of model state files (predictions / signals / calibration) at the time of each snapshot. Each `regular` snapshot writes a `state_manifest.json` mapping logical name -> sha. Used by walk-forward backtests. See `src/detect_temperature/state_archive.py`. |
| `artifacts\models\gbm.joblib` | Current production model. |
| `artifacts\predictions_gbm.csv` | Today's corrected predictions for today's markets. |
| `artifacts\market_signals.csv` | Per-bucket signals (paper_side = BUY_YES / BUY_NO / NO_TRADE). |
| `artifacts\strategy_portfolio_v2.csv` | Robust-filtered portfolio candidates from Strategy Lab. |
| `artifacts\paper_portfolio.csv` / `.json` | Open + settled paper positions. |
| `artifacts\paper_dashboard.html` | Dashboard HTML (what the mac serves). |
| `artifacts\paper_runs\<ts>-<label>\` | Archive of each run so we can compare yesterday vs today. |
| `logs\dashboard_server.log` | Long-running dashboard server log. |
| `logs\collector.log` | Append-only log for both regular and hot collector. |
| `logs\daily_open_trades.log` | Daily open cycle log. |
| `logs\daily_settle.log` | Daily settle log. |
| `logs\near_close.log` | Near-close refresh log (via `logging` in `near_close` import chain). |
| `status\health.json` | **Single-source-of-truth** status file. Read this first. |

---

## `status\health.json` at a glance

```json
{
  "updated_at": "ISO UTC",
  "tasks": {
    "collector_regular":  { "last_run", "code", "markets_scanned", "snapshot_dir" },
    "collector_hot":      { "last_run", "code", "markets_watched", "outcome" },
    "daily_open_trades":  { "last_run", "code", "outcome", "positions_opened",
                            "total_staked_usdc", "candidates", "robust", "selected" },
    "near_close_refresh": { "last_run", "code", "refreshed", "resolved_won",
                            "resolved_lost", "at_risk" },
    "daily_settle":       { "last_run", "code", "actuals_ok", "settled_positions",
                            "open_positions", "realized_pnl_usdc" },
    "calibration_refresh":{ "last_run", "code", "training_rows",
                            "stations_calibrated", "median_station_mae_c" },
    "dashboard_server":   { "last_run", "code", "host", "port", "uptime_s" }
  },
  "portfolio": {
    "bankroll_usdc": 100.0,
    "open_positions": 8,
    "settled_positions": 12,
    "win_rate_pct": 75.0,
    "realized_pnl_usdc": -1.25,
    "drawdown_triggered": false
  },
  "alerts": [
    "2026-05-12T22:00:12Z daily_open_trades: opened 8 positions, staked $2.00"
  ]
}
```

`alerts` is the most recent 50 events (newest first). Drawdown trigger,
collector failures, etc. show up here.

---

## How to check state in 30 seconds

From the mac:

```bash
ssh -i ~/.ssh/poly_collector_ed25519 wopipsy@100.105.99.20 \
  "type C:\\poly\\detect-temperature\\status\\health.json"
```

Or pipe to `jq` if installed:

```bash
ssh ... "type C:\\poly\\detect-temperature\\status\\health.json" | jq .portfolio
ssh ... "type C:\\poly\\detect-temperature\\status\\health.json" | jq '.alerts[0:5]'
```

Scheduler-level status (did the task actually fire?):

```powershell
Get-ScheduledTask -TaskName "Polymarket*" | ForEach-Object {
  $i = Get-ScheduledTaskInfo -TaskName $_.TaskName
  "{0,-32} state={1,-6} last={2}  code={3}" -f $_.TaskName, $_.State, $i.LastRunTime, $i.LastTaskResult
}
```

`LastTaskResult`:
- `0` — completed cleanly
- `267009` — "task is currently running" (can appear for a short-lived task)
- `267011` — never run yet (`PolymarketCalibrationRefresh` before first Monday, etc.)
- any other value — look at the matching log

---

## Life cycle of one paper position

```
  22:00 UTC  scan -> predict -> signals -> strategy-lab
             -> open_strategy_paper_portfolio
             -> artifacts/paper_portfolio.csv  (status=open)
             -> status/health.json.daily_open_trades.last_run updated

  22:05…00:59  collector keeps snapshotting every 5 min (data/history)

  01:00–04:30 UTC (every 30 min, 8 firings)
             refresh_open_positions
             - fetch intraday temperature for (station, today)
             - compute refined_fair using observed + remaining-hours sigma shrink
             - if refined_fair<=0.02 -> status=lost, PnL booked
             - if refined_fair>=0.98 -> status=won, PnL booked
             - if refined_edge<0      -> status=at_risk (still open)

  06:00 UTC  collect_actuals (lag=1 day) -> settle_paper_trades
             - any remaining open positions with observed_resolution_value
               get status=won/lost and PnL
             - status/health.json.daily_settle + portfolio updated
```

---

## What to check if something looks wrong

| Symptom | First thing to read |
|---|---|
| `status/health.json` old timestamp | Is the PC on? `Test-Connection 100.105.99.20` from mac. |
| `collector_regular.code != 0` | `logs/collector.log` last 50 lines. Scan/ratelimit? |
| `daily_open_trades.code == 3` | Drawdown kill-switch tripped. `alerts[0]` explains. Do not retry blindly. |
| `daily_open_trades.code == 2` | Pipeline error — `logs/daily_open_trades.log` has the traceback. |
| `near_close_refresh.at_risk` growing | Intraday observations diverging from forecast. Look at `artifacts/paper_portfolio.csv` `refined_fair_probability` column. |
| `actuals_error > 0` | Source API down. Usually transient. Check `logs/daily_settle.log`. |
| `portfolio.drawdown_triggered == true` | Stop. Investigate manually before restarting `daily_open_trades`. |

---

## How to stop / restart tasks (PowerShell, Admin)

```powershell
# pause one task (e.g. while you investigate)
Disable-ScheduledTask -TaskName PolymarketDailyOpenTrades

# resume
Enable-ScheduledTask  -TaskName PolymarketDailyOpenTrades

# force a manual run (does not affect schedule)
Start-ScheduledTask   -TaskName PolymarketDailyOpenTrades

# full nuke & re-register
powershell -ExecutionPolicy Bypass -File C:\poly\detect-temperature\scripts\register_windows_scheduler.ps1
powershell -ExecutionPolicy Bypass -File C:\poly\detect-temperature\scripts\register_daily_tasks.ps1
powershell -ExecutionPolicy Bypass -File C:\poly\detect-temperature\scripts\register_calibration_refresh.ps1
```

Tasks are idempotent: re-running the registration scripts replaces
definitions in-place.

---

## Keep the PC awake

A laptop default power scheme lets Windows enter Modern Standby after idle,
which silently skips some Task Scheduler firings. Observed in our logs:
hourly snapshot count dropped from 12/h to 5–10/h between 03:00 and 10:00,
even though the PC was never fully suspended.

Apply once, admin PowerShell:

```powershell
# never sleep / hibernate / monitor-off on AC and DC
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 0

# lid close does nothing (do not suspend when closing the lid)
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

# disable hibernation file entirely
powercfg /hibernate off

# allow Task Scheduler to wake the machine if Modern Standby still kicks in
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT
```

Then mark each Polymarket task as `WakeToRun=True` and allow battery:

```powershell
foreach ($t in @('PolymarketCollectorRegular','PolymarketCollectorHot',
                 'PolymarketDailyOpenTrades','PolymarketNearCloseRefresh',
                 'PolymarketDailySettle','PolymarketCalibrationRefresh',
                 'PolymarketDashboardServer')) {
  $task = Get-ScheduledTask -TaskName $t
  $s = $task.Settings
  $s.WakeToRun = $true
  $s.DisallowStartIfOnBatteries = $false
  $s.StopIfGoingOnBatteries = $false
  Set-ScheduledTask -TaskName $t -Settings $s | Out-Null
}
```

Verify:

```powershell
powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
```

Both "AC power index" and "DC power index" should read `0x00000000`.

---

## Reinstall checklist (e.g. new PC)

1. Copy the repo to `C:\poly\detect-temperature`
2. `python -m venv .venv && .venv\Scripts\python -m pip install -e . pytest`
3. Apply the "Keep the PC awake" section above
4. **Re-train the GBM on the target machine**:
   ```
   .venv\Scripts\python -m detect_temperature.cli train-gbm ^
     --training C:\poly\detect-temperature\data\training_real.csv ^
     --model C:\poly\detect-temperature\artifacts\models\gbm.joblib
   ```
   The repo-shipped `gbm.joblib` was pickled with the developer's scikit-learn
   version; loading it under a different minor (e.g. 1.7 vs 1.8) silently
   breaks with `AttributeError: 'SimpleImputer' object has no attribute '_fill_dtype'`.
   The weekly `PolymarketCalibrationRefresh` task would heal this on the
   next Monday anyway, but retraining once at install time avoids a day of
   failing `daily_open_trades` runs.
5. Run the four registration scripts (admin PowerShell):
   - `scripts\register_windows_scheduler.ps1` (collector regular + hot)
   - `scripts\register_daily_tasks.ps1` (daily open + near-close + daily settle)
   - `scripts\register_calibration_refresh.ps1` (weekly retrain)
   - `scripts\register_dashboard_server.ps1` (long-running HTTP server + firewall rule)
6. `Start-ScheduledTask` each one to verify
7. `type status\health.json` — every section should have `last_run`

---

## Links

- `HANDOFF.md` — full architecture, philosophy and module map
- `docs/model_architecture.md` — forecast model details
- `docs/polymarket_weather_strategy.md` — strategy design
- `docs/weather_risk_investigation_2026-05-05.md` — risk discussion
- Sources of truth in code:
  - `src/detect_temperature/status.py` — health writer
  - `src/detect_temperature/paper_server.py:run_open_trades_pipeline` — what the daily open does
  - `src/detect_temperature/paper_server.py:refresh_paper_state` — what the daily settle does
  - `src/detect_temperature/pipeline.py:refresh_open_positions` — what the near-close refresh does
  - `scripts/windows_collector.py` — what the regular + hot collectors do
