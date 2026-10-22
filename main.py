"""WoW Classic Fishing Bot — Entry Point.

Default toggle: Ctrl+Shift+F to start/stop fishing.
Press Ctrl+C in the terminal to exit.

Usage:
    python main.py                  # Normal mode
    python main.py --calibrate      # Calibrate screen region
    python main.py --test-input     # Test if key presses reach the game
    python main.py --test-bobber    # Debug bobber detection (saves image)
    python main.py --list-audio     # List capture devices / verify Stereo Mix
    python main.py --audio-debug    # Live audio levels + threshold calibration
    python main.py --list-monitors  # List available monitors

Environment variables:
    WOWBOT_HOTKEY   — Key combo to start/stop (default: "ctrl+shift+f")
    WOWBOT_FISH_KEY — Key to cast fishing (default: "1")
    SCAN_LEFT, SCAN_TOP, SCAN_WIDTH, SCAN_HEIGHT — Limit screen scan region
"""

import os
import sys
import time

# ── Load .env FIRST, before any bot imports ────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# Never let a non-ASCII log line crash the program when stdout is redirected
# to a file or pipe (Windows defaults to a legacy code page there).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import keyboard
from bot import FisherBot
from bot import screen
from bot import input as inp
from bot.audio import AudioMonitor, list_audio_devices


def calibration_mode():
    """Interactive calibration: click two corners to define the scan region."""
    import pyautogui

    print("=" * 50)
    print("  Screen Region Calibration")
    print()
    print("  1. Move your mouse to the TOP-LEFT corner of the game")
    print("     (or the area where the bobber appears)")
    print("  2. Press ENTER in this terminal to capture")
    print("  3. Then move to the BOTTOM-RIGHT corner and press ENTER again")
    print("=" * 50)

    input("\nPress ENTER when mouse is at the TOP-LEFT corner...")
    x1, y1 = pyautogui.position()
    print(f"  Captured: ({x1}, {y1})")

    input("\nPress ENTER when mouse is at the BOTTOM-RIGHT corner...")
    x2, y2 = pyautogui.position()
    print(f"  Captured: ({x2}, {y2})")

    left = min(x1, x2)
    top = min(y1, y2)
    width = abs(x2 - x1)
    height = abs(y2 - y1)

    print()
    print("=" * 50)
    print("  Set these environment variables before launching:")
    print()
    print(f"  set SCAN_LEFT={left}")
    print(f"  set SCAN_TOP={top}")
    print(f"  set SCAN_WIDTH={width}")
    print(f"  set SCAN_HEIGHT={height}")
    print()
    print("  Or in PowerShell:")
    print(f"  $env:SCAN_LEFT={left}")
    print(f"  $env:SCAN_TOP={top}")
    print(f"  $env:SCAN_WIDTH={width}")
    print(f"  $env:SCAN_HEIGHT={height}")
    print("=" * 50)


def _audio_debug_mode() -> None:
    """Standalone audio monitor: live levels + why blocks were rejected.

    Cast in-game, watch the `rms` figure while a fish bites, then set
    WOWBOT_MIN_AMP (and WOWBOT_SPIKE_MULT) in .env accordingly.
    """
    from bot.audio import MIN_AMPLITUDE, SPIKE_THRESHOLD_MULTIPLIER, AudioMonitor

    print("=" * 60)
    print("  Audio Calibration Mode")
    print(f"  min_amp={MIN_AMPLITUDE}  spike_mult={SPIKE_THRESHOLD_MULTIPLIER}")
    print("  Cast in-game and compare 'rms' while waiting vs. at the splash.")
    print("  Press Ctrl+C to stop and get suggested values.")
    print("=" * 60)

    monitor = AudioMonitor(debug=True)
    monitor.start()

    try:
        while True:
            time.sleep(0.25)
            snap = monitor.snapshot()
            floor = snap["floor"]
            ratio = (snap["rms"] / floor) if floor > 0 else 0.0
            print(
                f"[Level] rms={snap['rms']:.5f} peak={snap['peak']:.5f} "
                f"floor={floor:.5f} ratio={ratio:.1f}x "
                f"max={snap['max_rms']:.5f} triggers={snap['triggers']}",
                flush=True,
            )
            if not monitor.healthy():
                print("[Level] WARNING: audio stream is not running!", flush=True)
    except KeyboardInterrupt:
        print("\nStopping...")
        monitor.stop()

    snap = monitor.snapshot()
    loudest = snap["max_rms"]
    print("=" * 60)
    print(f"  Loudest level seen: {loudest:.5f} ({snap['triggers']} trigger(s))")
    if loudest > 0:
        print(f"  Suggested: WOWBOT_MIN_AMP={max(loudest / 4.0, 0.0002):.4f}")
        print("  Keep it well below a splash and above digital silence.")
        if loudest < MIN_AMPLITUDE:
            print("  WARNING: nothing ever exceeded MIN_AMP - lower WOWBOT_MIN_AMP!")
    else:
        print("  No audio captured at all - check Stereo Mix / output device.")
    if snap["error"] is not None:
        print(f"  Detection error: {snap['error']!r}")
    print("=" * 60)


