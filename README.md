# SolarSplit

[![Validate](https://github.com/jdgarcia99/ha-solarsplit/actions/workflows/validate.yml/badge.svg)](https://github.com/jdgarcia99/ha-solarsplit/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz)

SolarSplit is a Home Assistant integration that shares your PV surplus among loads ordered by priority (pool pump, water heater, air conditioners, radiators…). One control loop replaces the usual pairs of on/off automations: every cycle it works out how much power is available, decides which loads should run, and makes at most one change, with hysteresis and minimum on/off times.

> **Status: early development.** The control loop runs in **shadow mode**: it decides and publishes what it would do, without switching any load. Zero-injection probing, hourly net balance, services and per-load daily targets are not implemented yet. See the [design document](docs/design.md) (in Spanish) and the [changelog](CHANGELOG.md).

## How it works

Each cycle computes the available budget from smoothed grid readings:

```
budget = export − import + power of the managed loads that are on − safety margin
```

Power already used by managed loads is added back, because it can be reassigned. Loads are then walked in priority order: each one enters if it fits in what is left of the budget. The engine compares the desired and the real states and makes a single change: switch-offs first (lowest priority first), then switch-ons (highest priority first), always respecting the settle time and each load's minimum on and off times.

A load only switches on after the surplus has covered it for its switch-on delay, and only switches off after the deficit has lasted its switch-off delay. Loads regulated elsewhere (FreeDS) are never switched but their power counts. If someone else switches a load, SolarSplit stops controlling it until it returns to the state SolarSplit set.

When the global condition goes off, SolarSplit switches off the loads it switched on itself and that respect the condition (respecting their minimum on time); loads switched by hand and loads that ignore the condition are left alone. Turning the master switch off, instead, leaves every load as it is.

The loop's memory (what it switched, the time of each load's last change and the delay marks) is saved, so a reload or a restart of Home Assistant does not turn its loads into manual ones. A load changed while Home Assistant was stopped is detected as manual. Delay marks older than two minutes are dropped on restart.

All SolarSplit entities get the label **Gestión de excedentes**, created if it does not exist.

### Shadow mode

Shadow mode is **on by default**. The loop runs every cycle and updates all its entities, but never calls a service on a load. Compare its decisions with your current automations for a while, then turn shadow mode off in the integration's settings. Turning a load's **Manage** switch off, or the master switch off, hands the load back to your automations at any time.

Planned: zero-injection mode (probing loads when the inverter limits export), hourly net balance mode, `force_on` / `force_off` / `recalculate` services, and daily targets per load.

## Entities

The **SolarSplit** device:

| Entity | Description |
| --- | --- |
| Surplus management (switch) | Master switch. Off: the loop leaves every load as it is. |
| Available budget (sensor, W) | Budget of the last cycle. |
| Assigned power (sensor, W) | Power of the managed loads that are on. |
| Mode (sensor) | `normal`, `zero_injection` or `paused`. |
| Last decision (sensor) | `none`, `turn_on` or `turn_off`; attributes `load`, `reason`, `budget` and `shadow` (true if it was only simulated). |

One device per load:

| Entity | Description |
| --- | --- |
| Manage (switch) | Include or exclude the load without deleting it. |
| State (sensor) | `on`, `off`, `waiting` (a change is wanted), `blocked`, `manual`, `unmanaged` or `unavailable`. |
| Reason (sensor) | Why, for example `no_budget`, `on_delay`, `min_on_time` or `not_eligible`. |
| Priority (number) | Applied on the fly, without reloading; also stored in the load's settings. |

Diagnostics (on the integration's menu) include the settings, every load and the last cycle.

## Installation

1. In HACS, add `https://github.com/jdgarcia99/ha-solarsplit` as a custom repository of type *Integration*.
2. Install **SolarSplit** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → SolarSplit**.

## Try it in shadow mode

There are no releases yet; HACS installs the latest commit of `main` and shows its short hash as the version. Every new commit appears as an update.

1. **Install.** HACS → ⋮ → *Custom repositories* → `https://github.com/jdgarcia99/ha-solarsplit`, type *Integration*. Download **SolarSplit** and restart Home Assistant.
2. **Set up.** *Settings → Devices & services → Add integration → SolarSplit*. Choose the grid export and import sensors (for example `sensor.meter_export_to_grid` and the import sensor of the energy meter), the global condition (`input_boolean.casa_activada`) and, if you use it, the zero-injection entity. **Leave Shadow mode on.**
3. **Add the loads** with **Add load**, one per device: real nominal power (or a power sensor), priority (1 first), delays and minimum times as your automations use them today, the usage condition (for example `input_boolean.climatizar_casa` for the air conditioners) and, for the water heater, *Regulated externally when* = the FreeDS master switch. Untick *Respect the global condition* for loads that must run with the house empty, such as the pool pump.
4. **Watch for a week or two**, next to your current automations, which keep doing the real work:
   - **Last decision**: in its history, every `turn_on` / `turn_off` with its `load` and `reason` attributes is what SolarSplit would have done. Compare it with what your automations did.
   - **State** and **Reason** of each load: `waiting` means it wants a change and the reason says what holds it (`on_delay`, `min_on_time`, `settling`…); `blocked` with `no_budget` or `waiting_higher` explains why a load stays off.
   - **Available budget** next to your export sensor in a history graph: it should follow the surplus without jumping with every cloud.
   - **Diagnostics** (integration menu) when something looks wrong: settings, memory of every load and the last cycle.
5. **Tune** delays, margins and priorities until the decisions match what you expect. Priority changes apply at once; other changes reload the integration without losing its memory.

When you are ready to hand over one load, turn **Manage** off on every other load, disable the automations of that load, and turn shadow mode off in the integration's settings. The master switch, or a load's **Manage** switch, gives control back to your automations at any time.

## Settings

All settings live in the UI; there is no YAML. They can be changed later from the integration's **Configure** button.

| Setting | Default | Description |
| --- | --- | --- |
| Grid export sensor | — | Power exported to the grid (W). Main signal of the loop. |
| Grid import sensor | — | Power imported from the grid (W). Used to detect a deficit. |
| PV power sensor | none | PV production (W). Needed for zero-injection mode. |
| Zero-injection mode entity | none | When on, the loop probes loads instead of reading export. |
| Global condition | none | When off, the loop leaves every load as it is. |
| Cycle interval | 5 s | How often the control loop runs. |
| Safety margin | 100 W | Power never handed out to loads. |
| Smoothing window | 30 s | Moving average applied to the readings. |
| Settle time | 30 s | Wait after a change before the next one. |
| Switch-on margin | 100 W | Extra power a load needs, above its own, to be switched on. |
| Import tolerance | 150 W | Grid import tolerated before a load is switched off. |
| Shadow mode | on | Decide and publish decisions without switching any load. |

## Loads

Each load is a subentry: on the integration's page, use **Add load**, and edit or delete it from its menu. An entity can belong to one load only.

| Setting | Default | Description |
| --- | --- | --- |
| Name | — | Shown on the load's device. |
| Entity | — | `switch`, `input_boolean` or `climate` entity that turns the load on and off. |
| Nominal power | — | Power drawn when on (W). |
| Power sensor | none | Measured power; used instead of the nominal power when set. |
| Priority | 10 | 1 is served first. |
| Do not skip | off | Only switch on when every eligible load before it is on. |
| Switch-on delay | 60 s | The surplus must cover the load this long before it is switched on. |
| Switch-off delay | 30 s | The deficit must last this long before the load is switched off. |
| Minimum on / off time | 5 / 5 min | Protects compressors and pumps. |
| Respect the global condition | on | When the global condition is off, leave this load as it is. |
| Usage condition entity / state | none / `on` | The load is only used while that entity has that state. |
| Respect manual changes | on | If someone else switches the load, stop controlling it until it returns to the state SolarSplit set. |
| Climate mode when on | cool | For `climate` entities. |
| Regulated externally when | none | While this entity is on (for example FreeDS master), the load is never switched but its power counts. |

## License

[MIT](LICENSE)
