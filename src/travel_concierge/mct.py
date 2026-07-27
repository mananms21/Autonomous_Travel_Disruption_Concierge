from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MCTBand:
    connection_type: str
    min_minutes: int
    max_minutes: int
    recommended_minutes: int
    description: str


MCT_TABLE: dict[str, MCTBand] = {
    "DOMESTIC_DOMESTIC_SAME_TERMINAL": MCTBand(
        connection_type="Domestic -> Domestic, same terminal",
        min_minutes=30,
        max_minutes=45,
        recommended_minutes=45,
        description="Typical domestic same-terminal transfer time.",
    ),
    "DOMESTIC_DOMESTIC_TERMINAL_CHANGE": MCTBand(
        connection_type="Domestic -> Domestic, terminal change",
        min_minutes=45,
        max_minutes=60,
        recommended_minutes=60,
        description="Typical domestic transfer with terminal move.",
    ),
    "DOMESTIC_INTERNATIONAL": MCTBand(
        connection_type="Domestic -> International",
        min_minutes=60,
        max_minutes=90,
        recommended_minutes=90,
        description="Domestic arrival connecting to international departure.",
    ),
    "INTERNATIONAL_INTERNATIONAL_TERMINAL_CHANGE": MCTBand(
        connection_type="International -> International, terminal change",
        min_minutes=90,
        max_minutes=120,
        recommended_minutes=120,
        description="Typical international transfer with terminal move.",
    ),
}


def lookup_mct(
    airport: str,
    *,
    terminal_change: bool,
    intl_to_domestic: bool,
    origin_international: bool | None = None,
    destination_international: bool | None = None,
) -> MCTBand:
    """Return a conservative MCT band for the connection.

    The table uses the user's requested categories and falls back conservatively
    when the exact combination is not listed.
    """

    if origin_international and destination_international:
        return MCT_TABLE["INTERNATIONAL_INTERNATIONAL_TERMINAL_CHANGE"] if terminal_change else MCTBand(
            connection_type="International -> International, same terminal",
            min_minutes=90,
            max_minutes=120,
            recommended_minutes=105,
            description=f"Conservative same-terminal international transfer at {airport}.",
        )

    if intl_to_domestic or (origin_international and destination_international is False):
        return MCTBand(
            connection_type="International -> Domestic",
            min_minutes=60,
            max_minutes=90,
            recommended_minutes=90,
            description=f"International arrival connecting to domestic departure at {airport}.",
        )

    if terminal_change:
        return MCT_TABLE["DOMESTIC_DOMESTIC_TERMINAL_CHANGE"]

    return MCT_TABLE["DOMESTIC_DOMESTIC_SAME_TERMINAL"]
