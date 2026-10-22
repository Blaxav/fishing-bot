"""Self-test for bot/config.py - no game, screen or real config needed.

Every test points `config.CONFIG_PATH` at a throwaway file in a temp directory,
so the repository's config.json is never modified.

Run:  .venv\\Scripts\\python.exe test_config.py
"""

import contextlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from bot import config

REGION = {"left": 1001, "top": 361, "width": 637, "height": 343}


@contextlib.contextmanager
def temp_config(initial=None):
    """Yield a path to a throwaway config.json (optionally pre-filled)."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        if initial is not None:
            path.write_text(initial, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            yield path


def test_missing_config_gives_no_region():
    with temp_config() as path:
        assert not path.exists()
        assert config.get_scan_region() is None


def test_set_scan_region_round_trips():
    with temp_config() as path:
        written = config.set_scan_region(REGION)
        assert written == path, written
        assert json.loads(path.read_text(encoding="utf-8"))["scan_region"] == REGION
        assert config.get_scan_region() == REGION


def test_saved_file_is_human_readable():
    with temp_config() as path:
        config.set_scan_region(REGION)
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n"), "file should end with a newline"
        assert '"left": 1001' in text, text


def test_set_scan_region_keeps_other_keys():
    with temp_config(initial='{"WOWBOT_SOMETHING": 5}') as path:
        config.set_scan_region(REGION)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["WOWBOT_SOMETHING"] == 5, data
        assert data["scan_region"] == REGION, data


def test_no_temp_file_left_behind():
    with temp_config() as path:
        config.set_scan_region(REGION)
        leftovers = [p.name for p in path.parent.iterdir() if p.name != "config.json"]
        assert not leftovers, leftovers


def test_broken_json_is_ignored():
    with temp_config(initial="{not json") as path:
        assert config.load() == {}
        assert config.get_scan_region() is None
        # Saving afterwards must still produce a valid file
        config.set_scan_region(REGION)
        assert json.loads(path.read_text(encoding="utf-8"))["scan_region"] == REGION


def test_non_object_json_is_ignored():
    with temp_config(initial="[1, 2, 3]"):
        assert config.load() == {}
        assert config.get_scan_region() is None


def test_incomplete_region_is_ignored():
    with temp_config(initial='{"scan_region": {"left": 10, "top": 20}}'):
        assert config.get_scan_region() is None


def test_non_numeric_region_is_ignored():
    initial = '{"scan_region": {"left": "x", "top": 1, "width": 10, "height": 10}}'
    with temp_config(initial=initial):
        assert config.get_scan_region() is None


def test_zero_area_region_is_ignored():
    initial = '{"scan_region": {"left": 0, "top": 0, "width": 0, "height": 10}}'
    with temp_config(initial=initial):
        assert config.get_scan_region() is None


def test_region_values_are_coerced_to_int():
    initial = '{"scan_region": {"left": "1001", "top": 361.0, "width": 637, "height": 343}}'
    with temp_config(initial=initial):
        region = config.get_scan_region()
        assert region == REGION, region
        assert all(isinstance(value, int) for value in region.values()), region


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"[FAIL] {test.__name__}: {exc}")
        else:
            print(f"[ OK ] {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
