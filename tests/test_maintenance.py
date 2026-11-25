"""Maintenance window: a maintenance entity, and devices of loads unavailable or restarted."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit.const import (
    CONF_CIRCUIT_SENSOR,
    CONF_MAINTENANCE_ENTITY,
    CONF_PV_SENSOR,
)
from custom_components.solarsplit.maintenance import (
    MAX_OUTAGE,
    SOURCE_ENTITY,
    SOURCE_RESTART,
    SOURCE_UNAVAILABLE,
    UPTIME_BOOT,
    Device,
    MaintenanceTracker,
)

from .test_coordinator import POOL, _cycle, _entity, _load, _setup, _state

MARGIN = 600.0
SCRIPT = "script.esphome_update_all_esphome_devices"
PV = "sensor.pv_power"
UPTIME = "sensor.depuradora_piscina_uptime"


# Without Home Assistant


def test_entity_on_and_the_margin_after() -> None:
    tracker = MaintenanceTracker(margin=MARGIN)
    assert tracker.update(0, False, {}) is None
    window = tracker.update(10, True, {})
    assert window.source == SOURCE_ENTITY and window.until is None
    window = tracker.update(70, False, {})  # the script ends; devices restart after it
    assert window.source == SOURCE_ENTITY and window.until == 70 + MARGIN
    assert tracker.update(70 + MARGIN - 1, False, {}) is not None
    assert tracker.update(70 + MARGIN, None, {}) is None  # an unusable entity is off
    assert tracker.overlaps(50, 60) and not tracker.overlaps(0, 5)


def test_device_unavailable_and_back_within_15_minutes() -> None:
    tracker = MaintenanceTracker(margin=MARGIN)
    tracker.update(0, None, {"termo": Device(True)})
    window = tracker.update(10, None, {"termo": Device(False)})
    assert (window.source, window.until, window.devices) == (SOURCE_UNAVAILABLE, None, ("termo",))
    window = tracker.update(200, None, {"termo": Device(True)})
    assert window.until == 200 + MARGIN
    assert tracker.update(200 + MARGIN, None, {"termo": Device(True)}) is None


def test_device_unavailable_for_longer_is_an_outage() -> None:
    tracker = MaintenanceTracker(margin=MARGIN)
    tracker.update(0, None, {"termo": Device(False)})
    assert tracker.update(MAX_OUTAGE, None, {"termo": Device(False)}) is not None
    assert tracker.update(MAX_OUTAGE + 1, None, {"termo": Device(False)}) is None
    assert tracker.update(MAX_OUTAGE + 100, None, {"termo": Device(True)}) is None  # no margin either


def test_restart_by_uptime() -> None:
    tracker = MaintenanceTracker(margin=MARGIN)
    tracker.update(0, None, {"ac": Device(True, 30000)})
    assert tracker.update(5, None, {"ac": Device(True, 30005)}) is None
    window = tracker.update(10, None, {"ac": Device(True, 3)})  # seconds since boot went back
    assert (window.source, window.until) == (SOURCE_RESTART, 10 + MARGIN)
    # A boot time that moves forward is a restart too
    tracker.update(20, None, {"pump": Device(True, 1_000_000, UPTIME_BOOT)})
    window = tracker.update(30, None, {"pump": Device(True, 1_000_025, UPTIME_BOOT)})
    assert window.devices == ("ac", "pump")


# Inside Home Assistant


async def test_script_running_and_the_margin_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(PV, "3000", {"unit_of_measurement": "W"})
    hass.states.async_set(SCRIPT, "off")
    entry = await _setup(hass, **{CONF_MAINTENANCE_ENTITY: SCRIPT, CONF_PV_SENSOR: PV})
    coordinator = entry.runtime_data
    sensor = _entity(hass, f"{entry.entry_id}_maintenance", "binary_sensor")
    coordinator.base.days.clear()
    coordinator.profile.days.clear()

    hass.states.async_set(SCRIPT, "on")  # the script updates every ESPHome device
    await _cycle(hass, entry, export=1000)
    state = hass.states.get(sensor)
    assert state.state == "on" and state.attributes["source"] == "entity" and state.attributes["until"] is None
    decision = hass.states.get(_entity(hass, f"{entry.entry_id}_last_decision"))
    assert decision.attributes["maintenance"] == "entity"
    assert coordinator.base.days == {} and coordinator.profile.days == {}  # nothing learned

    freezer.tick(timedelta(minutes=1))
    hass.states.async_set(SCRIPT, "off")  # it ends; the devices restart after it
    await _cycle(hass, entry, export=1000)
    state = hass.states.get(sensor)
    assert state.state == "on" and state.attributes["until"] is not None
    freezer.tick(timedelta(minutes=9, seconds=59))
    await _cycle(hass, entry, export=1000)
    assert hass.states.get(sensor).state == "on" and coordinator.base.days == {}

    freezer.tick(timedelta(seconds=1))
    await _cycle(hass, entry, export=1000)
    assert hass.states.get(sensor).state == "off"
    assert coordinator.base.known(dt_util.now().hour)  # learning again
    assert float(_state(hass, entry, "budget")) == 900  # decisions as always


def _device_with_uptime(hass: HomeAssistant, uptime: str) -> None:
    """The pool pump's ESPHome device, with its switch and its uptime sensor."""
    config = MockConfigEntry(domain="esphome")
    config.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id, identifiers={("esphome", "depuradora")}, name="depuradora_piscina"
    )
    entities = er.async_get(hass)
    for domain, object_id in (("switch", "depuradora_piscina"), ("sensor", "depuradora_piscina_uptime")):
        entities.async_get_or_create(
            domain, "esphome", object_id, config_entry=config, device_id=device.id, suggested_object_id=object_id
        )
    hass.states.async_set(UPTIME, uptime)


