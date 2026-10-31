# Changelog

## 0.0.2

- Engine: per-load switch-on and switch-off delays. The engine returns, per load, since when each condition holds (`on_since`, `off_since`); the caller stores the marks and passes them back. A delay not yet met yields the `on_delay` / `off_delay` reason.
- Brand assets (icon and logos) for the HACS brands check, rendered by `scripts/render_brand.py`.
- Validate workflow also runs ruff.

## 0.0.1

- Project skeleton: manifest, HACS metadata, Validate workflow and design document.
- Config and options flows for the global settings (grid sensors, optional PV, zero-injection and global condition entities, loop tuning).
- Pure control engine (`engine.py`): budget, priority walk with skips and "no skip", on/off hysteresis, settle time, minimum on/off times, variable, manual and unmanaged loads. Not yet wired to Home Assistant.
