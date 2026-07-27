from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .client import AviationstackClient, parse_aviationstack_datetime


@dataclass(frozen=True)
class FlightSearchResult:
    flight_iata: str | None
    flight_icao: str | None
    airline: str | None
    departure_airport: str | None
    departure_terminal: str | None
    departure_time: datetime | None
    arrival_airport: str | None
    arrival_terminal: str | None
    arrival_time: datetime | None
    status: str | None
    raw: dict[str, Any]


def search_routes(client: AviationstackClient, *, departure_iata: str, arrival_iata: str, flight_date: date | None = None, max_results: int = 10) -> list[FlightSearchResult]:
    """Search flights for a route using Aviationstack's flights endpoint."""

    response = client.get_flights(dep_iata=departure_iata, arr_iata=arrival_iata, flight_date=flight_date)
    data = list(response.get("data", []))
    matches: list[FlightSearchResult] = []

    for record in data:
        departure = record.get("departure", {}) or {}
        arrival = record.get("arrival", {}) or {}

        if str(departure.get("iata") or "").upper() != departure_iata.upper():
            continue
        if str(arrival.get("iata") or "").upper() != arrival_iata.upper():
            continue

        flight = record.get("flight", {}) or {}
        airline = record.get("airline")
        airline_name = airline.get("name") if isinstance(airline, dict) else airline
        matches.append(
            FlightSearchResult(
                flight_iata=flight.get("iata"),
                flight_icao=flight.get("icao"),
                airline=airline_name,
                departure_airport=departure.get("iata"),
                departure_terminal=departure.get("terminal"),
                departure_time=parse_aviationstack_datetime(departure.get("scheduled")),
                arrival_airport=arrival.get("iata"),
                arrival_terminal=arrival.get("terminal"),
                arrival_time=parse_aviationstack_datetime(arrival.get("scheduled")),
                status=str(record.get("flight_status") or record.get("status") or "unknown"),
                raw=record,
            )
        )

        if len(matches) >= max_results:
            break

    return matches
