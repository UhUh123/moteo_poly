"""Live verification that status.update_task drops stale fields correctly.

Runs entirely on a tmp file so the real health.json is untouched.
Exits non-zero if any check fails — wrap with `&& echo OK` in shell
to confirm.
"""
import sys
import tempfile
from pathlib import Path

from detect_temperature.status import update_task, load_health


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "h.json"

        # 1. Success establishes some success-side fields.
        update_task(
            "collector_metar",
            {"code": 0, "outcome": "snapshot", "rows_appended": 27,
             "stations_requested": 51, "days_touched": ["2026-05-18"]},
            path=p,
        )
        s = load_health(p)["tasks"]["collector_metar"]
        print(f"after success: code={s['code']} outcome={s.get('outcome')} "
              f"rows_appended={s.get('rows_appended')} "
              f"stations_requested={s.get('stations_requested')}")
        assert s["code"] == 0
        assert s["outcome"] == "snapshot"
        assert s["rows_appended"] == 27

        # 2. Error must wipe success-side fields.
        update_task("collector_metar", {"code": 2, "error": "DNS failed"}, path=p)
        s = load_health(p)["tasks"]["collector_metar"]
        print(f"after error:   code={s['code']} outcome={s.get('outcome')!r} "
              f"rows_appended={s.get('rows_appended')!r} "
              f"error={s.get('error')!r}")
        assert s["code"] == 2
        assert s["error"] == "DNS failed"
        for stale in ("outcome", "rows_appended", "stations_requested", "days_touched"):
            assert stale not in s, f"{stale} leaked into error state"

        # 3. Recovery must wipe error-side fields.
        update_task(
            "collector_metar",
            {"code": 0, "outcome": "snapshot", "rows_appended": 5},
            path=p,
        )
        s = load_health(p)["tasks"]["collector_metar"]
        print(f"after recover: code={s['code']} outcome={s.get('outcome')} "
              f"error={s.get('error')!r}")
        assert s["code"] == 0
        assert s["outcome"] == "snapshot"
        assert "error" not in s, "error string survived recovery"

        # 4. Two consecutive errors -> second replaces first.
        update_task(
            "collector_metar",
            {"code": 2, "error": "first failure", "diagnostic": "detail-A"},
            path=p,
        )
        update_task(
            "collector_metar",
            {"code": 3, "error": "second failure"},
            path=p,
        )
        s = load_health(p)["tasks"]["collector_metar"]
        print(f"after 2nd err: code={s['code']} error={s.get('error')!r} "
              f"diagnostic={s.get('diagnostic')!r}")
        assert s["code"] == 3
        assert s["error"] == "second failure"
        assert "diagnostic" not in s, "stale diagnostic from prior error survived"

        # 5. Two consecutive successes -> merge (partial-update preserves
        #    fields from earlier success).
        update_task(
            "collector_metar",
            {"code": 0, "outcome": "snapshot", "rows_appended": 30,
             "stations_requested": 51},
            path=p,
        )
        update_task(
            "collector_metar",
            {"code": 0, "rows_appended": 12},  # only this changes
            path=p,
        )
        s = load_health(p)["tasks"]["collector_metar"]
        print(f"after merge:   code={s['code']} outcome={s.get('outcome')} "
              f"rows_appended={s.get('rows_appended')} "
              f"stations_requested={s.get('stations_requested')}")
        assert s["outcome"] == "snapshot", "merge must keep outcome from earlier success"
        assert s["stations_requested"] == 51, "merge must keep stations_requested"
        assert s["rows_appended"] == 12, "merge must apply update"

    print("ALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
