from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol
import os

from .client import AviationstackClient
from .monitoring import FlightLeg
from .search import search_routes


class RebookingState(str, Enum):
    RECEIVED = "RECEIVED"
    SEARCHING = "SEARCHING"
    OPTIONS_FOUND = "OPTIONS_FOUND"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    HELD = "HELD"
    AUTO_BOOKING = "AUTO_BOOKING"
    BOOKING_IN_PROGRESS = "BOOKING_IN_PROGRESS"
    RETRYING = "RETRYING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    NO_OPTIONS = "NO_OPTIONS"
    ESCALATED_HUMAN = "ESCALATED_HUMAN"
    CANCELLED_BY_MEMBER = "CANCELLED_BY_MEMBER"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class RebookingRequest:
    event_id: str
    pnr: str
    card_member_id: str
    card_tier: str
    disrupted_leg_id: str
    trigger_type: str
    rationale: str
    confidence: float
    disrupted_leg: FlightLeg
    reference_fare: float | None = None
    cabin_class: str = "economy"
    passenger_count: int = 1
    currency: str = "USD"
    search_window_hours: int = 24

    @property
    def route(self) -> str:
        return self.disrupted_leg.route or f"{self.disrupted_leg.departure_airport}-{self.disrupted_leg.arrival_airport}"


@dataclass(frozen=True)
class RebookingPolicy:
    max_auto_delta: dict[str, float] = field(
        default_factory=lambda: {"PLATINUM": 500.0, "STANDARD": 300.0, "GOLD": 400.0}
    )
    allowed_cabins: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "PLATINUM": ("economy", "premium_economy", "business", "first"),
            "GOLD": ("economy", "premium_economy", "business"),
            "STANDARD": ("economy", "premium_economy"),
        }
    )
    max_price_delta_ratio: float = 0.25
    max_retry_attempts: int = 3
    hold_timeout_minutes: int = 20
    top_n_options: int = 3
    arrival_delay_weight: float = 0.20
    price_weight: float = 0.35
    cabin_weight: float = 0.15
    stops_weight: float = 0.10
    connection_risk_penalty: float = 0.25

    def allowed_cabin(self, card_tier: str, cabin_class: str) -> bool:
        return cabin_class.lower() in self.allowed_cabins.get(card_tier.upper(), self.allowed_cabins["STANDARD"])

    def max_auto_delta_for(self, card_tier: str) -> float:
        return self.max_auto_delta.get(card_tier.upper(), self.max_auto_delta["STANDARD"])


@dataclass(frozen=True)
class FlightOffer:
    offer_id: str
    provider: str
    airline: str
    origin_airport: str
    destination_airport: str
    departure_time: datetime
    arrival_time: datetime
    cabin_class: str
    price: float
    currency: str
    stops: int = 0
    hold_supported: bool = False
    requires_instant_payment: bool = True
    connection_buffer_minutes: int | None = None
    required_mct_minutes: int | None = None
    reconnection_risk: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BookingReceipt:
    booking_id: str
    offer_id: str
    provider: str
    state: RebookingState
    booked_at: datetime
    payment_required_by: datetime | None = None
    confirmation_code: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OriginalLegCancellationResult:
    assumed_supported: bool
    cancelled: bool
    summary: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedOffer:
    offer: FlightOffer
    score: float
    policy_compliant: bool
    price_delta: float
    rationale: str


@dataclass(frozen=True)
class RebookingOutcome:
    event_id: str
    state: RebookingState
    selected_offer: FlightOffer | None
    booking: BookingReceipt | None
    ranked_offers: list[RankedOffer]
    history: list[RebookingState]
    summary: str
    handoff_next: tuple[str, ...] = ("hotel", "notification")
    cancellation: OriginalLegCancellationResult | None = None
    pending_hold: BookingReceipt | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class RebookingProvider(Protocol):
    def search_alternate_flights(self, request: RebookingRequest) -> list[FlightOffer]: ...

    def book_flight(self, request: RebookingRequest, offer: FlightOffer, *, idempotency_key: str) -> BookingReceipt: ...

    def hold_flight_option(self, request: RebookingRequest, offer: FlightOffer, *, idempotency_key: str) -> BookingReceipt: ...

    def confirm_held_booking(self, request: RebookingRequest, hold: BookingReceipt, *, idempotency_key: str) -> BookingReceipt: ...

    def cancel_new_booking(self, request: RebookingRequest, booking: BookingReceipt, *, reason: str) -> None: ...

    def cancel_original_leg(self, request: RebookingRequest, booking: BookingReceipt) -> OriginalLegCancellationResult: ...


