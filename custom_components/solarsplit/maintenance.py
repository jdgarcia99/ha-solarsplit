"""Maintenance window: firmware updates and restarts of the loads' devices.

While a maintenance entity is on (for example the script that updates every
ESPHome device), and for a margin after it, readings are not trusted to learn
from. Without an entity, a device of a load that goes unavailable and comes
back within `MAX_OUTAGE`, or restarts (its uptime goes back), opens the same
window. Safety decisions do not change: unknown data is still unknown. No
Home Assistant imports: times are seconds on any clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_OUTAGE = 900.0  # s: a device unavailable for longer is an outage, not maintenance
KEEP = 1800.0  # s of past windows kept, to discard measurements that overlap them

SOURCE_ENTITY = "entity"  # the maintenance entity is on, or was within the margin
SOURCE_UNAVAILABLE = "unavailable"  # a device of a load is unavailable, or came back within the margin
SOURCE_RESTART = "restart"  # a device of a load restarted within the margin
SOURCES = [SOURCE_ENTITY, SOURCE_UNAVAILABLE, SOURCE_RESTART]

UPTIME_SECONDS = "seconds"  # seconds since boot: goes down on a restart
UPTIME_BOOT = "boot"  # time of the boot: goes up on a restart


@dataclass(frozen=True)
class Device:
    """A device of a load as seen in one cycle."""

    available: bool  # False while its entities are unavailable
    uptime: float | None = None  # seconds since boot, or boot time as a timestamp; None: unknown
    uptime_kind: str = UPTIME_SECONDS


@dataclass(frozen=True)
class Window:
    """The maintenance window in force, if any."""

    source: str
    until: float | None  # end of the margin; None while it lasts (entity on, device unavailable)
    devices: tuple[str, ...] = ()


@dataclass
class _Watch:
    unavailable_since: float | None = None
    uptime: float | None = None


@dataclass
class MaintenanceTracker:
    """Follows the maintenance entity and the loads' devices, cycle by cycle."""

    margin: float = 600.0  # s after the entity goes off, a device comes back or restarts
    _entity_on: bool = False
    _entity_until: float | None = None
    _devices: dict[str, _Watch] = field(default_factory=dict)
    _until: dict[str, tuple[float, str]] = field(default_factory=dict)  # device: (end of margin, source)
    _past: list[tuple[float, float]] = field(default_factory=list)  # windows that ended: (start, end)
    _start: float | None = None  # start of the window in force
    window: Window | None = None

    def update(self, now: float, entity_on: bool | None, devices: dict[str, Device]) -> Window | None:
        """The window in force after this cycle's readings (an unusable entity counts as off)."""
        sources: list[tuple[str, float | None, tuple[str, ...]]] = []
        if entity_on:
            sources.append((SOURCE_ENTITY, None, ()))
        elif self._entity_on:  # it has just gone off: the margin starts now
            self._entity_until = now + self.margin
        self._entity_on = bool(entity_on)
        if not entity_on and self._entity_until is not None and now < self._entity_until:
            sources.append((SOURCE_ENTITY, self._entity_until, ()))

        for name, device in devices.items():
            watch = self._devices.setdefault(name, _Watch())
            if not device.available:
                watch.unavailable_since = watch.unavailable_since if watch.unavailable_since is not None else now
                if now - watch.unavailable_since <= MAX_OUTAGE:
                    sources.append((SOURCE_UNAVAILABLE, None, (name,)))
                continue
            if watch.unavailable_since is not None:
                if now - watch.unavailable_since <= MAX_OUTAGE:  # back in time: it was maintenance
                    self._until[name] = (now + self.margin, SOURCE_UNAVAILABLE)
                watch.unavailable_since = None
            if device.uptime is not None:
                previous, watch.uptime = watch.uptime, device.uptime
                restarted = previous is not None and (
                    device.uptime < previous if device.uptime_kind == UPTIME_SECONDS else device.uptime > previous
                )
                if restarted:
                    self._until[name] = (now + self.margin, SOURCE_RESTART)

        for name, (until, source) in list(self._until.items()):
            if now < until:
                sources.append((source, until, (name,)))
            else:
                del self._until[name]

        if not sources:
            if self._start is not None:
                self._past.append((self._start, now))
            self._start, self.window = None, None
        else:
            self._start = self._start if self._start is not None else now
            ongoing = any(until is None for _, until, _ in sources)
            self.window = Window(
                sources[0][0],
                None if ongoing else max(until for _, until, _ in sources if until is not None),
                tuple(sorted({name for _, _, names in sources for name in names})),
            )
        self._past = [(start, end) for start, end in self._past if end >= now - KEEP]
        return self.window

    def overlaps(self, start: float, end: float) -> bool:
        """Whether a period touched a maintenance window, past or in force."""
        if self._start is not None and end >= self._start:
            return True
        return any(start <= past_end and end >= past_start for past_start, past_end in self._past)
