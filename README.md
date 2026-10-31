# SolarSplit

[![Validate](https://github.com/jdgarcia99/ha-solarsplit/actions/workflows/validate.yml/badge.svg)](https://github.com/jdgarcia99/ha-solarsplit/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz)

SolarSplit is a Home Assistant integration that shares your PV surplus among loads ordered by priority (pool pump, water heater, air conditioners, radiators…). One control loop replaces the usual pairs of on/off automations: every cycle it works out how much power is available, decides which loads should run, and makes at most one change, with hysteresis and minimum on/off times.

> **Status: early development.** The control engine and the global configuration are in place; loads, entities and the control loop are not yet. See the [design document](docs/design.md) (in Spanish) and the [changelog](CHANGELOG.md).

## How it works

Each cycle computes the available budget from smoothed grid readings:

```
budget = export − import + power of the managed loads that are on − safety margin
```

Power already used by managed loads is added back, because it can be reassigned. Loads are then walked in priority order: each one enters if it fits in what is left of the budget. The engine compares the desired and the real states and makes a single change: switch-offs first (lowest priority first), then switch-ons (highest priority first), always respecting the settle time and each load's minimum on and off times.

Planned on top of that: zero-injection mode (probing loads when the inverter limits export), hourly net balance mode, variable loads regulated elsewhere (FreeDS), manual override detection, and status and reason entities for every load.

## Installation

1. In HACS, add `https://github.com/jdgarcia99/ha-solarsplit` as a custom repository of type *Integration*.
2. Install **SolarSplit** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → SolarSplit**.

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
