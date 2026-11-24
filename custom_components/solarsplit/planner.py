"""Grid hours a relay load needs to meet its minimum before its deadline, at the lowest cost.

The expected surplus of every hour comes from what SolarSplit has seen on the
last days (`SurplusProfile`). Hours with enough expected surplus count first;
what is still missing is bought from the grid in the cheapest hours, preferring
the load's window, without going over the grid capacity. Without prices the
last hours before the deadline are used. No Home Assistant imports: times are
timestamps in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

PROFILE_DAYS = 7  # days of surplus kept to expect the next ones
HOURS = 24


@dataclass(frozen=True)
class Slot:
    """One hour of the horizon."""

    start: float
    end: float
    buy: float | None  # €/kWh; None: unknown
    sell: float | None  # €/kWh; None: unknown (an hour with surplus then costs nothing)
    surplus_w: float = 0.0  # expected surplus for managed loads
    estimated: bool = False  # the price is the same hour of today: tomorrow's is not published yet


@dataclass(frozen=True)
class Request:
    """A load whose minimum is not met yet."""

    load_id: str
    priority: int  # order in which loads take the expected surplus and the grid capacity
    power_w: float
    pending_h: float
    deadline: float
    preferred: frozenset[float] = frozenset()  # starts of the slots in its preferred window


@dataclass(frozen=True)
class PlannedSlot:
    """Part of a slot the load is expected to run in."""

    start: float
    end: float
    hours: float
    grid: bool  # bought from the grid (grid fill); otherwise covered by the expected surplus
    buy: float | None
    sell: float | None
    price: float | None  # €/kWh, effective: the sell price for the surplus part, the buy price for the rest
    cost: float | None  # €
    estimated: bool = False
    kwh: float = 0.0  # energy the load draws in it


@dataclass(frozen=True)
class Plan:
    """The hours a load is expected to run in to meet its minimum."""

    load_id: str
    priced: bool  # cheapest hours; False: the last hours before the deadline
    slots: tuple[PlannedSlot, ...] = ()
    short_h: float = 0.0  # hours that fit nowhere before the deadline

    @property
    def cost(self) -> float | None:
        """Estimated cost of the plan; None without prices."""
        if not self.priced:
            return None
        return sum(slot.cost or 0.0 for slot in self.slots)

    @property
    def grid_h(self) -> float:
        """Hours bought from the grid."""
        return sum(slot.hours for slot in self.slots if slot.grid)

    @property
    def grid_kwh(self) -> float:
        """Energy drawn in the hours bought from the grid."""
        return sum(slot.kwh for slot in self.slots if slot.grid)

    def fill_at(self, now: float) -> bool:
        """Whether the plan buys the hour that contains `now` from the grid."""
        return any(slot.grid and slot.start <= now < slot.end for slot in self.slots)


def _price(slot: Slot, surplus_w: float, power_w: float) -> float | None:
    """Effective price of running `power_w` in the slot, given the surplus expected for it."""
    if slot.buy is None:
        return None
    covered = min(max(surplus_w, 0.0), power_w) / power_w if power_w > 0 else 0.0
    return covered * (slot.sell or 0.0) + (1 - covered) * slot.buy


def _planned(slot: Slot, hours: float, grid: bool, left_w: float, power_w: float, priced: bool) -> PlannedSlot:
    """Part of a slot in a plan, with its effective price and cost."""
    price = None
    if priced:
        price = _price(slot, left_w, power_w) if grid else (slot.sell or 0.0)
    cost = None if price is None else price * hours * power_w / 1000
    return PlannedSlot(
        slot.start, slot.end, round(hours, 4), grid, slot.buy, slot.sell,
        None if price is None else round(price, 5), None if cost is None else round(cost, 6), slot.estimated,
        round(hours * power_w / 1000, 4),
    )


@dataclass
class _Usage:
    """What the requests planned so far take from every slot."""

    surplus: dict[float, float]  # W of expected surplus still free
    grid_w: dict[float, float]  # W of grid fill
    grid_loads: dict[float, int]  # loads in grid fill
    grid_cap: float | None

    def fits(self, slot: Slot, power_w: float) -> bool:
        """Whether one more load of `power_w` fits in the grid capacity of the slot."""
        if self.grid_cap is None:
            return self.grid_loads[slot.start] == 0
        return self.grid_w[slot.start] + power_w <= self.grid_cap


def _plan_one(now: float, slots: list[Slot], req: Request, usage: _Usage) -> Plan:
    """Plan one request with what the earlier ones left."""
    usable = [
        (slot, hours) for slot in slots
        if (hours := (min(slot.end, req.deadline) - max(slot.start, now)) / 3600) > 1e-9
    ]
    priced = bool(usable) and all(slot.buy is not None for slot, _ in usable)
    chosen: list[PlannedSlot] = []
    need = req.pending_h

    # Hours with enough expected surplus come first, in time order: the load runs then anyway.
    on_surplus: set[float] = set()
    for slot, hours in usable:
        if need <= 1e-9:
            break
        if usage.surplus[slot.start] >= req.power_w:
            take = min(hours, need)
            chosen.append(_planned(slot, take, False, usage.surplus[slot.start], req.power_w, priced))
            usage.surplus[slot.start] -= req.power_w
            on_surplus.add(slot.start)
            need -= take

    # The rest from the grid: the cheapest hours (the preferred window first), or the last ones.
    candidates = [
        (slot, hours) for slot, hours in usable
        if slot.start not in on_surplus and usage.fits(slot, req.power_w)
    ]
    if priced:
        candidates.sort(key=lambda item: (
            item[0].start not in req.preferred,
            _price(item[0], usage.surplus[item[0].start], req.power_w),
            item[0].start,
        ))
    else:
        candidates.sort(key=lambda item: -item[0].start)
    for slot, hours in candidates:
        if need <= 1e-9:
            break
        take = min(hours, need)
        chosen.append(_planned(slot, take, True, usage.surplus[slot.start], req.power_w, priced))
        usage.grid_w[slot.start] += req.power_w
        usage.grid_loads[slot.start] += 1
        usage.surplus[slot.start] = max(usage.surplus[slot.start] - req.power_w, 0.0)
        need -= take
    return Plan(req.load_id, priced, tuple(sorted(chosen, key=lambda s: s.start)), round(max(need, 0.0), 4))


def plan(now: float, slots: list[Slot], requests: list[Request], grid_cap: float | None) -> dict[str, Plan]:
    """Plan every request, in priority order.

    `grid_cap` is the power grid fill may use in one hour (contracted power less the
    margin). None: unknown, so at most one load fills from the grid in each hour.
    """
    usage = _Usage(
        {slot.start: slot.surplus_w for slot in slots},
        {slot.start: 0.0 for slot in slots},
        {slot.start: 0 for slot in slots},
        grid_cap,
    )
    return {
        req.load_id: _plan_one(now, slots, req, usage)
        for req in sorted(requests, key=lambda r: (r.priority, r.load_id))
    }


@dataclass
class SurplusProfile:
    """Mean surplus of every hour of the last days, to expect the next ones.

    `days` maps a local date to 24 pairs [sum of W, samples].
    """

    days: dict[str, list[list[float]]] = field(default_factory=dict)

    def add(self, day: str, hour: int, watts: float) -> None:
        """Add one reading of the surplus to an hour of a day."""
        if day not in self.days:
            self.days[day] = [[0.0, 0] for _ in range(HOURS)]
            for old in sorted(self.days)[:-PROFILE_DAYS]:
                del self.days[old]
        cell = self.days[day][hour]
        cell[0] += max(watts, 0.0)
        cell[1] += 1

    def expected(self, hour: int) -> float:
        """Median over the last days of the mean surplus of an hour; 0 W without data."""
        means = [hours[hour][0] / hours[hour][1] for hours in self.days.values() if hours[hour][1]]
        return median(means) if means else 0.0
