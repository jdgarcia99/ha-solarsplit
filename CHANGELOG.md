# Changelog

## 0.0.1

- Project skeleton: manifest, HACS metadata, Validate workflow and design document.
- Config and options flows for the global settings (grid sensors, optional PV, zero-injection and global condition entities, loop tuning).
- Pure control engine (`engine.py`): budget, priority walk with skips and "no skip", on/off hysteresis, settle time, minimum on/off times, variable, manual and unmanaged loads. Not yet wired to Home Assistant.
