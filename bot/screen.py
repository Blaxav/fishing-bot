"""Screen capture and bobber detection using mss + OpenCV."""

import time
import os
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import mss

_BOBBER_PATH = Path(__file__).parent.parent / "bobber.png"
_region: Optional[dict] = None
_monitor_index: int = int(os.environ.get("WOWBOT_MONITOR", "1"))

# Template match threshold for finding the bobber
# INCREASED: was 0.5, but that matched random UI elements on wrong monitor
MATCH_THRESHOLD = float(os.environ.get("WOWBOT_BOBBER_THRESHOLD", "0.5"))

# When the match score drops below this ratio of the initial score,
# the bobber is considered submerged (fish bite detected)
DISAPPEAR_RATIO = 0.45

# Polling interval while waiting for bite
POLL_INTERVAL = 0.2

# Max wait time for a bite (seconds)
MAX_WAIT_TIME = 25.0


def set_region(region: Optional[dict]) -> None:
    global _region
    _region = region


def capture() -> np.ndarray:
    with mss.mss() as sct:
        if _region is not None:
            monitor = dict(_region)
            monitor["mon"] = -1
        else:
            monitor = sct.monitors[_monitor_index]
        sct_img = sct.grab(monitor)
        return np.array(sct_img)[:, :, :3]


def _load_template() -> Optional[np.ndarray]:
    if not _BOBBER_PATH.exists():
        print(f"[ERROR] Bobber template not found: {_BOBBER_PATH}")
        return None
    template = cv2.imread(str(_BOBBER_PATH))
    if template is None:
        print(f"[ERROR] Failed to load: {_BOBBER_PATH}")
        return None
    return template


def _abs_to_local(abs_x: int, abs_y: int) -> Tuple[int, int]:
    if _region is not None:
        return (abs_x - _region["left"], abs_y - _region["top"])
    return (abs_x, abs_y)


def find_bobber() -> Optional[Tuple[int, int]]:
    """Find bobber center in absolute screen coordinates."""
    template = _load_template()
    if template is None:
        return None

    frame = capture()
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    print(f"[Bot] Bobber search: best_match={max_val:.4f} (threshold={MATCH_THRESHOLD})")

    if max_val < MATCH_THRESHOLD:
        return None

    h, w = template.shape[:2]
    rel_x = max_loc[0] + w // 2
    rel_y = max_loc[1] + h // 2

    if _region is not None:
        return (rel_x + _region["left"], rel_y + _region["top"])
    return (rel_x, rel_y)


def wait_for_bite(bobber_pos: Tuple[int, int], timeout: float = MAX_WAIT_TIME) -> bool:
    """Monitor bobber until it submerges (template match drops)."""
    template = _load_template()
    if template is None:
        return False

    bx, by = bobber_pos
    start_time = time.time()

    frame = capture()
    fh, fw = frame.shape[:2]
    lx, ly = _abs_to_local(bx, by)

    margin = 120
    h, w = template.shape[:2]
    sx1 = max(0, lx - margin)
    sy1 = max(0, ly - margin)
    sx2 = min(fw, lx + margin)
    sy2 = min(fh, ly + margin)

    search_area = frame[sy1:sy2, sx1:sx2]
    if search_area.shape[0] < h or search_area.shape[1] < w:
        search_area = frame

    result = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
    _, baseline_score, _, _ = cv2.minMaxLoc(result)
    print(f"[Bot] Baseline bobber match: {baseline_score:.4f}")

    last_known_pos = bobber_pos

    while time.time() - start_time < timeout:
        frame = capture()
        lx, ly = _abs_to_local(bx, by)
        sx1 = max(0, lx - margin)
        sy1 = max(0, ly - margin)
        sx2 = min(fw, lx + margin)
        sy2 = min(fh, ly + margin)

        search_area = frame[sy1:sy2, sx1:sx2]
        if search_area.shape[0] < h or search_area.shape[1] < w:
            time.sleep(POLL_INTERVAL)
            continue

        result = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
        _, match_score, _, match_loc = cv2.minMaxLoc(result)

        if match_score >= MATCH_THRESHOLD:
            new_rel_x = sx1 + match_loc[0] + w // 2
            new_rel_y = sy1 + match_loc[1] + h // 2
            if _region is not None:
                bx = new_rel_x + _region["left"]
                by = new_rel_y + _region["top"]
            else:
                bx = new_rel_x
                by = new_rel_y
            last_known_pos = (bx, by)

        threshold = baseline_score * DISAPPEAR_RATIO
        if match_score < threshold:
            print(f"[Bot] Bobber submerged! (score: {match_score:.4f} < {threshold:.4f})")
            return True

        time.sleep(POLL_INTERVAL)

    return False


def get_last_position() -> Optional[Tuple[int, int]]:
    return find_bobber()


# ── Debug / calibration tools ──────────────────────────────────────────

