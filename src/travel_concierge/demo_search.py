from __future__ import annotations

from datetime import date
import json
import os

from .client import AviationstackClient
from .search import search_routes


def main() -> None:
    access_key = os.environ.get("AVIATIONSTACK_ACCESS_KEY")
    if not access_key:
        raise SystemExit("Set AVIATIONSTACK_ACCESS_KEY first")

    client = AviationstackClient(access_key=access_key)
    results = search_routes(client, departure_iata="HYD", arrival_iata="GAU", flight_date=date.today(), max_results=10)
    print(json.dumps([result.__dict__ for result in results], default=str, indent=2))


if __name__ == "__main__":
    main()
