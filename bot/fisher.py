"""Main fishing bot logic — orchestrates the fishing loop.

Uses bobber disappearance detection: when the bobber submerges
(template match score drops), the fish has bitten.
"""

import time
import threading
from typing import Optional

import cv2

from . import input as inp
from . import screen
from .audio import AudioMonitor


class FisherBot:
    """Orchestrates the WoW Classic fishing loop.

    Flow:
        1. Press fishing key (1) to cast
        2. Wait for bobber to appear on screen
        3. Monitor bobber — when it submerges, fish bit
        4. Right-click to catch
        5. Repeat until stopped

    If WOWBOT_VISUAL_DEBUG=1 in .env, shows a live overlay window
    with the bobber highlighted.
    """

    def __init__(
        self,
        fishing_key: str = "1",
        cast_delay: float = 1.5,
        post_loot_delay: float = 2.0,
        bobber_search_retries: int = 10,
        bobber_search_interval: float = 0.5,
    ):
        self.fishing_key = fishing_key
        self.cast_delay = cast_delay
        self.post_loot_delay = post_loot_delay
        self.bobber_search_retries = bobber_search_retries
        self.bobber_search_interval = bobber_search_interval

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._audio: Optional[AudioMonitor] = None

    def start(self) -> None:
        """Start the fishing loop in a background thread."""
        if self._running:
            print("[Bot] Already running.")
            return

        self._running = True

        # Start audio monitor
        if self._audio is None:
            self._audio = AudioMonitor()
        self._audio.start()

        self._thread = threading.Thread(target=self._loop)
        self._thread.start()
        print("[Bot] Started. Press Ctrl+Shift+F to stop.")

    def stop(self) -> None:
        """Stop the fishing loop."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._audio is not None:
            self._audio.stop()
        screen.close_debug_window()
        print("[Bot] Stopping...")

    def _loop(self) -> None:
        """Main fishing loop — runs until stopped."""
        while self._running:
            print("[Bot] Casting line...")
            inp.press_fishing_key(self.fishing_key)
            time.sleep(self.cast_delay)

            # Wait for bobber to appear
            bobber_pos, match_score = self._wait_for_bobber_with_score()
            if bobber_pos is None:
                print("[Bot] Failed to find bobber, recasting...")
                continue

            print(f"[Bot] Bobber found at {bobber_pos}, watching...")

            # Show visual debug overlay
            frame = screen.capture()
            screen.show_finding(frame, bobber_pos, match_score)

            # Wait for bobber to submerge (fish bite) with tracking
            bite = self._wait_for_bite_with_tracking(bobber_pos)

            if bite:
                print("[Bot] Fish! Right-clicking bobber...")
                frame = screen.capture()
                screen.show_bite(frame, bobber_pos)

                fresh_pos = screen.get_last_position()
                click_pos = fresh_pos if fresh_pos is not None else bobber_pos

                # Right-click to catch (required in WoW Classic)
                inp.right_click(*click_pos)
                time.sleep(0.3)
                inp.right_click(*click_pos)
                time.sleep(self.post_loot_delay)
            else:
                print("[Bot] Bobber timed out, recasting...")

        screen.close_debug_window()
        print("[Bot] Stopped.")

    def _wait_for_bobber_with_score(self) -> tuple:
        """Repeatedly scan for bobber, returning position and match score.
        Times out after bobber_search_retries * bobber_search_interval (~5s)."""
        for i in range(self.bobber_search_retries):
            if not self._running:
                return None, 0.0
            pos = screen.find_bobber()
            if pos is not None:
                # Re-verify with a fresh match to get the actual score
                template = screen._load_template()
                frame = screen.capture()
                if template is not None:
                    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(result)
                    # Double-check: reject weak matches (water, UI, etc.)
                    if max_val < 0.5:
                        print(f"[Bot] Bobber match too weak ({max_val:.3f}), retrying...")
                        continue
                    return pos, max_val
                return pos, 0.0
            time.sleep(self.bobber_search_interval)
        return None, 0.0

    def _wait_for_bite_with_tracking(self, bobber_pos: tuple) -> bool:
        """Monitor bobber with visual tracking overlay."""
        import cv2
        import numpy as np

        template = screen._load_template()
        if template is None:
            return False

        bx, by = bobber_pos
        start_time = time.time()

        frame = screen.capture()
        fh, fw = frame.shape[:2]

        margin = 120
        h, w = template.shape[:2]

        if screen._region is not None:
            lx = bx - screen._region["left"]
            ly = by - screen._region["top"]
        else:
            lx, ly = bx, by

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

        # Clear any stale audio detection before starting
        if self._audio is not None:
            self._audio.clear_bite()

        last_known_pos = bobber_pos
        MATCH_THRESHOLD = 0.5
        DISAPPEAR_RATIO = 0.45
        POLL_INTERVAL = 0.2

        while time.time() - start_time < 25.0:
            if not self._running:
                return False

            # Check audio splash detection
            if self._audio is not None and self._audio.bite_detected():
                print("[Bot] Audio splash detected! Fish on the line!")
                return True

            frame = screen.capture()

            if screen._region is not None:
                lx = bx - screen._region["left"]
                ly = by - screen._region["top"]
            else:
                lx, ly = bx, by

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
                if screen._region is not None:
                    bx = new_rel_x + screen._region["left"]
                    by = new_rel_y + screen._region["top"]
                else:
                    bx = new_rel_x
                    by = new_rel_y
                last_known_pos = (bx, by)

            # Show tracking overlay
            screen.show_tracking(frame, last_known_pos, match_score)

            threshold = baseline_score * DISAPPEAR_RATIO
            if match_score < threshold:
                print(f"[Bot] Bobber submerged! (score: {match_score:.4f} < {threshold:.4f})")
                return True

            time.sleep(POLL_INTERVAL)

        return False