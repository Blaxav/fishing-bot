"""Audio-based fish bite detection using a loopback recording device.

Captures whatever the speakers play (Stereo Mix, "Mixage stéréo", What U Hear
or a virtual cable) and detects amplitude spikes corresponding to the fishing
splash sound in WoW Classic.
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


# Recording devices that natively capture speaker output. Names are localised
# ("Mixage stéréo" is the French "Stereo Mix", "What U Hear" the SoundBlaster
# one). Virtual cables such as VB-Audio show up as "CABLE Output" and are
# equally valid: WoW is routed to "CABLE Input", we listen to "CABLE Output".
STEREO_MIX_NAMES = (
    "stereo mix",
    "mixage st",
    "wave out mix",
    "what u hear",
    "loopback",
    "cable output",
    "vb-audio",
    "voicemeeter out",
)


def _resolve_device(value: str, devices) -> Optional[int]:
    """Resolve a WOWBOT_AUDIO_DEVICE value (index or name substring) to an index."""
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        index = int(value)
        if index < len(devices):
            return index
        print(f"[Audio] WOWBOT_AUDIO_DEVICE={value}: only {len(devices)} device(s) available.")
        return None
    needle = value.lower()
    for index, device in enumerate(devices):
        if needle in device["name"].lower():
            return index
    print(f"[Audio] WOWBOT_AUDIO_DEVICE={value!r}: no device name contains that text.")
    return None


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
        """Start capturing system audio."""
        if self._running:
            return

        self._running = True
        self._bite_flag.clear()
        self._callback_error = None
        # Drop the previous (closed) stream so the wait loop below cannot mistake
        # it for the new one when start() is called again after stop().
        self._stream = None

        # Ordered list of devices to try (best first, None = default input).
        devices = self._capture_candidates()

        self._thread = threading.Thread(
            target=self._audio_thread,
            args=(devices,),
            daemon=True,
        )
        self._thread.start()

        # Wait for the stream to open (sample-rate fallbacks can take a moment).
        for _ in range(40):
            if self._stream is not None or not self._thread.is_alive():
                break
            time.sleep(0.05)
        self._drain_messages()

        if self.healthy():
            print("[Audio] Monitoring system audio for splash sounds...")
        else:
            print("[Audio] WARNING: no audio capture stream - "
                  "splash detection is disabled.")

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
    def _capture_candidates() -> list:
        """Ordered list of devices to try for system-audio capture.

        ``None`` means "whatever Windows considers the default input". The
        PortAudio build bundled with sounddevice has no WASAPI loopback, so an
        output device cannot be opened for capture: only *recording* devices
        (Stereo Mix, "What U Hear", a virtual cable, ...) can hear the game.

        Priority:
        1. WOWBOT_AUDIO_DEVICE (index or name substring), when set
        2. Stereo Mix / Mixage stéréo / What U Hear / virtual cable inputs
        3. Any other WASAPI input
        4. System default input (usually a microphone - last resort)
        """
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()

        override = os.environ.get("WOWBOT_AUDIO_DEVICE", "").strip()
        if override:
            index = _resolve_device(override, devices)
            if index is not None:
                return [index]
            print("[Audio] Ignoring WOWBOT_AUDIO_DEVICE and auto-detecting.")

        candidates: list = []

        # 1. Devices that natively record the speaker output.
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] < 1:
                continue
            name = dev["name"].lower()
            if any(keyword in name for keyword in STEREO_MIX_NAMES):
                candidates.append(idx)

        # 2. Other WASAPI inputs (e.g. a virtual cable selected by the user).
        wasapi_idx = None
        for i, api in enumerate(host_apis):
            if "WASAPI" in api["name"]:
                wasapi_idx = i
                break
        if wasapi_idx is not None:
            for idx, dev in enumerate(devices):
                if (dev["hostapi"] == wasapi_idx
                        and dev["max_input_channels"] > 0
                        and idx not in candidates):
                    candidates.append(idx)

        # 3. Default input, last.
        candidates.append(None)
        return candidates

    @staticmethod
    def _find_loopback_device():
        """Best capture device index (kept for diagnostics/backwards compat)."""
        candidates = AudioMonitor._capture_candidates()
        return candidates[0] if candidates else None

    def _open_stream(self, device, samplerate: int):
        """Open and start an input stream, raising on failure."""
        dev_info = (sd.query_devices(device) if device is not None
                    else sd.query_devices(kind="input"))
        channels = int(dev_info["max_input_channels"])
        if channels < 1:
            raise RuntimeError("device has no input channels")

        host_apis = sd.query_hostapis()
        hostapi = dev_info["hostapi"]
        is_wasapi = (
            hostapi is not None
            and 0 <= hostapi < len(host_apis)
            and "WASAPI" in host_apis[hostapi]["name"]
        )

        stream_kwargs = dict(
            device=device,
            channels=channels,
            samplerate=samplerate,
            blocksize=max(int(samplerate * BLOCK_DURATION), 1),
            callback=self._callback,
            dtype="float32",
        )
        # WASAPI shared mode rejects rates the device does not support unless
        # auto_convert is enabled (e.g. 22050 Hz on a 48000 Hz Stereo Mix).
        if is_wasapi:
            stream_kwargs["extra_settings"] = sd.WasapiSettings(auto_convert=True)

        stream = sd.InputStream(**stream_kwargs)
        stream.start()
        return stream

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

    def _callback(self, indata, frames, time_info, status) -> None:
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

    def _audio_thread(self, devices) -> None:
        """Initialise COM on this thread, then run the capture loop."""
        # PortAudio's WASAPI backend needs COM initialised on the thread that
        # opens the stream. sounddevice only initialises COM for the thread
        # that first touched PortAudio (usually the main thread), so opening a
        # WASAPI device from here fails with "Unanticipated host error"
        # otherwise. MME/DirectSound do not care, which is why the bug only
        # shows up on machines whose only capture device is WASAPI.
        com_initialised = False
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # STA
                com_initialised = True
            except Exception:  # noqa: BLE001 - best effort
                pass

        try:
            self._run_capture(devices)
        finally:
            if com_initialised:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:  # noqa: BLE001 - best effort
                    pass

    def _run_capture(self, devices) -> None:
        """Open the best capture device and detect spikes."""
        last_error = None
        failures = []
        stream_started = False

        for device in devices:
            try:
                info = (sd.query_devices(device) if device is not None
                        else sd.query_devices(kind="input"))
            except Exception as exc:  # noqa: BLE001 - device vanished
                last_error = exc
                failures.append(f"device {device!r}: {exc}")
                continue

            label = (f"[{device}] {info['name']}" if device is not None
                     else f"default input '{info['name']}'")

            if info["max_input_channels"] < 1:
                last_error = RuntimeError("not an input device")
                failures.append(f"{label}: no input channels")
                continue

            if device is None:
                print("[Audio] WARNING: falling back to the system default input.")
                print("[Audio] WARNING: that is usually a microphone and will NOT "
                      "hear the game - enable 'Stereo Mix' in Windows sound settings.")

            rates = [SAMPLE_RATE]
            default_rate = int(info.get("default_samplerate") or SAMPLE_RATE)
            if default_rate != SAMPLE_RATE:
                rates.append(default_rate)

            for rate in rates:
                try:
                    stream = self._open_stream(device, rate)
                except Exception as exc:  # noqa: BLE001 - try next rate/device
                    last_error = exc
                    continue
                # Publish the stream only after reporting it, so start()'s
                # "Monitoring..." line always comes after this one.
                print(f"[Audio] Capturing from {label} "
                      f"({info['max_input_channels']} ch @ {rate} Hz).")
                self._stream = stream
                stream_started = True
                break

            if stream_started:
                break
            failures.append(f"{label}: {last_error}")

        if not stream_started:
            print(f"[Audio] Could not start audio capture ({last_error!r}).")
            for failure in failures:
                print(f"[Audio]   {failure}")
            print("[Audio] Enable a recording device that hears the game:")
            print("[Audio]   Windows Settings -> System -> Sound -> More sound settings")
            print("[Audio]   -> Recording -> right-click -> Show Disabled Devices")
            print("[Audio]   -> enable 'Stereo Mix' (or install VB-Audio Virtual Cable)")
            print("[Audio] Then check: uv run python main.py --list-audio")
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

    devices = sd.query_devices()
    host_apis = sd.query_hostapis()

    print("=" * 90)
    print("  Available Audio Devices")
    print("=" * 90)
    for idx, dev in enumerate(devices):
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        host_idx = dev["hostapi"]
        host = host_apis[host_idx]["name"] if host_idx < len(host_apis) else "?"
        api = host_apis[host_idx] if host_idx < len(host_apis) else {}
        default = ""
        if idx == api.get("default_input_device"):
            default = " [DEFAULT INPUT]"
        elif idx == api.get("default_output_device"):
            default = " [DEFAULT OUTPUT]"
        print(f"  [{idx:2}] {host:<20} in={in_ch} out={out_ch} "
              f"{dev['name']}{default}")
    print("=" * 90)
    print("The bot can only capture from a device with in>0 that hears the game:")
    print("  Stereo Mix / Mixage stéréo / What U Hear, or a virtual cable.")
    print("The PortAudio build used here has NO WASAPI loopback, so output-only")
    print("devices (in=0) cannot be used for capture.")
    print()

    candidates = AudioMonitor._capture_candidates()
    print("Bot would try, in order:")
    for position, device in enumerate(candidates, start=1):
        if device is None:
            info = sd.query_devices(kind="input")
            print(f"  {position}. default input [{info['index']}] {info['name']} "
                  f"(last resort - usually a microphone)")
        else:
            info = devices[device]
            print(f"  {position}. [{device}] {info['name']}")
    print()
    print("Force a device with its index or part of its name in .env:")
    print("  WOWBOT_AUDIO_DEVICE=23")
    print("Then run: uv run python main.py --audio-debug")