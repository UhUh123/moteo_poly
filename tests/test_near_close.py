from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from detect_temperature.near_close import (
    NearCloseInput,
    hours_remaining_until,
    refined_bucket_probability,
    shrink_sigma,
)


def test_no_observation_matches_plain_forecast() -> None:
    spec = NearCloseInput(
        target_extreme="max",
        mean_c=22.0,
        sigma_c=2.5,
        lower_c=21.5,
        upper_c=22.5,
        observed_so_far_c=None,
        hours_remaining=24.0,
    )
    assert 0.1 < refined_bucket_probability(spec) < 0.25


def test_observed_max_above_bucket_zero_probability() -> None:
    spec = NearCloseInput(
        target_extreme="max",
        mean_c=22.0,
        sigma_c=2.5,
        lower_c=21.5,
        upper_c=22.5,
        observed_so_far_c=23.0,  # we already saw 23 — can never land in [21.5, 22.5]
        hours_remaining=1.0,
    )
    assert refined_bucket_probability(spec) == 0.0


def test_observed_max_inside_bucket_collapses_to_stay_below_upper() -> None:
    spec = NearCloseInput(
        target_extreme="max",
        mean_c=22.0,
        sigma_c=2.5,
        lower_c=21.5,
        upper_c=22.5,
        observed_so_far_c=22.0,  # already in-bucket
        hours_remaining=1.0,
    )
    # With mean=22 right in the middle of [21.5, 22.5] and sigma shrunk to
    # sqrt(1/24) * 2.5 ≈ 0.5 (floored at 0.625), P(future <= 22.5) is ~CDF(0.8) ≈ 0.79.
    # The test asserts the "already in bucket → high chance to stay" direction.
    p = refined_bucket_probability(spec)
    assert p > 0.75


def test_observed_min_below_bucket_zero_probability() -> None:
    spec = NearCloseInput(
        target_extreme="min",
        mean_c=10.0,
        sigma_c=2.0,
        lower_c=10.0,
        upper_c=11.0,
        observed_so_far_c=8.5,  # already saw 8.5 — final min will be <= 8.5 < 10
        hours_remaining=1.0,
    )
    assert refined_bucket_probability(spec) == 0.0


def test_observed_min_inside_bucket_collapses_to_stay_above_lower() -> None:
    spec = NearCloseInput(
        target_extreme="min",
        mean_c=11.0,      # forecast sits mid-bucket, not on the edge
        sigma_c=2.0,
        lower_c=10.0,
        upper_c=12.0,
        observed_so_far_c=10.5,  # already inside, above lower
        hours_remaining=1.0,
    )
    # sigma shrinks to sqrt(1/24)*2 ≈ 0.408, floored at 0.5. P(future >= 10 | mean=11, sigma=0.5)
    # ≈ CDF(2.0) ≈ 0.977 — close to certainty we stay above the lower edge.
    p = refined_bucket_probability(spec)
    assert p > 0.9


def test_shrink_sigma_floor() -> None:
    # Full day: sigma preserved.
    assert shrink_sigma(2.5, 24.0) == pytest.approx(2.5)
    # 6 hours remaining -> sqrt(0.25) = 0.5 of full sigma
    assert shrink_sigma(2.5, 6.0) == pytest.approx(1.25, rel=1e-3)
    # near close: floor of 0.25 * sigma prevents degenerate collapse
    assert shrink_sigma(2.5, 0.1) == pytest.approx(0.625, rel=1e-3)


def test_refined_prob_goes_up_as_close_approaches_when_obs_inside_bucket() -> None:
    # Same observation, different hours_remaining -> closer to close should
    # give higher confidence that we stay inside the bucket.
    spec_24 = NearCloseInput(
        target_extreme="max",
        mean_c=22.0,
        sigma_c=2.5,
        lower_c=21.5,
        upper_c=22.5,
        observed_so_far_c=22.0,
        hours_remaining=24.0,
    )
    spec_1 = NearCloseInput(**{**spec_24.__dict__, "hours_remaining": 1.0})
    assert refined_bucket_probability(spec_1) > refined_bucket_probability(spec_24)


def test_hours_remaining_clamp() -> None:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    future_close = datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc)
    assert hours_remaining_until(future_close, now) == pytest.approx(3.5, abs=0.01)
    past_close = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    assert hours_remaining_until(past_close, now) == 0.0
    # naive datetime coerced to UTC
    naive = datetime(2026, 5, 11, 14, 0)
    assert hours_remaining_until(naive, now) == pytest.approx(2.0, abs=0.01)
    # clamp to 48h
    way_future = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    assert hours_remaining_until(way_future, now) == 48.0
