from __future__ import annotations


def fahrenheit_to_celsius(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def celsius_to_fahrenheit(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def normalize_temperature(value: float, unit: str) -> float:
    normalized = unit.strip().lower()
    if normalized in {"c", "celsius", "degc", "°c"}:
        return value
    if normalized in {"f", "fahrenheit", "degf", "°f"}:
        return fahrenheit_to_celsius(value)
    raise ValueError(f"Unsupported temperature unit: {unit!r}")