async def test_restart_detected_by_the_uptime_sensor(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _device_with_uptime(hass, "2026-11-22T20:12:36+00:00")  # boot time
    entry = await _setup(hass, loads=[_load("Pool", POOL, 750, 1, **{CONF_CIRCUIT_SENSOR: "sensor.circuit"})])
    sensor = _entity(hass, f"{entry.entry_id}_maintenance", "binary_sensor")
    await _cycle(hass, entry)
    assert hass.states.get(sensor).state == "off"

    hass.states.async_set(UPTIME, dt_util.utcnow().isoformat())  # it rebooted after its update
    await _cycle(hass, entry)
    state = hass.states.get(sensor)
    assert state.state == "on" and state.attributes["source"] == "restart"
    assert state.attributes["devices"] == ["depuradora_piscina"]
    freezer.tick(timedelta(minutes=10))
    await _cycle(hass, entry)
    assert hass.states.get(sensor).state == "off"


async def test_device_unavailable_briefly_is_maintenance_and_its_hours_are_not_counted(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=10))
    _device_with_uptime(hass, "100")
    entry = await _setup(hass, pool="on", loads=[_load("Pool", POOL, 750, 1)])
    sensor = _entity(hass, f"{entry.entry_id}_maintenance", "binary_sensor")
    freezer.tick(timedelta(minutes=30))
    await _cycle(hass, entry, export=1000)

    hass.states.async_set(POOL, "unavailable")  # the OTA update
    freezer.tick(timedelta(minutes=5))
    await _cycle(hass, entry, export=1000)
    assert hass.states.get(sensor).attributes["source"] == "unavailable"
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(POOL, "on")
    await _cycle(hass, entry, export=1000)
    # 30 min on, 10 min unknown (not counted), on again since
    assert float(_state(hass, entry, "on_today", POOL)) == pytest.approx(0.5, abs=0.01)
    assert hass.states.get(sensor).state == "on"  # the margin after it came back
    freezer.tick(timedelta(minutes=30))
    await _cycle(hass, entry, export=1000)
    assert float(_state(hass, entry, "on_today", POOL)) == pytest.approx(1.0, abs=0.01)
    assert hass.states.get(sensor).state == "off"


async def test_no_maintenance_without_entity_or_device_events(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await _cycle(hass, entry)
    state = hass.states.get(_entity(hass, f"{entry.entry_id}_maintenance", "binary_sensor"))
    assert state.state == "off" and state.attributes["source"] is None


@pytest.mark.parametrize(("script", "learned"), [("on", 0), ("off", 1)])
async def test_circuit_jumps_are_not_learned_during_maintenance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, script: str, learned: int
) -> None:
    circuit = "sensor.circuit"
    hass.states.async_set(SCRIPT, script)
    hass.states.async_set(circuit, "300", {"unit_of_measurement": "W"})
    entry = await _setup(
        hass, loads=[_load("Pool", POOL, 750, 1, **{CONF_CIRCUIT_SENSOR: circuit})],
        **{CONF_MAINTENANCE_ENTITY: SCRIPT},
    )
    for _ in range(5):
        freezer.tick(timedelta(seconds=5))
        await _cycle(hass, entry)
    hass.states.async_set(POOL, "on")
    hass.states.async_set(circuit, "1100", {"unit_of_measurement": "W"})
    for _ in range(15):
        freezer.tick(timedelta(seconds=5))
        await _cycle(hass, entry)
    assert len(entry.runtime_data.memory[next(iter(entry.subentries))].learned) == learned
