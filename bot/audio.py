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
SPIKE_THRESHOLD_MULTIPLIER = _env_float("WOWBOT_SPIKE_MULT", 3.0)

# Absolute minimum amplitude to consider (filters out background hum).
MIN_AMPLITUDE = _env_float("WOWBOT_MIN_AMP", 0.03)

# How many recent amplitude samples to average for the noise floor.
NOISE_FLOOR_WINDOW = 30

# Audio sample rate (Hz).
SAMPLE_RATE = 22050

# How often (seconds) the audio callback delivers a chunk.
BLOCK_DURATION = 0.05

# After a spike is detected, ignore further spikes for this many seconds.
COOLDOWN_SECONDS = _env_float("WOWBOT_COOLDOWN", 0.8)

# Print every RMS value to stdout (for debugging). Override: WOWBOT_AUDIO_DEBUG=1
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

    def __init__(self):
        if not HAS_SOUNDDEVICE:
            raise RuntimeError(
                "sounddevice not installed. Run: uv add sounddevice"
            )

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream: Optional[sd.InputStream] = None

        # Rolling buffer of recent amplitude values for noise floor
        self._amplitude_history = collections.deque(maxlen=NOISE_FLOOR_WINDOW)

        # Flags
        self._bite_flag = threading.Event()
        self._last_spike_time = 0.0
        self._lock = threading.Lock()

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
        print("[Audio] Stopped.")

    def bite_detected(self) -> bool:
        """Check if a bite splash was detected.

        Returns True once per detection, then resets the flag.
        """
        return self._bite_flag.is_set()

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
        print("[Audio] Enable 'Stereo Mix' in Windows Sound settings → Recording tab.")
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

    def _audio_thread(self, device) -> None:
        """Background thread: captures audio and detects spikes."""
        blocksize = int(SAMPLE_RATE * BLOCK_DURATION)

        debug_print_counter = 0
        last_debug_print = 0.0

        def audio_callback(indata, frames, time_info, status):
            nonlocal debug_print_counter, last_debug_print

            if not self._running:
                return

            if status and "input underflow" not in str(status) and "input overflow" not in str(status):
                return

            # Compute RMS amplitude of this chunk
            data = np.frombuffer(indata, dtype=np.float32)
            if data.size == 0:
                return
            if data.size >= 2:
                data = data.reshape(-1, 2).mean(axis=1)

            rms = float(np.sqrt(np.mean(data**2)))

            with self._lock:
                self._amplitude_history.append(rms)

                # Debug: print RMS every ~0.5s
                if AUDIO_DEBUG:
                    now_debug = time.time()
                    if now_debug - last_debug_print > 0.5:
                        last_debug_print = now_debug
                        avg = np.mean(list(self._amplitude_history)) if self._amplitude_history else 0
                        print(f"[Audio Debug] RMS={rms:.6f} | floor_avg={avg:.6f} | spike_mult={SPIKE_THRESHOLD_MULTIPLIER} | min_amp={MIN_AMPLITUDE}")
                        if rms > MIN_AMPLITUDE and avg > 0 and rms > avg * SPIKE_THRESHOLD_MULTIPLIER:
                            print(f"[Audio Debug]   ^^^ WOULD TRIGGER SPIKE (but debounce applies)")

                # Need enough samples to establish noise floor
                if len(self._amplitude_history) < NOISE_FLOOR_WINDOW // 3:
                    return

                avg_noise = np.mean(list(self._amplitude_history))
                if avg_noise < 0.001:
                    avg_noise = 0.001  # avoid division by zero

                # Check for spike
                if rms > MIN_AMPLITUDE and rms > avg_noise * SPIKE_THRESHOLD_MULTIPLIER:
                    now = time.time()
                    if now - self._last_spike_time > COOLDOWN_SECONDS:
                        self._last_spike_time = now
                        self._bite_flag.set()
                        print(f"[Audio] ⚡ Splash detected! (amplitude: {rms:.4f}, floor: {avg_noise:.4f})")

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
            print("[Audio] Try: Settings → Sound → Recording → enable 'Stereo Mix'")
            print("[Audio] Or install VB-Audio Virtual Cable (free)")
            return

        # Main loop — keep thread alive while streaming
        try:
            while self._running:
                time.sleep(0.1)
        except Exception as e:
            print(f"[Audio] Runtime error: {e}")
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass


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
    print("If none: right-click speaker → Sounds → Recording →")
    print("  enable 'Stereo Mix', OR install VB-Audio Virtual Cable.")