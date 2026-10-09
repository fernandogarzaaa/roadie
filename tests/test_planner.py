import itertools
from datetime import date

import pytest

from app import planner
from app.planner import Stop

HOME = (45.5152, -122.6784)  # Portland
START = date(2027, 1, 1)


def s(city, lat, lon, score=0.5):
    return Stop(city=city, lat=lat, lon=lon, score=score)


SEATTLE = s("Seattle", 47.6062, -122.3321, 0.8)
EUGENE = s("Eugene", 44.0521, -123.0868, 0.7)
BOISE = s("Boise", 43.6150, -116.2023, 0.2)
OLYMPIA = s("Olympia", 47.0379, -122.9007, 0.6)


def test_order_stops_matches_brute_force_optimum():
    stops = [SEATTLE, EUGENE, OLYMPIA, BOISE, s("Bend", 44.0582, -121.3153)]
    order = planner.order_stops(HOME, stops)
    assert sorted(x.city for x in order) == sorted(x.city for x in stops)
    best = min(planner.loop_km(HOME, list(p)) for p in itertools.permutations(stops))
    assert planner.loop_km(HOME, order) == pytest.approx(best, rel=0.02)
    assert planner.loop_km(HOME, order) <= planner.loop_km(HOME, stops)


def test_order_trivial():
    assert planner.order_stops(HOME, []) == []
    assert planner.order_stops(HOME, [SEATTLE]) == [SEATTLE]
    assert planner.loop_km(HOME, []) == 0.0


def test_schedule_inserts_travel_days_for_long_legs():
    far = s("Far", 45.5152, -110.0)  # ~1300 km road east
    days = planner.schedule(HOME, "Home", [far], START, max_drive_km=600)
    kinds = [d.kind for d in days]
    assert kinds == ["travel", "travel", "show", "travel", "travel", "home"]
    assert days[0].date == "2027-01-01" and days[-1].day == 6
    assert sum(d.drive_km for d in days) == pytest.approx(
        2 * planner.leg_km(HOME, (far.lat, far.lon)), abs=0.5
    )


def test_schedule_rest_after_every_five_shows():
    stops = [s(f"C{i}", 45.5 + i * 0.1, -122.6) for i in range(7)]
    kinds = [d.kind for d in planner.schedule(HOME, "Home", stops, START)]
    assert kinds.count("rest") == 1
    assert kinds.index("rest") == 5
    assert kinds[-1] == "home"


def test_cost_model_exact():
    days = [planner.Day(1, "d", "show", "A", 100.0), planner.Day(2, "d", "home", "H", 100.0)]
    c = planner.cost(days, band_size=3)
    assert c.fuel == pytest.approx(200 * planner.FUEL_PER_KM)
    assert c.lodging == 1 * 2 * planner.LODGING_PER_ROOM  # 3 people -> 2 rooms, 1 night
    assert c.per_diem == 2 * 3 * planner.PER_DIEM
    assert c.total == round(c.fuel + c.lodging + c.per_diem, 2)
    assert planner.cost(days, band_size=1).lodging == planner.LODGING_PER_ROOM


def test_plan_route_fits_and_orders():
    route = planner.plan_route(HOME, "Portland", [SEATTLE, EUGENE, OLYMPIA], 10, 10_000, 3, START)
    assert {x.city for x in route.stops} == {"Seattle", "Eugene", "Olympia"}
    assert route.within_budget and not route.dropped
    assert route.break_even_per_show == round(route.cost.total / 3, 2)


def test_plan_route_drops_lowest_score_for_budget():
    route = planner.plan_route(HOME, "Portland", [SEATTLE, EUGENE, BOISE], 10, 1500, 3, START)
    assert route.dropped[0] == "Boise"
    assert "budget" in route.notes[0]


def test_plan_route_drops_for_days():
    route = planner.plan_route(HOME, "Portland", [SEATTLE, EUGENE, OLYMPIA], 3, 10_000, 3, START)
    assert len(route.days) <= 3
    assert route.dropped == ["Olympia"]


def test_plan_route_reports_impossible():
    far = s("Far", 45.5152, -100.0, 0.9)
    route = planner.plan_route(HOME, "Portland", [far], 2, 100, 3, START)
    assert any("days" in n for n in route.notes) and any("exceeds budget" in n for n in route.notes)
    assert not route.within_budget


