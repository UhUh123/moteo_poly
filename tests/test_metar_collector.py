from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from detect_temperature.sources.metar_collector import (
    CSV_COLUMNS,
    collect_metar_snapshot,
    load_station_ids,
)


def _kjfk_payload(temp=22.0, ts="2026-05-16T12:51:00Z", rep="METAR"):
    return {
        "icaoId": "KJFK",
        "obsTime": ts,
        "reportTime": ts,
        "metarType": rep,
        "temp": temp,
        "dewp": 14.0,
        "wdir": 210,
        "wspd": 8,
        "altim": 1015.0,
        "visib": "10+",
        "rawOb": f"KJFK 161251Z 21008KT 10SM FEW250 22/14 A2997",
    }


def test_collect_basic_writes_one_row(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"

    summary = collect_metar_snapshot(
        ["KJFK"],
        history_root=history,
        fetcher=lambda ids: [_kjfk_payload()],
        now_utc=datetime(2026, 5, 16, 12, 55, tzinfo=timezone.utc),
    )

    assert summary["requested"] == 1
    assert summary["received"] == 1
    assert summary["appended"] == 1

    target = history / "2026-05-16.csv"
    assert target.exists()
    rows = list(csv.DictReader(target.open(newline="", encoding="utf-8")))
    assert len(rows) == 1
    r = rows[0]
    assert r["station_id"] == "KJFK"
    assert r["observed_at"] == "2026-05-16T12:51:00+00:00"
    assert r["report_type"] == "METAR"
    assert r["temp_c"] == "22.0"
    assert r["wind_dir_deg"] == "210"
    assert r["wind_speed_kt"] == "8"
    assert r["raw_text"].startswith("KJFK 161251Z")
    # All columns must be present in order, even if some are empty
    assert list(rows[0].keys()) == CSV_COLUMNS


def test_collect_deduplicates_repeated_observation(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    payload = _kjfk_payload()

    first = collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [payload])
    second = collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [payload])

    assert first["appended"] == 1
    assert second["appended"] == 0  # same (station, observed_at) -> no second row
    rows = list(csv.DictReader((history / "2026-05-16.csv").open(newline="", encoding="utf-8")))
    assert len(rows) == 1


def test_collect_appends_when_observed_at_changes(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"

    p1 = _kjfk_payload(temp=22.0, ts="2026-05-16T12:51:00Z")
    p2 = _kjfk_payload(temp=23.5, ts="2026-05-16T13:51:00Z")

    collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [p1])
    collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [p2])

    rows = list(csv.DictReader((history / "2026-05-16.csv").open(newline="", encoding="utf-8")))
    assert len(rows) == 2
    assert {r["temp_c"] for r in rows} == {"22.0", "23.5"}


def test_collect_groups_by_utc_day_across_midnight(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    payload_eve = _kjfk_payload(ts="2026-05-16T23:51:00Z")
    payload_morn = _kjfk_payload(ts="2026-05-17T00:51:00Z")

    summary = collect_metar_snapshot(
        ["KJFK"], history_root=history,
        fetcher=lambda ids: [payload_eve, payload_morn],
    )

    assert summary["appended"] == 2
    assert sorted(summary["days_touched"]) == ["2026-05-16", "2026-05-17"]
    assert (history / "2026-05-16.csv").exists()
    assert (history / "2026-05-17.csv").exists()


def test_collect_handles_epoch_obstime(tmp_path: Path) -> None:
    """The endpoint sometimes returns obsTime as epoch seconds."""
    history = tmp_path / "metar_history"
    epoch = int(datetime(2026, 5, 16, 12, 51, tzinfo=timezone.utc).timestamp())
    payload = {**_kjfk_payload(), "obsTime": epoch}

    summary = collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [payload])
    assert summary["appended"] == 1
    rows = list(csv.DictReader((history / "2026-05-16.csv").open(newline="", encoding="utf-8")))
    assert rows[0]["observed_at"] == "2026-05-16T12:51:00+00:00"


def test_collect_drops_payload_without_obstime(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    bad = {**_kjfk_payload(), "obsTime": None, "reportTime": None, "receiptTime": None}

    summary = collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [bad])
    assert summary["received"] == 0
    assert summary["appended"] == 0
    assert not (history / "2026-05-16.csv").exists()


