# SolarSplit

Custom Home Assistant integration (domain `solarsplit`), distributed through HACS from the public repository `jdgarcia99/ha-solarsplit`. It replaces the "Gestión de excedentes" automations with one control loop that shares the PV surplus among loads ordered by priority. The full design, in Spanish, is in `docs/design.md`: read it before adding features, and keep it up to date when a decision changes.

## Layout

- `custom_components/solarsplit/`
  - `engine.py`: the control algorithm as a **pure function** (`decide()`): measurements and load states in, one `Decision` (at most one action, plus a `Reason` code per load) out. No Home Assistant imports, ever: it must stay testable with plain pytest and replayable over real days exported from InfluxDB.
  - `config_flow.py`: config flow (single entry) and options flow for the global settings. Both share `settings_schema()`.
  - `const.py`: `CONF_*` keys, `DEFAULT_*` values and limits.
  - `__init__.py`: `DEFAULTS`, `get_settings()` and entry setup.
  - `strings.json`, `translations/en.json`, `translations/es.json`: UI text.
- Planned (see the design): config subentries per load, `coordinator.py` (loop, smoothing, sensor reading, stable-for-delay hysteresis), `loads.py` (switch / climate / input_boolean / variable adapters), `sensor.py`, `switch.py`, `number.py`, `diagnostics.py`, services `force_on`, `force_off`, `recalculate`, and a shadow mode that decides without acting.
- `tests/`: pytest with `pytest-homeassistant-custom-component`. `test_engine.py` covers the engine without Home Assistant; `test_config_flow.py` the flows.
- `docs/design.md`: design document.
- `CHANGELOG.md`: one section per version.
- `.github/workflows/validate.yml`: **Validate** workflow (hassfest, HACS action and tests). It must stay green.

## Safety

The integration switches real devices (pool pump, air conditioners, water heater). Compressors and pumps must be protected by minimum on/off times; when in doubt, do nothing and report the reason. The control loop never switches loads off because it is paused or its global condition fails: it leaves them as they are.

## Conventions

### Language
- Code, comments, docstrings, README, release notes and commit messages are in **English**. The design document is in Spanish.
- The user interface is translated: every user-facing string lives in `translations/en.json` and `translations/es.json`. Never hard-code UI text in Python; the engine returns `Reason` codes that the UI translates.
- `strings.json` must be **identical** to `translations/en.json`. When a string changes, update `strings.json`, `en.json` and `es.json` together.

### Configuration
- Everything configurable goes in the **config flow, the options flow or a load subentry**. No YAML configuration.
- A new global setting needs: a `CONF_*` key and a `DEFAULT_*` value in `const.py`, an entry in `DEFAULTS` in `__init__.py`, a field in `settings_schema()`, labels and descriptions in both translations (under `config.step.user` and `options.step.init`), and a row in the README *Settings* table.
- Read settings through `get_settings(entry)` (defaults, then data, then options). Changing options reloads the entry.

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
