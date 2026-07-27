from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
import json
import math


@dataclass(frozen=True)
class AirportLocation:
    airport_code: str
    latitude: float
    longitude: float
    city: str = ""
    country: str = ""


@dataclass(frozen=True)
class WeatherSnapshot:
    airport_code: str
    observed_at: datetime
    temperature_c: float | None
    precipitation_mm: float | None
    wind_speed_kph: float | None
    weather_code: int | None
    storm_likelihood: float
    storm_risk: bool
    source: str = "open-meteo"
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class CongestionSnapshot:
    airport_code: str
    time_window_minutes: int
    congestion_score: float
    label: str
    rationale: str
    source: str = "heuristic"


@dataclass(frozen=True)
class CarrierOtpSnapshot:
    carrier: str
    route: str
    on_time_probability: float
    recent_delay_risk: float
    confidence: float
    source: str = "heuristic"


AIRPORT_DIRECTORY: dict[str, AirportLocation] = {
    "JFK": AirportLocation("JFK", 40.6413, -73.7781, city="New York", country="US"),
    "LGA": AirportLocation("LGA", 40.7769, -73.8740, city="New York", country="US"),
    "EWR": AirportLocation("EWR", 40.6895, -74.1745, city="Newark", country="US"),
    "LHR": AirportLocation("LHR", 51.4700, -0.4543, city="London", country="GB"),
    "DXB": AirportLocation("DXB", 25.2532, 55.3657, city="Dubai", country="AE"),
    "DEL": AirportLocation("DEL", 28.5562, 77.1000, city="Delhi", country="IN"),
    "BOM": AirportLocation("BOM", 19.0896, 72.8656, city="Mumbai", country="IN"),
    "SIN": AirportLocation("SIN", 1.3644, 103.9915, city="Singapore", country="SG"),
    "HKG": AirportLocation("HKG", 22.3080, 113.9185, city="Hong Kong", country="HK"),
    "ORD": AirportLocation("ORD", 41.9742, -87.9073, city="Chicago", country="US"),
    "DFW": AirportLocation("DFW", 32.8998, -97.0403, city="Dallas", country="US"),
    "ATL": AirportLocation("ATL", 33.6407, -84.4277, city="Atlanta", country="US"),
    "SFO": AirportLocation("SFO", 37.6213, -122.3790, city="San Francisco", country="US"),
}


class AirportLookupError(LookupError):
    pass


def get_airport_location(airport_code: str) -> AirportLocation:
    normalized = airport_code.strip().upper()
    try:
        return AIRPORT_DIRECTORY[normalized]
    except KeyError as exc:
        raise AirportLookupError(f"No airport coordinates registered for {normalized}") from exc


class OpenMeteoWeatherClient:
    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds

    def get_weather(self, airport_code: str) -> WeatherSnapshot:
        location = get_airport_location(airport_code)
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={location.latitude}&longitude={location.longitude}"
            "&current=temperature_2m,precipitation,wind_speed_10m,weather_code"
        )
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        current = payload.get("current", {}) or {}
        weather_code = current.get("weather_code")
        precipitation = current.get("precipitation")
        wind_speed = current.get("wind_speed_10m")
        likelihood = _storm_likelihood(weather_code, precipitation, wind_speed)

        return WeatherSnapshot(
            airport_code=location.airport_code,
            observed_at=datetime.now(timezone.utc),
            temperature_c=current.get("temperature_2m"),
            precipitation_mm=precipitation,
            wind_speed_kph=wind_speed,
            weather_code=weather_code,
            storm_likelihood=likelihood,
            storm_risk=likelihood >= 0.65,
            raw=payload,
        )


def _storm_likelihood(weather_code: int | None, precipitation_mm: float | None, wind_speed_kph: float | None) -> float:
    score = 0.0
    if weather_code in {95, 96, 99}:
        score += 0.85
    elif weather_code in {80, 81, 82}:
        score += 0.35
    elif weather_code in {71, 73, 75, 77, 85, 86}:
        score += 0.20

    if precipitation_mm is not None:
        score += min(0.35, precipitation_mm / 20.0)
    if wind_speed_kph is not None:
        score += min(0.25, max(0.0, wind_speed_kph - 35.0) / 100.0)

    return min(score, 1.0)


class AirportCongestionEstimator:
    """Heuristic congestion estimator used when a live airport ops feed is unavailable."""

    _hub_baseline = {
        "JFK": 0.68,
        "LGA": 0.75,
        "EWR": 0.66,
        "LHR": 0.72,
        "DXB": 0.70,
        "ORD": 0.74,
        "DFW": 0.71,
        "ATL": 0.76,
        "SFO": 0.67,
    }

    def get_airport_congestion(self, airport_code: str, time_window_minutes: int, observed_at: datetime | None = None) -> CongestionSnapshot:
        normalized = airport_code.strip().upper()
        current_time = observed_at or datetime.now(timezone.utc)
        base = self._hub_baseline.get(normalized, 0.50)
        hour = current_time.hour + current_time.minute / 60.0
        rush_factor = 0.18 if 6 <= hour <= 10 or 16 <= hour <= 20 else 0.05
        window_factor = min(0.12, time_window_minutes / 600.0)
        score = min(1.0, base + rush_factor + window_factor)
        label = "high" if score >= 0.75 else "moderate" if score >= 0.5 else "low"
        rationale = f"Baseline {base:.2f}, rush {rush_factor:.2f}, window {window_factor:.2f}"
        return CongestionSnapshot(normalized, time_window_minutes, score, label, rationale)


class CarrierOtpEstimator:
    _carrier_baseline = {
        "AI": 0.76,
        "AA": 0.79,
        "DL": 0.83,
        "UA": 0.81,
        "BA": 0.80,
        "EK": 0.85,
        "QF": 0.84,
        "SQ": 0.86,
        "LH": 0.82,
    }

    _route_adjustments = {
        "JFK-LHR": -0.03,
        "JFK-DXB": -0.05,
        "LHR-DXB": -0.02,
        "DEL-JFK": -0.06,
    }

    def get_carrier_otp_stats(self, carrier: str, route: str) -> CarrierOtpSnapshot:
        normalized_carrier = carrier.strip().upper()
        normalized_route = route.strip().upper()
        base = self._carrier_baseline.get(normalized_carrier, 0.78)
        adjustment = self._route_adjustments.get(normalized_route, 0.0)
        on_time_probability = _clamp(base + adjustment, 0.55, 0.95)
        recent_delay_risk = 1.0 - on_time_probability
        confidence = 0.35 if normalized_carrier not in self._carrier_baseline else 0.65
        return CarrierOtpSnapshot(normalized_carrier, normalized_route, on_time_probability, recent_delay_risk, confidence)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