class MockDuffelRebookingProvider:
    """Deterministic provider for the demo path."""

    def __init__(self) -> None:
        self._bookings_by_idempotency_key: dict[str, BookingReceipt] = {}
        self._holds_by_idempotency_key: dict[str, BookingReceipt] = {}

    def search_alternate_flights(self, request: RebookingRequest) -> list[FlightOffer]:
        origin = request.disrupted_leg.departure_airport
        destination = request.disrupted_leg.arrival_airport
        base_departure = max(request.disrupted_leg.scheduled_departure, datetime.now(timezone.utc)) + timedelta(hours=2)
        base_duration = max(90, int((request.disrupted_leg.scheduled_arrival - request.disrupted_leg.scheduled_departure).total_seconds() / 60))

        return [
            FlightOffer(
                offer_id=f"offer-{request.event_id[:8]}-1",
                provider="duffel-mock",
                airline=request.disrupted_leg.carrier or "IND",
                origin_airport=origin,
                destination_airport=destination,
                departure_time=base_departure,
                arrival_time=base_departure + timedelta(minutes=base_duration + 10),
                cabin_class=request.cabin_class,
                price=self._price_for(request, 240.0),
                currency=request.currency,
                stops=0,
                hold_supported=True,
                requires_instant_payment=False,
                connection_buffer_minutes=120,
                required_mct_minutes=45,
                reconnection_risk=0.05,
                raw={"mock": True, "tier": request.card_tier},
            ),
            FlightOffer(
                offer_id=f"offer-{request.event_id[:8]}-2",
                provider="duffel-mock",
                airline=request.disrupted_leg.carrier or "IND",
                origin_airport=origin,
                destination_airport=destination,
                departure_time=base_departure + timedelta(minutes=40),
                arrival_time=base_departure + timedelta(minutes=base_duration + 60),
                cabin_class="premium_economy" if request.cabin_class == "economy" else request.cabin_class,
                price=self._price_for(request, 190.0),
                currency=request.currency,
                stops=1,
                hold_supported=False,
                requires_instant_payment=True,
                connection_buffer_minutes=75,
                required_mct_minutes=55,
                reconnection_risk=0.18,
                raw={"mock": True, "stops": 1},
            ),
            FlightOffer(
                offer_id=f"offer-{request.event_id[:8]}-3",
                provider="duffel-mock",
                airline=request.disrupted_leg.carrier or "IND",
                origin_airport=origin,
                destination_airport=destination,
                departure_time=base_departure + timedelta(minutes=20),
                arrival_time=base_departure + timedelta(minutes=base_duration + 25),
                cabin_class="business",
                price=self._price_for(request, 510.0),
                currency=request.currency,
                stops=0,
                hold_supported=True,
                requires_instant_payment=False,
                connection_buffer_minutes=140,
                required_mct_minutes=40,
                reconnection_risk=0.02,
                raw={"mock": True, "upgrade": True},
            ),
        ]

    def book_flight(self, request: RebookingRequest, offer: FlightOffer, *, idempotency_key: str) -> BookingReceipt:
        existing = self._bookings_by_idempotency_key.get(idempotency_key)
        if existing:
            return existing

        ticket_payload = {
            "ticket_status": "BOOKED_MOCK",
            "event_id": request.event_id,
            "pnr": request.pnr,
            "card_member_id": request.card_member_id,
            "offer_id": offer.offer_id,
            "airline": offer.airline,
            "flight_date": offer.departure_time.date().isoformat(),
            "origin": offer.origin_airport,
            "destination": offer.destination_airport,
            "departure_time": offer.departure_time.isoformat(),
            "arrival_time": offer.arrival_time.isoformat(),
            "price": offer.price,
            "currency": offer.currency,
        }
        receipt = BookingReceipt(
            booking_id=f"book-{offer.offer_id}",
            offer_id=offer.offer_id,
            provider=offer.provider,
            state=RebookingState.CONFIRMED,
            booked_at=datetime.now(timezone.utc),
            confirmation_code=f"CNF-{offer.offer_id[-4:].upper()}",
            raw={"mode": "instant", "event_id": request.event_id, "ticket": ticket_payload},
        )
        self._bookings_by_idempotency_key[idempotency_key] = receipt
        return receipt

    def hold_flight_option(self, request: RebookingRequest, offer: FlightOffer, *, idempotency_key: str) -> BookingReceipt:
        if not offer.hold_supported or offer.requires_instant_payment:
            raise ValueError(f"Offer {offer.offer_id} does not support hold")

        existing = self._holds_by_idempotency_key.get(idempotency_key)
        if existing:
            return existing

        receipt = BookingReceipt(
            booking_id=f"hold-{offer.offer_id}",
            offer_id=offer.offer_id,
            provider=offer.provider,
            state=RebookingState.HELD,
            booked_at=datetime.now(timezone.utc),
            payment_required_by=datetime.now(timezone.utc) + timedelta(minutes=20),
            raw={"mode": "hold", "event_id": request.event_id},
        )
        self._holds_by_idempotency_key[idempotency_key] = receipt
        return receipt

    def confirm_held_booking(self, request: RebookingRequest, hold: BookingReceipt, *, idempotency_key: str) -> BookingReceipt:
        existing = self._bookings_by_idempotency_key.get(idempotency_key)
        if existing:
            return existing

        receipt = BookingReceipt(
            booking_id=f"book-{hold.offer_id}",
            offer_id=hold.offer_id,
            provider=hold.provider,
            state=RebookingState.CONFIRMED,
            booked_at=datetime.now(timezone.utc),
            confirmation_code=f"CNF-{hold.offer_id[-4:].upper()}",
            raw={"mode": "confirm_hold", "event_id": request.event_id},
        )
        self._bookings_by_idempotency_key[idempotency_key] = receipt
        return receipt

    def cancel_new_booking(self, request: RebookingRequest, booking: BookingReceipt, *, reason: str) -> None:
        key = f"{request.event_id}:{booking.offer_id}"
        self._bookings_by_idempotency_key.pop(key, None)
        self._holds_by_idempotency_key.pop(key, None)

    def cancel_original_leg(self, request: RebookingRequest, booking: BookingReceipt) -> OriginalLegCancellationResult:
        return OriginalLegCancellationResult(
            assumed_supported=False,
            cancelled=False,
            summary="Original leg cancellation is treated as a downstream integration point; the demo keeps it as a no-op unless the original ticket source is Duffel.",
            raw={"event_id": request.event_id, "booking_id": booking.booking_id},
        )

    def _price_for(self, request: RebookingRequest, base_price: float) -> float:
        if request.reference_fare is None:
            return base_price
        return max(0.0, request.reference_fare + base_price)


