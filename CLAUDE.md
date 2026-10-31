# SolarSplit

Custom Home Assistant integration (domain `solarsplit`), distributed through HACS from the public repository `jdgarcia99/ha-solarsplit`. It replaces the "Gestión de excedentes" automations with one control loop that shares the PV surplus among loads ordered by priority. The full design, in Spanish, is in `docs/design.md`: read it before adding features, and keep it up to date when a decision changes.

## Layout

- `custom_components/solarsplit/`
  - `engine.py`: the control algorithm as a **pure function** (`decide()`): measurements and load states in, one `Decision` (at most one action, plus a `Reason` code per load) out. No Home Assistant imports, ever: it must stay testable with plain pytest and replayable over real days exported from InfluxDB. All timing rules live here (settle time, minimum on/off times, on/off delays). State that must survive between cycles comes in through `LoadState` (`last_change`, `on_since`, `off_since`) and the new marks go out in the `Decision`; the coordinator only stores them and passes them back, it never decides.
  - `coordinator.py`: the control loop (`SolarSplitCoordinator`, one per entry). Every cycle it reads the grid sensors, smooths them, turns each load's entities into a `LoadState`, calls `decide()`, stores the returned marks in `LoadMemory` and, only when shadow mode is off, executes the action. It also detects manual changes (`commanded` vs the real state) and builds the `LoadView` (state and reason) the entities show. It never decides.
  - `loads.py`: domain adapters: whether a `switch` / `input_boolean` / `climate` load is on, reading W (also kW), and switching it with the right service (`climate.set_hvac_mode`, `turn_on` / `turn_off`).
  - `config_flow.py`: config flow (single entry), options flow for the global settings (both share `settings_schema()`) and `LoadSubentryFlow`, the subentry flow of a load (add and reconfigure share `load_schema()`).
  - `settings.py`: `DEFAULTS`, `LOAD_DEFAULTS`, `get_settings(entry)` and `load_settings(subentry)`. Re-exported from `__init__.py`.
  - `const.py`: `CONF_*` keys, `DEFAULT_*` values and limits.
  - `__init__.py`: entry setup: coordinator in `entry.runtime_data`, platforms, reload when options or subentries change.
  - `entity.py`: base entity; one service device for the loop and one per load (entities of a load are added with `config_subentry_id`).
  - `sensor.py` (budget, assigned power, mode, last decision; per load state and reason), `switch.py` (master switch and per-load Manage, kept in the coordinator memory saved across restarts), `number.py` (per-load priority, applied on the fly and written to the subentry without reloading), `diagnostics.py`.
  - `strings.json`, `translations/en.json`, `translations/es.json`: UI text, including entity names and enum states.
  - `brand/`: icon and logos, rendered by `scripts/render_brand.py`.
- Not implemented yet (see the design): zero-injection probing (the mode is detected and the engine stays paused), hourly net balance, services `force_on` / `force_off` / `recalculate`, daily targets and template conditions per load, time-on-today sensor.
- `tests/`: pytest with `pytest-homeassistant-custom-component`. `test_engine.py` covers the engine without Home Assistant; `test_config_flow.py` the config, options and load subentry flows; `test_coordinator.py` the loop inside Home Assistant (shadow mode, acting, manual changes, switches, delays). `conftest.py` creates the grid sensors. Register `async_mock_service` mocks **after** setting up the entry: loading the `switch` platform replaces earlier `switch.*` mocks.
- `docs/design.md`: design document.
- `CHANGELOG.md`: one section per version.
- `.github/workflows/validate.yml`: **Validate** workflow (hassfest, HACS action and tests). It must stay green.

## Safety

The integration switches real devices (pool pump, air conditioners, water heater). Compressors and pumps must be protected by minimum on/off times; when in doubt, do nothing and report the reason. The control loop never switches loads off because it is paused (master switch off, unknown mode): it leaves them as they are. The only exception is the global condition: when it fails, it switches off the relay loads it switched on itself that respect the condition (`global_shutdown`), still honouring the minimum on time; manual loads are left alone. Shadow mode is on by default and must stay the default: a new install never acts until the user turns it off.

## Conventions

### Language
- Code, comments, docstrings, README, release notes and commit messages are in **English**. The design document is in Spanish.
- The user interface is translated: every user-facing string lives in `translations/en.json` and `translations/es.json`. Never hard-code UI text in Python; the engine returns `Reason` codes that the UI translates.
- `strings.json` must be **identical** to `translations/en.json`. When a string changes, update `strings.json`, `en.json` and `es.json` together.

### Configuration
- Everything configurable goes in the **config flow, the options flow or a load subentry**. No YAML configuration.
- A new global setting needs: a `CONF_*` key and a `DEFAULT_*` value in `const.py`, an entry in `DEFAULTS` in `settings.py`, a field in `settings_schema()`, labels and descriptions in both translations (under `config.step.user` and `options.step.init`), and a row in the README *Settings* table.
- A new load setting needs the same, with `LOAD_DEFAULTS`, `load_schema()`, both subentry steps in the translations (`config_subentries.load.step.user` and `.reconfigure`), a field in `LoadConfig` and a row in the README *Loads* table.
- Read settings through `get_settings(entry)` (defaults, then data, then options) and `load_settings(subentry)`. Changing options or a subentry reloads the entry.

### Every change
- Includes **tests** that cover it. Engine behaviour is tested in `test_engine.py` without Home Assistant.
- **Bumps `version` in `custom_components/solarsplit/manifest.json`** (semantic versioning: patch for fixes, minor for features).
- Updates the README when behaviour or settings change, and adds an entry to `CHANGELOG.md`.
- Keeps the style of the surrounding code: type hints, short docstrings, Home Assistant import layout.

### Commits and releases
- Imperative subject in English (`Add …`, `Fix …`), focused commits, linear history on `main`.
- A release is a tag `v<version>` matching `manifest.json`, created with `gh release create`, with brief English notes.
- After pushing, check that the **Validate** workflow passes (`gh run list`, `gh run watch`).

## Tests

On Linux, macOS, WSL or CI:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest tests
```

Home Assistant's test tools do not run on plain Windows (`fcntl`, `resource` and asyncio's TCP socketpair). On the maintainer's Windows machine, which has no WSL or Docker, local shims live in the git-ignored `.dev/winshim/`:

```bash
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements_test.txt
PYTHONPATH=.dev/winshim .venv/Scripts/python -m pytest -p win_socketpair tests -q
```

The shims are only for local runs and are never committed; CI on Linux is the reference.