def test_collect_drops_payload_without_station(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    bad = {**_kjfk_payload()}
    bad.pop("icaoId")

    summary = collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [bad])
    assert summary["received"] == 0


def test_collect_returns_zero_for_empty_station_list(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    summary = collect_metar_snapshot([], history_root=history, fetcher=lambda ids: [])
    assert summary == {"requested": 0, "received": 0, "appended": 0, "stations": []}
    assert not history.exists()


def test_collect_normalizes_station_ids(tmp_path: Path) -> None:
    history = tmp_path / "metar_history"
    captured: list[list[str]] = []

    def fake(ids):
        captured.append(ids)
        return [_kjfk_payload()]

    collect_metar_snapshot(["  kjfk ", "KLAX", "kjfk", ""], history_root=history, fetcher=fake)
    # Expect uppercased, deduped, sorted, no empty strings
    assert captured == [["KJFK", "KLAX"]]


def test_collect_handles_corrupt_existing_csv(tmp_path: Path) -> None:
    """If the daily file is unreadable we must NOT lose the observations
    from this fetch. Old file gets renamed to .broken, fresh file written."""
    history = tmp_path / "metar_history"
    history.mkdir()
    target = history / "2026-05-16.csv"
    # Corrupt CSV with embedded null byte
    target.write_bytes(b"\x00not,a,csv\xff\xfe\nbroken\n")

    summary = collect_metar_snapshot(
        ["KJFK"], history_root=history,
        fetcher=lambda ids: [_kjfk_payload()],
    )

    # The corrupt file may parse as one row of garbage; what matters is that
    # the new observation got persisted afterwards. Verify both invariants:
    assert target.exists()
    new_rows = list(csv.DictReader(target.open(newline="", encoding="utf-8")))
    fetched_rows = [r for r in new_rows if r.get("station_id") == "KJFK"]
    assert len(fetched_rows) == 1
    assert fetched_rows[0]["temp_c"] == "22.0"


def test_collect_atomic_write_uses_tmp_file(tmp_path: Path, monkeypatch) -> None:
    """The write path must go through a .tmp staging file before rename.
    This guards against half-written CSVs if the process is killed mid-write."""
    history = tmp_path / "metar_history"
    seen_paths: list[str] = []

    real_replace = __import__("os").replace
    def tracking_replace(src, dst):
        seen_paths.append(str(src))
        seen_paths.append(str(dst))
        return real_replace(src, dst)
    monkeypatch.setattr("os.replace", tracking_replace)

    collect_metar_snapshot(["KJFK"], history_root=history, fetcher=lambda ids: [_kjfk_payload()])

    assert any(p.endswith(".tmp") for p in seen_paths), "write must stage through .tmp"
    assert any(p.endswith("2026-05-16.csv") for p in seen_paths), "final file must be the dated CSV"


def test_load_station_ids_reads_training_stations_json(tmp_path: Path) -> None:
    path = tmp_path / "stations.json"
    path.write_text(json.dumps([
        {"id": "KJFK", "lat": 40.6, "lon": -73.8},
        {"id": "klax"},
        {"id": ""},
        {"no_id_here": True},
    ]), encoding="utf-8")

    ids = load_station_ids(path)
    assert ids == ["KJFK", "KLAX"]


def test_load_station_ids_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert load_station_ids(tmp_path / "nope.json") == []


# ---- retry behaviour --------------------------------------------------------
#
# Why these tests matter: on Windows the household ISP DNS occasionally fails
# to resolve aviationweather.gov for minutes at a time. Without retries even a
# 200ms flap turns one collection cycle into a complete miss; ten minutes
# later the next cycle picks up only the *latest* observation per station,
# permanently losing the cycle that failed. With retries we bridge most flaps.


def _make_fake_response(*, status: int = 200, body=None):
    class _Resp:
        status_code = status

        def raise_for_status(self):
            if 400 <= self.status_code < 600:
                from requests import HTTPError
                raise HTTPError(f"{self.status_code} error")

        def json(self):
            return body or []

    return _Resp()


def test_default_fetcher_retries_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    """ConnectionError on attempt 1 (DNS failure), succeed on attempt 2.
    Sleeps must be invoked between attempts but with our injected sleeper
    so the test stays instant."""
    import requests as _requests
    from detect_temperature.sources import metar_collector as mc

    attempts = {"calls": 0, "sleeps": []}

    def fake_get(url, **kw):
        attempts["calls"] += 1
        if attempts["calls"] == 1:
            raise _requests.exceptions.ConnectionError("Failed to resolve 'aviationweather.gov'")
        return _make_fake_response(status=200, body=[_kjfk_payload()])

    monkeypatch.setattr(mc.requests, "get", fake_get)

    summary = collect_metar_snapshot(
        ["KJFK"],
        history_root=tmp_path / "metar_history",
        sleeper=lambda s: attempts["sleeps"].append(s),
        retries=3,
        backoff_s=0.1,
    )

    assert attempts["calls"] == 2
    assert attempts["sleeps"] == [0.1]
    assert summary["received"] == 1
    assert summary["appended"] == 1


def test_default_fetcher_gives_up_after_all_retries(tmp_path: Path, monkeypatch) -> None:
    """Persistent DNS failure: 1 + retries=3 attempts, then raise."""
    import requests as _requests
    from detect_temperature.sources import metar_collector as mc

    attempts = {"calls": 0, "sleeps": []}

    def fake_get(url, **kw):
        attempts["calls"] += 1
        raise _requests.exceptions.ConnectionError("DNS down")

    monkeypatch.setattr(mc.requests, "get", fake_get)

    with pytest.raises(_requests.exceptions.ConnectionError):
        collect_metar_snapshot(
            ["KJFK"],
            history_root=tmp_path / "metar_history",
            sleeper=lambda s: attempts["sleeps"].append(s),
            retries=3,
            backoff_s=0.1,
        )

    assert attempts["calls"] == 4, "1 initial + 3 retries"
    # exponential backoff: 0.1, 0.2, 0.4
    assert attempts["sleeps"] == pytest.approx([0.1, 0.2, 0.4])


def test_default_fetcher_does_not_retry_on_4xx(tmp_path: Path, monkeypatch) -> None:
    """A 400/404 means the request itself is wrong (e.g. unknown station
    code). Retrying would just hammer the gov endpoint for nothing."""
    from detect_temperature.sources import metar_collector as mc

    attempts = {"calls": 0}

    def fake_get(url, **kw):
        attempts["calls"] += 1
        return _make_fake_response(status=404)

    monkeypatch.setattr(mc.requests, "get", fake_get)

    from requests import HTTPError
    with pytest.raises(HTTPError):
        collect_metar_snapshot(
            ["KJFK"],
            history_root=tmp_path / "metar_history",
            sleeper=lambda s: None,
            retries=3,
            backoff_s=0.1,
        )

    assert attempts["calls"] == 1, "4xx must NOT retry"


def test_default_fetcher_retries_on_5xx(tmp_path: Path, monkeypatch) -> None:
    """Server flake (502/503) is retryable."""
    from detect_temperature.sources import metar_collector as mc

    attempts = {"calls": 0}

    def fake_get(url, **kw):
        attempts["calls"] += 1
        if attempts["calls"] < 3:
            return _make_fake_response(status=503)
        return _make_fake_response(status=200, body=[_kjfk_payload()])

    monkeypatch.setattr(mc.requests, "get", fake_get)

    summary = collect_metar_snapshot(
        ["KJFK"],
        history_root=tmp_path / "metar_history",
        sleeper=lambda s: None,
        retries=3,
        backoff_s=0.1,
    )

    assert attempts["calls"] == 3
    assert summary["received"] == 1


def test_default_fetcher_retries_on_timeout(tmp_path: Path, monkeypatch) -> None:
    import requests as _requests
    from detect_temperature.sources import metar_collector as mc

    attempts = {"calls": 0}

    def fake_get(url, **kw):
        attempts["calls"] += 1
        if attempts["calls"] == 1:
            raise _requests.exceptions.Timeout("read timeout")
        return _make_fake_response(status=200, body=[_kjfk_payload()])

    monkeypatch.setattr(mc.requests, "get", fake_get)

    summary = collect_metar_snapshot(
        ["KJFK"],
        history_root=tmp_path / "metar_history",
        sleeper=lambda s: None,
    )

    assert attempts["calls"] == 2
    assert summary["received"] == 1
