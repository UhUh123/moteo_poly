from __future__ import annotations

from typing import Any


RISK_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "default": {},
    "bankroll_100": {
        "build-market-signals": {
            "bankroll_usdc": 100.0,
            "sigma_c": 2.5,
            "min_edge": 0.05,
            "min_yes_probability": 0.12,
            "min_no_probability": 0.70,
            "max_spread": 0.03,
            "min_liquidity": 500.0,
            "allow_buy_yes": False,
        },
        "open-paper-trades": {
            "bankroll_usdc": 100.0,
            "min_edge": 0.08,
            "max_positions": 60,
            "max_stake_usdc": 0.25,
            "max_total_exposure_pct": 0.30,
            "max_event_exposure_pct": 0.01,
            "min_price": 0.02,
            "max_price": 0.90,
        },
        "run-strategy-lab": {
            "bankroll_usdc": 100.0,
            "max_positions": 60,
            "max_stake_usdc": 0.25,
            "max_total_exposure_pct": 0.30,
            "max_event_exposure_pct": 0.01,
            "max_event_positions": 1,
            "max_city_positions": 2,
            "max_city_exposure_pct": 0.01,
            "max_date_exposure_pct": 0.03,
            "max_extreme_exposure_pct": 0.10,
            "min_price": 0.02,
            "max_price": 0.90,
            "robust_min_edge": 0.10,
            "min_scenario_pass_rate": 1.0,
            "max_execution_slippage": 0.01,
            "maker_min_fill_score": 0.55,
            "maker_adverse_selection_penalty": 0.02,
            "mean_shifts_c": "-2.0,0,2.0",
            "sigma_values_c": "2.5,3.0,3.5",
            "slippage_values": "0,0.01,0.02",
            "drawdown_abort_usdc": -10.0,
        },
        "open-strategy-paper-trades": {
            "bankroll_usdc": 100.0,
            "max_positions": 60,
            "execution_mode": "taker",
            "drawdown_abort_usdc": -10.0,
        },
    },
}


def profile_names() -> tuple[str, ...]:
    return tuple(RISK_PROFILES)


def risk_profile_values(profile_name: str, command: str) -> dict[str, Any]:
    if profile_name == "default":
        return {}
    try:
        profile = RISK_PROFILES[profile_name]
    except KeyError as exc:
        raise ValueError(f"unknown risk profile: {profile_name}") from exc
    return dict(profile.get(command, {}))
