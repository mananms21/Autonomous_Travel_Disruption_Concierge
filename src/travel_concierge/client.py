from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json


@dataclass(frozen=True)
class AviationstackClient:
    """Small Aviationstack adapter for monitoring live flight status."""

    access_key: str
    base_url: str = "https://api.aviationstack.com/v1"
    timeout_seconds: int = 20

    def get_flights(self, **query_params: Any) -> dict[str, Any]:
        params: dict[str, str] = {"access_key": self.access_key}
        for key, value in query_params.items():
            if value is None:
                continue
            if isinstance(value, date):
                params[key] = value.isoformat()
            else:
                params[key] = str(value)

        url = f"{self.base_url.rstrip('/')}/flights?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json"})

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"Aviationstack request failed with HTTP {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"Aviationstack request failed: {exc.reason}") from exc

    def get_flight(self, *, flight_iata: str | None = None, flight_icao: str | None = None, flight_date: date | None = None) -> dict[str, Any]:
        query_params: dict[str, Any] = {"flight_iata": flight_iata, "flight_icao": flight_icao, "flight_date": flight_date}
        if flight_iata:
            query_params["flight_iata"] = flight_iata
        if flight_icao:
            query_params["flight_icao"] = flight_icao
        if flight_date:
            query_params["flight_date"] = flight_date

        if not query_params.get("flight_iata") and not query_params.get("flight_icao"):
            raise ValueError("Either flight_iata or flight_icao is required")

        return self.get_flights(**query_params)

    def fetch_status_snapshot(self, flight_iata: str, flight_date: date | None = None) -> list[dict[str, Any]]:
        response = self.get_flight(flight_iata=flight_iata, flight_date=flight_date)
        return list(response.get("data", []))


def parse_aviationstack_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed