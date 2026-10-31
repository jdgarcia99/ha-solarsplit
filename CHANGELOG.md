# Changelog

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
- README: how to try it in shadow mode.
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