def test_input_mode():
    """Diagnostic: test if key presses reach the game."""
    fishing_key = os.environ.get("WOWBOT_FISH_KEY", "1")
    
    print("=" * 50)
    print("  Input Diagnostic Mode")
    print(f"  Will press '{fishing_key}' 5 times in 10 seconds")
    print()
    print("  1. Focus the game window (click inside it)")
    print("  2. Press ENTER here to start the test")
    print("=" * 50)
    
    input()
    
    print(f"[Test] Pressing '{fishing_key}' in 3 seconds...")
    time.sleep(3)
    
    for i in range(5):
        print(f"[Test] Press #{i + 1}")
        inp.press_fishing_key(fishing_key)
        time.sleep(1.5)
    
    print("[Test] Done. Did you see 5 casts in-game?")
    print("[Test] If not: make sure you run this terminal as Administrator.")


def main():
    # ── Debug modes ───────────────────────────────────────────────────
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return
    if "--list-monitors" in sys.argv:
        screen.list_monitors()
        return
    if "--test-bobber" in sys.argv:
        screen.debug_bobber()
        return
    if "--test-input" in sys.argv:
        test_input_mode()
        return
    if "--calibrate" in sys.argv:
        calibration_mode()
        return
    if "--list-audio" in sys.argv:
        list_audio_devices()
        return
    if "--audio-debug" in sys.argv:
        _audio_debug_mode()
        return

    # ── Hotkey configuration ──────────────────────────────────────────
    toggle_hotkey = os.environ.get("WOWBOT_HOTKEY", "ctrl+shift+f")
    fishing_key = os.environ.get("WOWBOT_FISH_KEY", "1")

    # ── Scan region configuration ─────────────────────────────────────
    region = None
    left = os.environ.get("SCAN_LEFT")
    top = os.environ.get("SCAN_TOP")
    width = os.environ.get("SCAN_WIDTH")
    height = os.environ.get("SCAN_HEIGHT")

    if all(v is not None for v in (left, top, width, height)):
        region = {
            "left": int(left),       # type: ignore[arg-type]
            "top": int(top),          # type: ignore[arg-type]
            "width": int(width),      # type: ignore[arg-type]
            "height": int(height),    # type: ignore[arg-type]
        }
        screen.set_region(region)
        print(f"[Bot] Scan region: {left}x{top} {width}*{height}")
    else:
        print("[Bot] Scanning full primary monitor.")
        print("[Bot] Tip: Run 'python main.py --calibrate' to set a scan region.")

    # ── Create bot ────────────────────────────────────────────────────
    bot = FisherBot(fishing_key=fishing_key)

    def toggle():
        if bot._running:
            bot.stop()
        else:
            bot.start()

    # Register the toggle hotkey
    keyboard.add_hotkey(toggle_hotkey, toggle)

    print("=" * 50)
    print("  WoW Classic Fishing Bot (audio detection)")
    print(f"  Press {toggle_hotkey.upper()} to START / STOP fishing")
    print("  Press Ctrl+C to EXIT")
    print("=" * 50)
    print()
    print("!  Run this terminal as ADMINISTRATOR if inputs are blocked.")
    print("!  If audio fails: python main.py --list-audio")

    try:
        # Polling loop so Ctrl+C works reliably on Windows
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[Bot] Exiting...")
        bot.stop()


if __name__ == "__main__":
    main()