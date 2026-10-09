"""Deterministic tour maths: stop ordering, travel days and a cost model.

Qloo decides *where the audience is*; this module only answers "can a van
get there in time and on budget". It never invents demand or revenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.geo import road_km

# Cost model defaults (USD). Surfaced in the UI so users can sanity-check them.
FUEL_PER_KM = 0.22  # one van, ~12 L/100 km at ~$1.80/L
LODGING_PER_ROOM = 110.0  # budget motel, two people per room
PER_DIEM = 35.0  # food per person per day
MAX_DRIVE_KM_PER_DAY = 600.0
REST_EVERY_N_SHOWS = 5


@dataclass
class Stop:
    city: str
    lat: float
    lon: float
    score: float = 0.0
    entity_id: str = ""
    why: str = ""
    venues: list[dict[str, Any]] = field(default_factory=list)
    openers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Day:
    day: int
    date: str
    kind: str  # "show" | "travel" | "rest" | "home"
    city: str
    drive_km: float = 0.0
    stop: Stop | None = None


@dataclass
class CostBreakdown:
    fuel: float
    lodging: float
    per_diem: float

    @property
    def total(self) -> float:
        return round(self.fuel + self.lodging + self.per_diem, 2)

    def to_dict(self) -> dict[str, float]:
        return {
            "fuel": round(self.fuel, 2),
            "lodging": round(self.lodging, 2),
            "per_diem": round(self.per_diem, 2),
            "total": self.total,
        }


@dataclass
class Route:
    stops: list[Stop]
    days: list[Day]
    total_km: float
    cost: CostBreakdown
    budget: float
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def within_budget(self) -> bool:
        return self.cost.total <= self.budget

    @property
    def break_even_per_show(self) -> float:
        shows = sum(1 for d in self.days if d.kind == "show")
        return round(self.cost.total / shows, 2) if shows else 0.0


def leg_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return road_km(a[0], a[1], b[0], b[1])


def loop_km(home: tuple[float, float], stops: list[Stop]) -> float:
    if not stops:
        return 0.0
    pts = [home, *[(s.lat, s.lon) for s in stops], home]
    return sum(leg_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def order_stops(home: tuple[float, float], stops: list[Stop]) -> list[Stop]:
    """Nearest-neighbour tour from home, improved with 2-opt (closed loop)."""
    if len(stops) <= 1:
        return list(stops)
    remaining = list(stops)
    order: list[Stop] = []
    here = home
    while remaining:
        nxt = min(remaining, key=lambda s: (leg_km(here, (s.lat, s.lon)), s.city))
        order.append(nxt)
        remaining.remove(nxt)
        here = (nxt.lat, nxt.lon)
    improved = True
    while improved:
        improved = False
        best = loop_km(home, order)
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                length = loop_km(home, candidate)
                if length + 1e-6 < best:
                    order, best, improved = candidate, length, True
    return order


def schedule(
    home: tuple[float, float],
    home_name: str,
    stops: list[Stop],
    start: date,
    max_drive_km: float = MAX_DRIVE_KM_PER_DAY,
) -> list[Day]:
    days: list[Day] = []
    here = home
    n = 0

    def add(kind: str, city: str, km: float = 0.0, stop: Stop | None = None) -> None:
        nonlocal n
        n += 1
        days.append(
            Day(n, (start + timedelta(days=n - 1)).isoformat(), kind, city, round(km, 1), stop)
        )

    for shows, stop in enumerate(stops):
        km = leg_km(here, (stop.lat, stop.lon))
        while km > max_drive_km:
            add("travel", f"en route to {stop.city}", max_drive_km)
            km -= max_drive_km
        if shows and shows % REST_EVERY_N_SHOWS == 0:
            add("rest", stop.city)
        add("show", stop.city, km, stop)
        here = (stop.lat, stop.lon)
    km_home = leg_km(here, home)
    while km_home > max_drive_km:
        add("travel", f"en route to {home_name}", max_drive_km)
        km_home -= max_drive_km
    add("home", home_name, km_home)
    return days


def cost(days: list[Day], band_size: int) -> CostBreakdown:
    total_km = sum(d.drive_km for d in days)
    nights = sum(1 for d in days if d.kind != "home")
    rooms = max(1, -(-band_size // 2))
    return CostBreakdown(
        fuel=total_km * FUEL_PER_KM,
        lodging=nights * rooms * LODGING_PER_ROOM,
        per_diem=len(days) * band_size * PER_DIEM,
    )


def plan_route(
    home: tuple[float, float],
    home_name: str,
    candidates: list[Stop],
    tour_days: int,
    budget: float,
    band_size: int = 3,
    start: date | None = None,
) -> Route:
    """Fit the best-scoring stops into the day count and budget.

    Stops are added in score order; the lowest scorers are dropped first when
    the schedule runs long or the cost model goes over budget.
    """
    start = start or date.today() + timedelta(days=60)
    pool = sorted(candidates, key=lambda s: (-s.score, s.city))
    chosen = list(pool)
    dropped: list[str] = []
    notes: list[str] = []
    while True:
        ordered = order_stops(home, chosen)
        days = schedule(home, home_name, ordered, start)
        breakdown = cost(days, band_size)
        too_long = len(days) > tour_days
        too_costly = breakdown.total > budget
        if not (too_long or too_costly) or len(chosen) <= 1:
            break
        worst = min(chosen, key=lambda s: (s.score, s.city))
        chosen = [s for s in chosen if s is not worst]
        dropped.append(worst.city)
        notes.append(f"Dropped {worst.city} ({'days' if too_long else 'budget'}).")
    if len(days) > tour_days:
        notes.append(f"Even one stop needs {len(days)} days; extend the tour or start closer.")
    if breakdown.total > budget:
        notes.append(f"Minimum cost ${breakdown.total:,.0f} exceeds budget ${budget:,.0f}.")
    return Route(
        stops=ordered,
        days=days,
        total_km=round(sum(d.drive_km for d in days), 1),
        cost=breakdown,
        budget=budget,
        dropped=dropped,
        notes=notes,
    )


def route_to_dict(route: Route) -> dict[str, Any]:
    return {
        "total_km": route.total_km,
        "cost": route.cost.to_dict(),
        "budget": route.budget,
        "within_budget": route.within_budget,
        "break_even_per_show": route.break_even_per_show,
        "dropped": route.dropped,
        "notes": route.notes,
        "stops": [
            {
                "city": s.city,
                "lat": s.lat,
                "lon": s.lon,
                "score": round(s.score, 3),
                "entity_id": s.entity_id,
                "why": s.why,
                "venues": s.venues,
                "openers": s.openers,
            }
            for s in route.stops
        ],
        "days": [
            {"day": d.day, "date": d.date, "kind": d.kind, "city": d.city, "drive_km": d.drive_km}
            for d in route.days
        ],
        "assumptions": {
            "fuel_per_km": FUEL_PER_KM,
            "lodging_per_room": LODGING_PER_ROOM,
            "per_diem": PER_DIEM,
            "max_drive_km_per_day": MAX_DRIVE_KM_PER_DAY,
            "rest_every_n_shows": REST_EVERY_N_SHOWS,
        },
    }
