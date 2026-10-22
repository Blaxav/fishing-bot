"""Self-test for the splash detector - no audio hardware required.

Feeds synthetic level patterns straight into `AudioMonitor._process_block`
(the function the PortAudio callback calls) and checks the trigger decisions.

Run:  .venv\\Scripts\\python.exe test_audio_detection.py

Levels used below were measured on a real setup (Stereo Mix loopback):
    digital silence ~0.00002 | gameplay ambience ~0.0008 | splash ~0.0031
"""

import builtins
import os
import time

# Deterministic tuning, independent of .env / the shell environment
os.environ["WOWBOT_MIN_AMP"] = "0.0015"
os.environ["WOWBOT_SPIKE_MULT"] = "2.0"
os.environ["WOWBOT_AUDIO_DEBUG"] = "0"

import numpy as np  # noqa: E402

from bot import audio  # noqa: E402

FRAMES = int(audio.SAMPLE_RATE * audio.BLOCK_DURATION)  # one callback block


def make_block(rms: float, channels: int = 2, seed: int = 0) -> np.ndarray:
    """Return a noise block (frames, channels) with the requested RMS."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((FRAMES, channels)).astype(np.float32)
    noise /= float(np.sqrt(np.mean(np.square(noise))))
    return noise * rms


class Harness:
    """Drives _process_block and counts triggers."""

    def __init__(self) -> None:
        self.monitor = audio.AudioMonitor(debug=False)
        self.triggers = 0
        self.seed = 0

    def feed(self, rms: float, blocks: int, channels: int = 2) -> int:
        fired = 0
        for _ in range(blocks):
            self.seed += 1
            self.monitor._process_block(make_block(rms, channels, self.seed))
            if self.monitor.bite_detected():
                fired += 1
                self.triggers += 1
                self.monitor.clear_bite()
        return fired


def test_quiet_ambience_is_not_a_bite() -> None:
    h = Harness()
    fired = h.feed(0.0008, blocks=60)  # 3 s of normal gameplay ambience
    assert fired == 0, f"ambience produced {fired} false trigger(s)"


def test_splash_over_ambience_triggers_once() -> None:
    h = Harness()
    h.feed(0.0008, blocks=60)          # settle the noise floor
    fired = h.feed(0.0031, blocks=6)   # 0.3 s splash at the measured level
    h.feed(0.0012, blocks=6)           # ring-down
    assert fired == 1, f"expected exactly 1 trigger, got {fired}"


def test_loud_ambience_does_not_spam() -> None:
    h = Harness()
    h.feed(0.003, blocks=80)  # steady loud music/ambience
    assert h.triggers == 0, f"steady loud audio produced {h.triggers} trigger(s)"


def test_splash_over_loud_ambience() -> None:
    h = Harness()
    h.feed(0.003, blocks=80)
    time.sleep(audio.COOLDOWN_SECONDS + 0.2)
    fired = h.feed(0.012, blocks=6)  # 4x the loud floor
    assert fired == 1, f"expected 1 trigger over loud ambience, got {fired}"


def test_trigger_rate_limits() -> None:
    h = Harness()
    h.feed(0.0008, blocks=40)
    fired = h.feed(0.004, blocks=6)   # burst: must only fire once
    assert fired == 1, f"cooldown did not limit burst to 1 trigger (got {fired})"
    time.sleep(audio.COOLDOWN_SECONDS + 0.2)
    fired = h.feed(0.004, blocks=6)   # same burst after the cooldown
    assert fired == 1, f"second cast splash missed (got {fired})"


def test_soft_splash_is_detected() -> None:
    """Regression: a quiet splash (0.0022) must count.

    The old default MIN_AMPLITUDE (0.03, and 0.005 in .env) was higher than the
    level a real splash reaches through Stereo Mix, so bites were silently
    filtered out.
    """
    h = Harness()
    h.feed(0.0008, blocks=60)
    fired = h.feed(0.0022, blocks=6)
    assert fired == 1, f"soft splash missed ({fired} triggers) - MIN_AMP too high?"


def test_callback_inputs_never_raise() -> None:
    monitor = audio.AudioMonitor(debug=False)
    monitor._process_block(np.empty((0, 2), dtype=np.float32))   # empty
    monitor._process_block(np.zeros((FRAMES,), dtype=np.float32))  # mono
    monitor._process_block(np.zeros((FRAMES, 2), dtype=np.float32))  # stereo
    monitor._process_block(np.zeros((FRAMES, 6), dtype=np.float32))  # surround
    monitor._process_block(np.full((FRAMES, 2), 0.5, dtype=np.float32))  # clipped
    assert monitor.snapshot()["error"] is None


def test_messages_are_ascii_safe() -> None:
    """Regression: a non-ASCII log line used to abort the audio stream."""
    h = Harness()
    h.feed(0.0008, blocks=40)
    h.feed(0.0031, blocks=6)
    monitor = audio.AudioMonitor(debug=True)
    monitor._process_block(make_block(0.0031, seed=99))
    messages = list(h.monitor._messages) + list(monitor._messages)
    assert messages, "no log messages were produced"
    for message in messages:
        message.encode("cp1252")  # redirecting stdout on Windows uses cp1252


def test_printing_failure_cannot_break_the_audio_thread() -> None:
    monitor = audio.AudioMonitor(debug=False)
    original_print = builtins.print

    def exploding_print(*args, **kwargs):
        raise UnicodeEncodeError("charmap", "", 0, 1, "character maps to <undefined>")

    builtins.print = exploding_print
    try:
        monitor._emit("[Audio] Splash detected! (test)")
        monitor._drain_messages()  # must swallow the error
    finally:
        builtins.print = original_print
    assert not monitor._messages


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