class AviationstackRouteSearchProvider(MockDuffelRebookingProvider):
    """Use Aviationstack for alternate option discovery and mock booking confirmation."""

    def __init__(self, access_key: str, *, max_results: int = 15, fallback_to_mock: bool = True):
        super().__init__()
        self.client = AviationstackClient(access_key=access_key)
        self.max_results = max_results
        self.fallback_to_mock = fallback_to_mock

    def search_alternate_flights(self, request: RebookingRequest) -> list[FlightOffer]:
        flight_date = request.disrupted_leg.scheduled_departure.date()
        try:
            results = search_routes(
                self.client,
                departure_iata=request.disrupted_leg.departure_airport,
                arrival_iata=request.disrupted_leg.arrival_airport,
                flight_date=flight_date,
                max_results=self.max_results,
            )
        except Exception:  # noqa: BLE001 - provider can fail due plan or key limits
            if self.fallback_to_mock:
                return super().search_alternate_flights(request)
            raise

        offers: list[FlightOffer] = []
        fallback_departure = request.disrupted_leg.scheduled_departure + timedelta(hours=2)
        fallback_arrival = request.disrupted_leg.scheduled_arrival + timedelta(hours=2)

        for index, result in enumerate(results, start=1):
            departure_time = result.departure_time or (fallback_departure + timedelta(minutes=index * 25))
            arrival_time = result.arrival_time or (fallback_arrival + timedelta(minutes=index * 20))
            if departure_time < request.disrupted_leg.scheduled_departure:
                continue

            offer_id = result.flight_iata or result.flight_icao or f"api-offer-{index}"
            estimated_price = self._estimated_price(request, index=index)
            offers.append(
                FlightOffer(
                    offer_id=offer_id,
                    provider="aviationstack-search+mock-booking",
                    airline=result.airline or (request.disrupted_leg.carrier or "UNKNOWN"),
                    origin_airport=result.departure_airport or request.disrupted_leg.departure_airport,
                    destination_airport=result.arrival_airport or request.disrupted_leg.arrival_airport,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    cabin_class=request.cabin_class,
                    price=estimated_price,
                    currency=request.currency,
                    stops=0,
                    hold_supported=False,
                    requires_instant_payment=True,
                    connection_buffer_minutes=None,
                    required_mct_minutes=None,
                    reconnection_risk=min(0.40, 0.03 * index),
                    raw=result.raw,
                )
            )

        if offers:
            return offers

        if self.fallback_to_mock:
            return super().search_alternate_flights(request)
        return []

    def _estimated_price(self, request: RebookingRequest, *, index: int) -> float:
        if request.reference_fare is not None:
            return max(0.0, request.reference_fare + (80.0 + index * 25.0))
        return 180.0 + index * 35.0


