from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class MarketTarget:
    title: str
    slug: str
    city: str
    location_name: str
    target_date: date | None
    target_extreme: str
    target_unit: str
    station_id: str
    resolution_source_url: str
    source_domain: str
    description: str = ""

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["target_date"] = self.target_date.isoformat() if self.target_date else ""
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "MarketTarget":
        raw_date = record.get("target_date") or None
        parsed_date = date.fromisoformat(raw_date) if raw_date else None
        return cls(
            title=record.get("title", ""),
            slug=record.get("slug", ""),
            city=record.get("city", ""),
            location_name=record.get("location_name", ""),
            target_date=parsed_date,
            target_extreme=record.get("target_extreme", "unknown"),
            target_unit=record.get("target_unit", "unknown"),
            station_id=record.get("station_id", ""),
            resolution_source_url=record.get("resolution_source_url", ""),
            source_domain=record.get("source_domain", ""),
            description=record.get("description", ""),
        )


@dataclass(frozen=True)
class Prediction:
    slug: str
    station_id: str
    target_date: date | None
    target_extreme: str
    prediction_c: float | None
    model_name: str
    created_at: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "station_id": self.station_id,
            "target_date": self.target_date.isoformat() if self.target_date else "",
            "target_extreme": self.target_extreme,
            "prediction_c": self.prediction_c,
            "model_name": self.model_name,
            "created_at": self.created_at.isoformat(timespec="seconds"),
        }

