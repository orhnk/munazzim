from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class GeoLocation:
    latitude: float
    longitude: float
    city: str
    country: str
    timezone: str

    def as_tuple(self) -> tuple[float, float]:
        return self.latitude, self.longitude


class GeoLocator:
    """Resolve the user's approximate location via an IP geolocation service."""

    def __init__(self, endpoint: str = "https://ipapi.co/json/", timeout: float = 5.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def detect(self) -> GeoLocation | None:
        try:
            response = httpx.get(self.endpoint, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        data: dict[str, Any] = response.json()
        lat = data.get("latitude") or data.get("lat")
        lon = data.get("longitude") or data.get("lon")
        timezone = data.get("timezone") or data.get("time_zone")
        if lat is None or lon is None or timezone is None:
            return None
        city = data.get("city") or data.get("region") or ""
        country = data.get("country_name") or data.get("country") or ""
        try:
            return GeoLocation(
                latitude=float(lat),
                longitude=float(lon),
                city=str(city),
                country=str(country),
                timezone=str(timezone),
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return None
