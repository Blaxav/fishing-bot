"""Audio-based fish bite detection using WASAPI loopback.

Captures system audio output and detects amplitude spikes
corresponding to the fishing splash sound in WoW Classic.
"""

import threading
import time
import collections
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False


import os


def _env_float(name: str, default: float) -> float:
    """Read a float from environment variable, falling back to default."""
    val = os.environ.get(name)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            pass
    return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a bool from env var (1/true/yes = True, 0/false/no = False)."""
    val = os.environ.get(name)
    if val is not None:
        return val.lower() in ("1", "true", "yes")
    return default


# ── Tuning constants (overridable via env vars) ───────────────────────

# How far above the noise floor a spike must be to count as a bite.
# This is a *ratio*, so it works at any system volume.
SPIKE_THRESHOLD_MULTIPLIER = _env_float("WOWBOT_SPIKE_MULT", 2.0)

# Absolute minimum amplitude — only a "not digital silence" guard. Keep this
# well below the level a splash reaches on your setup (run --audio-debug and
# look at the `rms` value while a fish bites).
MIN_AMPLITUDE = _env_float("WOWBOT_MIN_AMP", 0.0015)

# How many recent amplitude samples are used for the noise floor.
NOISE_FLOOR_WINDOW = 30

# Ignore the first few blocks so the floor is meaningful.
WARMUP_BLOCKS = 8

# Audio sample rate (Hz).
SAMPLE_RATE = 22050

# How often (seconds) the audio callback delivers a chunk.
BLOCK_DURATION = 0.05

# After a spike is detected, ignore further spikes for this many seconds.
COOLDOWN_SECONDS = _env_float("WOWBOT_COOLDOWN", 0.8)

# Print level information to stdout (for debugging). Override: WOWBOT_AUDIO_DEBUG=1
AUDIO_DEBUG = _env_bool("WOWBOT_AUDIO_DEBUG", False)


class AudioMonitor:
    """Monitors system audio output for bite splash sounds.

    Usage:
        monitor = AudioMonitor()
        monitor.start()
        ...
        if monitor.bite_detected():
            print("Fish!")
        monitor.stop()
    """

    def __init__(self, debug: Optional[bool] = None):
        if not HAS_SOUNDDEVICE:
            raise RuntimeError(
                "sounddevice not installed. Run: uv add sounddevice"
            )

        self._debug = AUDIO_DEBUG if debug is None else debug

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream: Optional[sd.InputStream] = None

        # Rolling buffer of recent amplitude values for noise floor
        self._amplitude_history = collections.deque(maxlen=NOISE_FLOOR_WINDOW)

        # Flags
        self._bite_flag = threading.Event()
        self._last_spike_time = 0.0
        self._lock = threading.Lock()

        # Realtime-safe message queue. The audio callback must never print:
        # stdout is slow and, worse, a print() that raises (e.g. a Unicode
        # character on a non-UTF-8 stdout) propagates through PortAudio and
        # stops the stream for the rest of the session.
        self._messages: collections.deque = collections.deque(maxlen=32)
        self._callback_error: Optional[BaseException] = None
        self._last_debug_print = 0.0

        # Diagnostics (read by --audio-debug)
        self._last_rms = 0.0
        self._last_peak = 0.0
        self._noise_floor = 0.0
        self._trigger_count = 0
        self._max_rms = 0.0

    # ── Status ────────────────────────────────────────────────────────

    def healthy(self) -> bool:
        """True while the capture stream is actually running."""
        try:
            return self._stream is not None and bool(self._stream.active)
        except Exception:  # noqa: BLE001 - closed/invalid stream
            return False

    def snapshot(self) -> dict:
        """Current level information, for calibration/debug output."""
        return {
            "rms": self._last_rms,
            "peak": self._last_peak,
            "floor": self._noise_floor,
            "max_rms": self._max_rms,
            "triggers": self._trigger_count,
            "error": self._callback_error,
        }

    def _emit(self, message: str) -> None:
        """Queue a message for the (non-realtime) audio thread to print."""
        self._messages.append(message)

    def start(self) -> None:
        """Start capturing system audio via WASAPI loopback."""
        if self._running:
            return

        self._running = True
        self._bite_flag.clear()

        # Try to find the WASAPI loopback device
        device = self._find_loopback_device()

        self._thread = threading.Thread(
            target=self._audio_thread,
            args=(device,),
            daemon=True,
        )
        self._thread.start()

        # Wait for audio stream to initialize
        time.sleep(0.3)
        print(f"[Audio] Monitoring system audio for splash sounds...")

    def stop(self) -> None:
        """Stop audio capture."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._drain_messages()
        print("[Audio] Stopped.")

    def bite_detected(self) -> bool:
        """Check if a bite splash was detected.

        Returns True once per detection, then resets the flag.
        """
        return self._bite_flag.is_set()

    def wait_for_bite(self, timeout: float) -> bool:
        """Block until a splash is detected, or until `timeout` seconds pass.

        Lets the fishing loop react to a splash immediately instead of only
        checking the flag every POLL_INTERVAL. Like bite_detected(), this does
        not clear the flag.
        """
        detected = self._bite_flag.wait(timeout)
        if detected:
            # Flush the queued "Splash detected" line so the log stays in
            # order. Safe here: this is not the realtime callback thread.
            self._drain_messages()
        return detected

    def clear_bite(self) -> None:
        """Reset the bite detection flag."""
        self._bite_flag.clear()

    @staticmethod
    def _find_loopback_device():
        """Find a device that can capture system audio output.

        Priority:
        1. "Stereo Mix" / "Mixage stéréo" — records speaker output natively
        2. WASAPI loopback on the default output device
        3. Any input device on the WASAPI host API
        """
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()

        # 1. Search for Stereo Mix / Mixage stéréo by name
        stereo_mix_names = ("stereo mix", "mixage st", "wave out mix", "what u hear", "loopback")
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                name_lower = dev["name"].lower()
                if any(kw in name_lower for kw in stereo_mix_names):
                    print(f"[Audio] Using Stereo Mix device [{idx}]: {dev['name']}")
                    return idx

        # Find WASAPI host API index
        wasapi_api_idx = None
        for i, api in enumerate(host_apis):
            if "WASAPI" in api["name"]:
                wasapi_api_idx = i
                break

        if wasapi_api_idx is not None:
            # 2. Try WASAPI default output device for loopback
            wasapi_default_out = host_apis[wasapi_api_idx].get("default_output_device")
            if wasapi_default_out is not None and 0 <= wasapi_default_out < len(devices):
                dev = devices[wasapi_default_out]
                print(f"[Audio] Using WASAPI output device [{wasapi_default_out}]: {dev['name']}")
                return wasapi_default_out

            # 3. Fallback: any WASAPI input device
            for idx, dev in enumerate(devices):
                if dev["hostapi"] == wasapi_api_idx and dev["max_input_channels"] > 0:
                    print(f"[Audio] Using WASAPI input device [{idx}]: {dev['name']}")
                    return idx

        print("[Audio] No system-audio capture device found.")
        print("[Audio] Enable 'Stereo Mix' in Windows Sound settings -> Recording tab.")
        print("[Audio] Run --list-audio to see available devices.")
        return None

    @staticmethod
    def _get_wasapi_extra():
        """Return WASAPI loopback extra settings for sounddevice."""
        try:
            return sd.WasapiSettings()
        except AttributeError:
            pass

        try:
            from _sounddevice_data.wasapi import WasapiSettings as _WasapiSettings
            return _WasapiSettings()
        except ImportError:
            pass

        return None

    def _drain_messages(self) -> None:
        """Print messages queued by the realtime callback."""
        while self._messages:
            try:
                message = self._messages.popleft()
            except IndexError:
                return
            try:
                print(message, flush=True)
            except Exception:  # noqa: BLE001 - logging must never break audio
                pass

    def _process_block(self, indata) -> None:
        """Analyse one audio chunk (called from the PortAudio callback).

        The level of this chunk is compared with the *median* level of the
        previous NOISE_FLOOR_WINDOW chunks. A median is used instead of a mean
        because a mean is dragged up by the splash itself and by loud ambience,
        which pushed real bites below the ratio test.
        """
        data = np.asarray(indata, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)  # any channel count -> mono
        if data.size == 0:
            return

        rms = float(np.sqrt(np.mean(np.square(data))))
        peak = float(np.max(np.abs(data)))
        now = time.time()

        with self._lock:
            self._last_rms = rms
            self._last_peak = peak
            if rms > self._max_rms:
                self._max_rms = rms

            # Floor comes from the chunks *before* this one
            floor = float(np.median(self._amplitude_history)) if self._amplitude_history else 0.0
            self._noise_floor = floor
            self._amplitude_history.append(rms)

            if len(self._amplitude_history) < WARMUP_BLOCKS or floor <= 0.0:
                reason = "warmup"
            elif rms < MIN_AMPLITUDE:
                reason = f"below min_amp ({rms:.5f} < {MIN_AMPLITUDE:.5f})"
            else:
                ratio = rms / floor
                if ratio < SPIKE_THRESHOLD_MULTIPLIER:
                    reason = f"below ratio ({ratio:.2f}x < {SPIKE_THRESHOLD_MULTIPLIER:.2f}x)"
                elif now - self._last_spike_time <= COOLDOWN_SECONDS:
                    reason = "cooldown"
                else:
                    self._last_spike_time = now
                    self._trigger_count += 1
                    self._bite_flag.set()
                    reason = "trigger"
                    self._emit(
                        f"[Audio] Splash detected! (rms {rms:.5f}, "
                        f"floor {floor:.5f}, {ratio:.1f}x, peak {peak:.5f})")

            if self._debug and now - self._last_debug_print > 0.5:
                self._last_debug_print = now
                ratio_dbg = (rms / floor) if floor > 0 else 0.0
                self._emit(
                    f"[Audio Debug] rms={rms:.5f} peak={peak:.5f} "
                    f"floor={floor:.5f} ratio={ratio_dbg:.1f}x "
                    f"min_amp={MIN_AMPLITUDE} spike_mult={SPIKE_THRESHOLD_MULTIPLIER} "
                    f"-> {reason}")

    def _audio_thread(self, device) -> None:
        """Background thread: captures audio and detects spikes."""
        blocksize = int(SAMPLE_RATE * BLOCK_DURATION)

        def audio_callback(indata, frames, time_info, status):
            # Runs on the PortAudio realtime thread: never print or touch the
            # filesystem here. Any exception escaping this function aborts the
            # stream for the rest of the session, so everything is caught and
            # reported through the message queue instead.
            if not self._running:
                return
            try:
                self._process_block(indata)
            except Exception as exc:  # noqa: BLE001 - never kill the stream
                self._callback_error = exc
                self._emit(f"[Audio] Detection error: {exc!r}")

        # Try a sequence of devices — first the "best" (Stereo Mix/loopback),
        # then fall back to default input if that fails.
        devices_to_try = [device]
        if device is not None:
            devices_to_try.append(None)  # system default input as fallback

        last_error = None
        stream_started = False
        for try_device in devices_to_try:
            try:
                if try_device is not None:
                    dev_info = sd.query_devices(try_device)
                    channels = dev_info["max_input_channels"]
                    if channels < 1:
                        channels = 2

                    host_apis = sd.query_hostapis()
                    is_wasapi = (
                        dev_info["hostapi"] is not None
                        and dev_info["hostapi"] < len(host_apis)
                        and "WASAPI" in host_apis[dev_info["hostapi"]]["name"]
                    )
                else:
                    channels = 2
                    is_wasapi = False

                extra = self._get_wasapi_extra() if is_wasapi else None
                stream_kwargs = dict(
                    device=try_device,
                    channels=channels,
                    samplerate=SAMPLE_RATE,
                    blocksize=blocksize,
                    callback=audio_callback,
                    dtype="float32",
                )
                if extra is not None:
                    stream_kwargs["extra_settings"] = extra
                self._stream = sd.InputStream(**stream_kwargs)
                self._stream.start()
                stream_started = True
                if try_device != device:
                    dev_name = sd.query_devices(try_device)["name"] if try_device is not None else "default input"
                    print(f"[Audio] Fallback: using device '{dev_name}'")
                break
            except Exception as e:
                last_error = e
                continue

        if not stream_started:
            print(f"[Audio] Error: {last_error}")
            print("[Audio] Try: Settings -> Sound -> Recording -> enable 'Stereo Mix'")
            print("[Audio] Or install VB-Audio Virtual Cable (free)")
            return

        # Main loop — keep thread alive while streaming
        try:
            while self._running:
                time.sleep(0.05)
                self._drain_messages()
                # Surface a dead stream (device removed, driver reset, resume
                # from sleep) instead of spinning forever while nothing is heard.
                if self._running and not self.healthy():
                    self._emit("[Audio] Capture stream stopped unexpectedly - "
                               "bites will no longer be detected by sound.")
                    break
        except Exception as e:
            print(f"[Audio] Runtime error: {e}")
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
            self._drain_messages()


def list_audio_devices() -> None:
    """Print all available audio devices for debugging."""
    if not HAS_SOUNDDEVICE:
        print("sounddevice not installed.")
        return

    print("=" * 60)
    print("  Available Audio Devices")
    print("=" * 60)
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        name = dev["name"]
        host = dev["hostapi"]
        default = ""
        # Check if this device matches a relevant default
        host_apis = sd.query_hostapis()
        if host < len(host_apis):
            api = host_apis[host]
            if idx == api.get("default_input_device"):
                default = " [DEFAULT INPUT]"
            elif idx == api.get("default_output_device"):
                default = " [DEFAULT OUTPUT]"
        print(f"  [{idx}] {name} (in={in_ch}, out={out_ch}){default}")
    print("=" * 60)
    print()
    print("Look for a device with 'loopback' in the name (WASAPI).")
    print("If none: right-click speaker -> Sounds -> Recording ->")
    print("  enable 'Stereo Mix', OR install VB-Audio Virtual Cable.")