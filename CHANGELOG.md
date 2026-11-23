# Changelog

## 0.2.0 (unreleased)

- Daily target per load, in a collapsible *Daily target* section: hours on today sensor (or SolarSplit's own count), target hours from an entity (`input_number`, `number` or `sensor`) or a fixed value. Once reached, the load is not eligible until the next day (reason `target_reached`) and is switched off after its minimum on time.
- New per-load sensor *Time on today* (h): SolarSplit's own count of the hours the load was on today, reset at local midnight, kept across restarts.
- New load type **Regulable** (for example a water heater behind a diverter), configured in a collapsible *Regulable load* section of the load form: power sensor, limit entity, mode entity and its automatic value, minimum and maximum limit, smallest change written (100 W) and minimum time between writes (30 s). In automatic mode its power counts in the budget and SolarSplit writes its limit: the maximum when nothing ahead of it uses the budget, otherwise what the loads ahead leave (reasons `full_power`, `limited`). In any other mode it is `external` and its power is grid power. SolarSplit never switches it; with its switch off it is blocked. The maximum is written back when SolarSplit stops limiting it (master switch, Manage, other mode, global condition, unload). The State sensor shows the limit as an attribute.
- Unknown data is never 0 W. A missing or frozen grid reading makes the cycle decide nothing and publish the reason `grid_unknown` (the budget becomes unknown; entities stay available). New setting *Stale data timeout* (20 s): a non-zero grid reading not reported for longer, or both grid sensors quiet at once, counts as unknown.
- An unknown zero-injection entity pauses the loop (`mode_unknown`); an unknown global or usage condition leaves its loads as they are (`global_unknown`, `condition_unknown`) instead of switching them off. A variable load whose power or regulation entity cannot be read is unavailable.
- A load switched off by something else (a safety cut, a person, an automation) is `blocked` with reason `switched_off` and SolarSplit never switches it back on, even without *Respect manual changes*, until it is on again or its Manage switch is turned off and on. Not detected in shadow mode.
- New brand: icon and README banner, with SVG sources in `images/`. The orange logos and `scripts/render_brand.py` are gone.

## 0.1.0 (unreleased)

- Loads as config subentries: add, edit and delete each load from the UI.
- Control loop (coordinator): reads the grid sensors, smooths them, builds every load's state, stores the engine's delay marks and, outside shadow mode, switches the chosen load (`switch`, `input_boolean` or `climate`).
- Shadow mode setting, on by default: the loop decides and publishes without switching anything.
- Entities: master switch, available budget, assigned power, mode and last decision; per load, a managed switch, state and reason sensors and a priority number.
- Manual changes: a load switched by someone else is left alone until it returns to the state SolarSplit set, or its Manage switch is turned on again.
- Diagnostics with the settings, the loads and the last cycle.
- Persistent memory (Home Assistant store): what SolarSplit switched, each load's last transition time, delay marks (dropped after a gap of more than 2 minutes), the master and Manage switches. After a reload or restart a load switched on by SolarSplit stays managed; one changed while Home Assistant was stopped becomes manual. Minimum on/off times use SolarSplit's own transition time, which survives restarts.
- Priority changes from the priority entity apply on the fly without reloading, and are stored in the load's subentry.
- Global condition off: loads switched on by SolarSplit that respect the condition are switched off (reason `global_shutdown`, no off delay, minimum on time respected). Manual loads, loads that ignore the condition and the master switch behave as before. In shadow mode the decision is only published.
- Every entity gets the label "Gestión de excedentes", created if missing.
- README: step-by-step install from `main` through a HACS custom repository and load setup, to try it in shadow mode.
- UI descriptions of the global condition and *Respect the global condition* explain the switch-off.
- Validate workflow runs ruff over the whole repository.
- Engine: unavailable loads and loads held by the global condition are left as they are, and their power is not reassigned.
- Requires Home Assistant 2025.6 or later.

## 0.0.2

- Engine: per-load switch-on and switch-off delays. The engine returns, per load, since when each condition holds (`on_since`, `off_since`); the caller stores the marks and passes them back. A delay not yet met yields the `on_delay` / `off_delay` reason.
- Brand assets (icon and logos) for the HACS brands check, rendered by `scripts/render_brand.py`.
- Validate workflow also runs ruff.

## 0.0.1

- Project skeleton: manifest, HACS metadata, Validate workflow and design document.
- Config and options flows for the global settings (grid sensors, optional PV, zero-injection and global condition entities, loop tuning).
- Pure control engine (`engine.py`): budget, priority walk with skips and "no skip", on/off hysteresis, settle time, minimum on/off times, variable, manual and unmanaged loads. Not yet wired to Home Assistant.
