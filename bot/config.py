"""Persistent, machine-specific bot configuration (config.json).

Calibration results live here rather than in ``.env``: they are numbers written
by ``python main.py --calibrate``, not environment settings, and re-running the
calibration updates the file directly instead of asking the user to copy
variables around.

The file is optional. Without it the bot scans the full primary monitor.
"""

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_SCAN_KEYS = ("left", "top", "width", "height")


def load() -> dict:
    """Return the parsed config, or an empty dict when missing/invalid."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[Config] Ignoring {CONFIG_PATH.name}: {exc}")
        return {}

    if not isinstance(data, dict):
        print(f"[Config] Ignoring {CONFIG_PATH.name}: expected a JSON object.")
        return {}
    return data


def save(config: dict) -> Path:
    """Write the config atomically, then return its path.

    Writing to a temporary file first means an interrupted save (Ctrl+C, full
    disk) cannot leave a truncated config.json behind.
    """
    tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, CONFIG_PATH)
    return CONFIG_PATH


def _as_int(value) -> Optional[int]:
    """Coerce a JSON value to int, or return None when it is not numeric."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_scan_region() -> Optional[dict]:
    """Return the saved scan region, or None when absent/invalid.

    The returned dict has exactly the keys mss expects: left, top, width, height.
    """
    region = load().get("scan_region")
    if not isinstance(region, dict):
        return None

    values = {key: _as_int(region.get(key)) for key in _SCAN_KEYS}
    if any(value is None for value in values.values()):
        print(f"[Config] scan_region in {CONFIG_PATH.name} is incomplete; ignoring it.")
        return None
    if values["width"] <= 0 or values["height"] <= 0:
        print(f"[Config] scan_region in {CONFIG_PATH.name} is empty; ignoring it.")
        return None
    return values


def set_scan_region(region: dict) -> Path:
    """Persist a scan region, leaving every other config key untouched."""
    values = {key: int(region[key]) for key in _SCAN_KEYS}
    config = load()
    config["scan_region"] = values
    return save(config)