_DEBUG_WINDOW_NAME = "WoW Bot - Bobber Tracker"


def _is_visual_debug() -> bool:
    """Check env var dynamically (so .env loading in main.py takes effect)."""
    return os.environ.get("WOWBOT_VISUAL_DEBUG", "0") == "1"


def set_visual_debug(enabled: bool) -> None:
    """Override visual debug (not needed with .env, but kept for API)."""
    pass


def _draw_debug_overlay(frame: np.ndarray, bobber_pos: Tuple[int, int],
                         template: np.ndarray, match_score: float = 0.0,
                         status: str = "") -> None:
    """Draw debug annotations and show in a window."""
    if not _is_visual_debug():
        return

    annotated = frame.copy()
    h, w = template.shape[:2]
    bx, by = bobber_pos

    # Convert to frame-local coordinates
    if _region is not None:
        lx = bx - _region["left"]
        ly = by - _region["top"]
    else:
        lx, ly = bx, by

    # Draw red rectangle around bobber
    pt1 = (lx - w // 2, ly - h // 2)
    pt2 = (lx + w // 2, ly + h // 2)
    cv2.rectangle(annotated, pt1, pt2, (0, 0, 255), 3)

    # Draw crosshair at center
    cv2.line(annotated, (lx - 10, ly), (lx + 10, ly), (0, 0, 255), 2)
    cv2.line(annotated, (lx, ly - 10), (lx, ly + 10), (0, 0, 255), 2)

    # Status text
    lines = [
        f"Score: {match_score:.3f}",
        f"Pos: ({bx}, {by})",
        status,
    ]
    y_offset = 30
    for line in lines:
        if line:
            cv2.putText(annotated, line, (10, y_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            y_offset += 20

    # Resize for display (max 800px wide)
    scale = min(800 / annotated.shape[1], 1.0)
    if scale < 1.0:
        new_w = int(annotated.shape[1] * scale)
        new_h = int(annotated.shape[0] * scale)
        display = cv2.resize(annotated, (new_w, new_h))
    else:
        display = annotated

    cv2.imshow(_DEBUG_WINDOW_NAME, display)
    cv2.waitKey(1)


def show_finding(frame: np.ndarray, bobber_pos: Tuple[int, int],
                  score: float) -> None:
    """Show debug overlay when bobber is first found."""
    template = _load_template()
    if template is not None:
        _draw_debug_overlay(frame, bobber_pos, template, score,
                            "FOUND - Waiting for bite...")


def show_tracking(frame: np.ndarray, bobber_pos: Tuple[int, int],
                   score: float) -> None:
    """Show debug overlay while tracking bobber."""
    template = _load_template()
    if template is not None:
        _draw_debug_overlay(frame, bobber_pos, template, score,
                            "TRACKING - Watching bobber...")


def show_bite(frame: np.ndarray, bobber_pos: Tuple[int, int]) -> None:
    """Show debug overlay when bite detected."""
    template = _load_template()
    if template is not None:
        _draw_debug_overlay(frame, bobber_pos, template, 0.0,
                            "!!! BITE !!! Clicking now...")


def close_debug_window() -> None:
    """Close the visual debug window."""
    try:
        cv2.destroyWindow(_DEBUG_WINDOW_NAME)
    except cv2.error:
        pass


def debug_bobber() -> None:
    """Capture frame, find bobber, save annotated image."""
    template = _load_template()
    if template is None:
        print("[ERROR] No bobber template found.")
        return

    frame = capture()
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    print(f"Best match score: {max_val:.4f} (threshold: {MATCH_THRESHOLD})")
    print(f"Monitor index: {_monitor_index}")

    h, w = template.shape[:2]
    annotated = frame.copy()
    loc = np.where(result >= 0.4)
    for pt in zip(*loc[::-1]):
        cv2.rectangle(annotated, pt, (pt[0] + w, pt[1] + h), (0, 0, 255), 2)

    if max_val >= 0.4:
        cv2.rectangle(annotated, max_loc, (max_loc[0] + w, max_loc[1] + h), (0, 255, 0), 3)

    save_path = Path(__file__).parent.parent / "bobber_debug.png"
    cv2.imwrite(str(save_path), annotated)
    print(f"Annotated image saved to: {save_path}")
    print(f"Monitor resolution: {frame.shape[1]}x{frame.shape[0]}")
    print()
    print("Check bobber_debug.png — green = best match, red = other matches > 0.4")
    print(f"If bobber isn't marked: try WOWBOT_MONITOR=2 or WOWBOT_MONITOR=0")


def list_monitors() -> None:
    """Print available monitors from mss."""
    with mss.mss() as sct:
        for i, m in enumerate(sct.monitors):
            print(f"  Monitor [{i}]: {m['width']}x{m['height']} at ({m['left']}, {m['top']})")