def test_route_to_dict_shape():
    route = planner.plan_route(HOME, "Portland", [SEATTLE], 5, 5000, 2, START)
    d = planner.route_to_dict(route)
    assert d["stops"][0]["city"] == "Seattle"
    assert d["days"][-1]["kind"] == "home"
    assert d["assumptions"]["fuel_per_km"] == planner.FUEL_PER_KM
    assert d["cost"]["total"] == route.cost.total


def test_cost_model_constants_are_pinned():
    # These are shown to users as the assumptions behind every estimate.
    assert (planner.FUEL_PER_KM, planner.LODGING_PER_ROOM, planner.PER_DIEM) == (0.22, 110.0, 35.0)
    assert (planner.MAX_DRIVE_KM_PER_DAY, planner.REST_EVERY_N_SHOWS) == (600.0, 5)
    assert Stop("A", 0, 0).score == 0.0


def test_rounding_in_outputs():
    c = planner.CostBreakdown(fuel=1.23456, lodging=2.0, per_diem=3.0)
    assert c.to_dict() == {"fuel": 1.23, "lodging": 2.0, "per_diem": 3.0, "total": 6.23}
    route = planner.Route(
        stops=[],
        days=[planner.Day(1, "d", "show", "A")] * 3,
        total_km=0,
        cost=planner.CostBreakdown(10.0, 0, 0),
        budget=1,
    )
    assert route.break_even_per_show == 3.33
    empty = planner.Route(stops=[], days=[], total_km=0, cost=c, budget=1)
    assert empty.break_even_per_show == 0.0


def test_loop_km_counts_every_leg():
    a, b = s("A", 45.5152, -121.0), s("B", 46.5152, -121.0)
    expected = (
        planner.leg_km(HOME, (a.lat, a.lon))
        + planner.leg_km((a.lat, a.lon), (b.lat, b.lon))
        + planner.leg_km((b.lat, b.lon), HOME)
    )
    assert planner.loop_km(HOME, [a, b]) == pytest.approx(expected)


def test_two_stops_are_ordered_nearest_first():
    near, far = s("Near", 45.6, -122.6), s("Far", 47.6, -122.3)
    assert [x.city for x in planner.order_stops(HOME, [far, near])] == ["Near", "Far"]


@pytest.mark.parametrize("seed", range(6))
def test_order_is_two_opt_optimal(seed):
    import random

    rng = random.Random(seed)
    stops = [s(f"S{i}", 44 + rng.random() * 4, -124 + rng.random() * 6) for i in range(7)]
    order = planner.order_stops(HOME, stops)
    best = planner.loop_km(HOME, order)
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            cand = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
            assert planner.loop_km(HOME, cand) >= best - 1e-6


def test_schedule_dates_and_zero_drive_rest_day():
    stops = [s(f"C{i}", 45.5 + i * 0.1, -122.6) for i in range(6)]
    days = planner.schedule(HOME, "Home", stops, START)
    assert [d.date for d in days[:3]] == ["2027-01-01", "2027-01-02", "2027-01-03"]
    rest = next(d for d in days if d.kind == "rest")
    assert rest.drive_km == 0.0 and rest.stop is None
    assert days[0].stop is stops[0]


def test_leg_exactly_at_daily_limit_needs_no_travel_day():
    stop = s("Edge", 46.5, -122.6)
    limit = planner.leg_km(HOME, (stop.lat, stop.lon))
    kinds = [d.kind for d in planner.schedule(HOME, "Home", [stop], START, max_drive_km=limit)]
    assert kinds == ["show", "home"]


def test_exact_fit_on_days_and_budget_is_kept():
    stops = [SEATTLE, EUGENE]
    probe = planner.plan_route(HOME, "Portland", stops, 30, 1e9, 3, START)
    days, total = len(probe.days), probe.cost.total
    exact = planner.plan_route(HOME, "Portland", stops, days, total, 3, START)
    assert exact.dropped == [] and exact.within_budget and not exact.notes


def test_drops_down_to_a_single_stop():
    route = planner.plan_route(HOME, "Portland", [SEATTLE, EUGENE, OLYMPIA], 30, 50, 3, START)
    assert [x.city for x in route.stops] == ["Seattle"]
    assert route.dropped == ["Olympia", "Eugene"]


def test_defaults_band_of_three_and_start_in_sixty_days():
    from datetime import timedelta

    route = planner.plan_route(HOME, "Portland", [SEATTLE], 5, 5000)
    assert route.days[0].date == (date.today() + timedelta(days=60)).isoformat()
    assert route.cost.lodging == 1 * 2 * planner.LODGING_PER_ROOM  # one night, two rooms
