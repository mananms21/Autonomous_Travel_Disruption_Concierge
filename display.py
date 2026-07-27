"""
Friendly console output for the top-3 ranked options and the final booking
confirmation. Purely presentational — doesn't affect any decision logic,
just makes test.py-style runs readable instead of staring at raw dicts.
"""
from __future__ import annotations

from .models import HotelOption


def _fmt_dates(check_in, check_out) -> str:
    return f"{check_in.strftime('%b %d')} - {check_out.strftime('%b %d, %Y')}"


def print_top_options(scored: list[tuple[HotelOption, float]]) -> None:
    """scored: output of rank_top_n() - list of (HotelOption, score) pairs."""
    print("\nTop options found:")
    for i, (option, score) in enumerate(scored, start=1):
        stars = f"{option.star_rating}★" if option.star_rating else "unrated"
        print(f"  {i}. {option.hotel_name} — ${option.total_price:.2f} total "
              f"(${option.nightly_rate:.2f}/night) — {stars} — "
              f"{_fmt_dates(option.check_in, option.check_out)}")
    print()


def print_booking_confirmation(option: HotelOption) -> None:
    print(f"\nBooked {option.hotel_name} for {_fmt_dates(option.check_in, option.check_out)} "
          f"— ${option.total_price:.2f} total (${option.nightly_rate:.2f}/night)\n")