class RebookingAgent:
    def __init__(self, provider: RebookingProvider | None = None, policy: RebookingPolicy | None = None):
        if provider is not None:
            self.provider = provider
        else:
            access_key = os.environ.get("AVIATIONSTACK_ACCESS_KEY")
            self.provider = AviationstackRouteSearchProvider(access_key) if access_key else MockDuffelRebookingProvider()
        self.policy = policy or RebookingPolicy()

    def initiate_rebooking(self, request: RebookingRequest) -> RebookingOutcome:
        history = [RebookingState.RECEIVED, RebookingState.SEARCHING]
        offers = self.provider.search_alternate_flights(request)
        if not offers:
            history.append(RebookingState.NO_OPTIONS)
            return RebookingOutcome(
                event_id=request.event_id,
                state=RebookingState.NO_OPTIONS,
                selected_offer=None,
                booking=None,
                ranked_offers=[],
                history=history,
                summary="No alternate flights matched the route and policy constraints.",
                raw={"request": _serialize_request(request)},
            )

        history.append(RebookingState.OPTIONS_FOUND)
        ranked_offers = self.rank_and_filter(request, offers)
        selected_offer = ranked_offers[0].offer
        history.extend([RebookingState.AUTO_BOOKING, RebookingState.BOOKING_IN_PROGRESS])
        booking = self.provider.book_flight(request, selected_offer, idempotency_key=request.event_id)
        history.append(RebookingState.CONFIRMED)
        cancellation = self.provider.cancel_original_leg(request, booking)
        return RebookingOutcome(
            event_id=request.event_id,
            state=RebookingState.CONFIRMED,
            selected_offer=selected_offer,
            booking=booking,
            ranked_offers=ranked_offers,
            history=history,
            summary=f"Mock rebooking selected the top option and booked it for {selected_offer.departure_time.date()}.",
            cancellation=cancellation,
            raw={"request": _serialize_request(request)},
        )

    def rank_and_filter(self, request: RebookingRequest, offers: list[FlightOffer]) -> list[RankedOffer]:
        ranked = [self._score_offer(request, offer) for offer in offers]
        return sorted(ranked, key=lambda item: item.score, reverse=True)[: self.policy.top_n_options]

    def _score_offer(self, request: RebookingRequest, offer: FlightOffer) -> RankedOffer:
        price_delta = self._price_delta(request, offer)
        reference_price = request.reference_fare or max(offer.price, 1.0)
        price_score = max(0.0, 1 - min(price_delta, reference_price * 2) / max(reference_price, 1.0))
        arrival_delay_minutes = max(0.0, (offer.arrival_time - request.disrupted_leg.scheduled_arrival).total_seconds() / 60)
        arrival_score = max(0.0, 1 - min(arrival_delay_minutes, 360) / 360)
        cabin_match = 1.0 if offer.cabin_class.lower() == request.cabin_class.lower() else 0.0
        score = (
            self.policy.price_weight * price_score
            + self.policy.arrival_delay_weight * arrival_score
            + self.policy.cabin_weight * cabin_match
            - self.policy.stops_weight * float(offer.stops)
            - self.policy.connection_risk_penalty * max(0.0, min(1.0, offer.reconnection_risk))
        )
        compliant = self._is_policy_compliant(request, offer, price_delta)
        rationale = self._rationale_for_offer(request, offer, price_delta, compliant)
        return RankedOffer(offer=offer, score=score, policy_compliant=compliant, price_delta=price_delta, rationale=rationale)

    def _is_policy_compliant(self, request: RebookingRequest, offer: FlightOffer, price_delta: float) -> bool:
        max_delta = self.policy.max_auto_delta_for(request.card_tier)
        cabin_allowed = self.policy.allowed_cabin(request.card_tier, offer.cabin_class)
        return price_delta <= max_delta and cabin_allowed and offer.reconnection_risk <= 0.15

    def _price_delta(self, request: RebookingRequest, offer: FlightOffer) -> float:
        if request.reference_fare is None:
            return offer.price
        return max(0.0, offer.price - request.reference_fare)

    def _rationale_for_offer(self, request: RebookingRequest, offer: FlightOffer, price_delta: float, compliant: bool) -> str:
        status = "policy-compliant" if compliant else "needs approval"
        return (
            f"{offer.airline} {offer.cabin_class} {offer.stops}-stop offer at {offer.price:.2f} {offer.currency}; "
            f"price delta {price_delta:.2f} vs limit {self.policy.max_auto_delta_for(request.card_tier):.2f}; "
            f"reconnection risk {offer.reconnection_risk:.2f}; {status}."
        )


def _serialize_request(request: RebookingRequest) -> dict[str, Any]:
    return {
        "event_id": request.event_id,
        "pnr": request.pnr,
        "card_member_id": request.card_member_id,
        "card_tier": request.card_tier,
        "disrupted_leg_id": request.disrupted_leg_id,
        "trigger_type": request.trigger_type,
        "rationale": request.rationale,
        "confidence": request.confidence,
        "route": request.route,
        "cabin_class": request.cabin_class,
        "reference_fare": request.reference_fare,
        "passenger_count": request.passenger_count,
        "currency": request.currency,
        "search_window_hours": request.search_window_hours,
    }
