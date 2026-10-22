# WoW Bot — Classic Fishing (Audio Detection)

A Python bot that automates fishing in WoW Classic. Uses **sound-based splash detection** for reliable fish bite detection — no more false positives from water ripples.

> ⚠ **Personal use only.** This is a private hobby project, published as-is for my own
> reference. It is not intended for distribution, is not supported for anyone else, and
> comes with no guarantees of any kind. Automating gameplay violates the World of
> Warcraft Terms of Service and can get your account suspended or banned — use at your
> own risk.

## How It Works

1. Presses key `1` to cast the fishing line
2. Finds the bobber on screen with OpenCV template matching
3. Listens for the **splash sound** through a recording device (Stereo Mix / virtual cable)
4. On splash: **right-clicks** the bobber to catch the fish
5. Repeats until stopped

## Requirements

- **Python 3.12+**
- **WoW Classic** in **Windowed** or **Borderless Windowed** mode
- **System audio enabled** — the bot needs to hear the game sounds

## Installation

```bash
uv sync
```

## Quick Start

```bash
# 1. Verify a capture device that hears the game exists (Stereo Mix)
uv run python main.py --list-audio

# 2. (Optional) Calibrate screen region
uv run python main.py --calibrate

# 3. Run as Administrator!
#    Right-click terminal → "Run as administrator", then:
uv run python main.py

# Press Ctrl+Shift+F to start/stop fishing
# Press Ctrl+C to exit
```

## Audio Setup

The bot captures from a **recording device that hears the speakers** — usually
**Stereo Mix** ("Mixage stéréo" in French Windows). It does *not* use WASAPI
loopback: the PortAudio build shipped with `sounddevice` does not expose it, so
an output-only device (`in=0`) can never be captured from.

This is the usual reason a splash is not detected on a new computer:

1. Right-click the speaker icon → **Sounds** → **Recording** tab.
2. Right-click empty space → **Show Disabled Devices** (Stereo Mix is often
   disabled by default on new machines).
3. Right-click **Stereo Mix / Mixage stéréo** → **Enable**.
4. Make sure the game plays through the **same sound card** as Stereo Mix.
   Stereo Mix only records its own card: if WoW outputs to HDMI (monitor),
   USB/Bluetooth headphones or a Steam streaming device, Realtek Stereo Mix
   records silence. Put Windows' default output and WoW's output on the same
   device.
5. Check what the bot sees:

   ```bash
   uv run python main.py --list-audio
   ```

   A usable device has `in>0` and a name like Stereo Mix / Mixage stéréo /
   `CABLE Output`. The command also prints the devices the bot will try.

6. Force a device if auto-detection picks the microphone:

   ```ini
   WOWBOT_AUDIO_DEVICE=23      # index from --list-audio
   # or
   WOWBOT_AUDIO_DEVICE=cable   # part of the device name, case-insensitive
   ```

If no Stereo Mix exists (some laptop drivers hide it), install
[VB-Audio Virtual Cable](https://vb-audio.com/Cable/), set WoW's output to
`CABLE Input`, and use `WOWBOT_AUDIO_DEVICE=CABLE Output`.

### Audio debugging checklist

1. `uv run python main.py --list-audio` — is there a device with `in>0` that
   hears the game? Output-only devices (`in=0`) cannot be used.
2. `uv run python main.py --audio-debug` — read the first
   `[Audio] Capturing from ...` line, then watch `rms` while casting.
3. `rms` never moves → **capture problem** (Stereo Mix disabled, game on another
   sound card, game muted, "Sound Effects" volume at 0, or no "play sound in
   background" when the window loses focus).
4. `rms` moves at the splash but `triggers=0` → **threshold problem**: lower
   `WOWBOT_MIN_AMP` / `WOWBOT_SPIKE_MULT`.
5. `uv run python test_audio_detection.py` — if all tests pass, the detector
   logic is fine and the problem is capture or thresholds.

## Controls

| Key | Action |
|---|---|
| **Ctrl+Shift+F** | Start / Stop the fishing loop |
| **Ctrl+C** | Exit the bot |

> ⚠ **Run the terminal as Administrator** — games block simulated inputs from non-elevated processes.

## Bobber Template

`bobber.png` must be a screenshot crop of the fishing bobber from your game. Replace it if detection fails.

## Optional: Limit the Scan Region

```bash
uv run python main.py --calibrate
```

Click the top-left and bottom-right corners of your game window, then copy-paste the printed environment variables.

## Tuning Audio Detection

Set these in `.env` (all optional):

| Parameter | Default | Description |
|---|---|---|
| `WOWBOT_AUDIO_DEVICE` | auto | Capture device index or name substring (see `--list-audio`). Use when auto-detection picks the microphone. |
| `WOWBOT_SPIKE_MULT` | `2.0` | How many times louder than the recent median level the splash must be. Scale-free, so it works at any volume. Lower = more sensitive. |
| `WOWBOT_MIN_AMP` | `0.0015` | Absolute floor, only meant to reject near-silence. **Must stay well below the level a splash reaches on your machine.** |
| `WOWBOT_COOLDOWN` | `0.8` | Ignore spikes for this long after each detection (seconds). |
| `WOWBOT_AUDIO_DEBUG` | `0` | Print the measured level and the reason each block was rejected. |

### Calibrating

```bash
uv run python main.py --list-audio   # which device will be captured?
uv run python main.py --audio-debug  # live levels
```

Cast in-game and compare the `[Level] rms=` value while waiting with the value at
the moment of the splash. Typical numbers measured on this setup (Stereo Mix,
once attenuation by the Windows mixer is included):

| Situation | `rms` |
|---|---|
| Idle / digital silence | ~0.00002 |
| Gameplay ambience | ~0.0008 |
| Splash | ~0.003 - 0.008 |

What the debug output means:

| Symptom | Meaning / fix |
|---|---|
| `WARNING: no audio capture stream` | No device could be opened. Check `--list-audio`, enable Stereo Mix, or set `WOWBOT_AUDIO_DEVICE`. |
| `Capturing from ... default input` | It is listening to the **microphone**, not the game. Enable Stereo Mix or set `WOWBOT_AUDIO_DEVICE`. |
| `rms` stays at `0.0000x` while the game is audible | The device does not hear the game: wrong sound card (HDMI / USB / Bluetooth), game muted, or Sound Effects volume at 0. |
| `rms` moves at the splash but `triggers=0` | Captured but rejected: lower `WOWBOT_MIN_AMP`, then `WOWBOT_SPIKE_MULT` (e.g. `1.5`). |
| `triggers` increases while nothing bites | Too sensitive: raise `WOWBOT_MIN_AMP` / `WOWBOT_SPIKE_MULT`. |

Set `WOWBOT_MIN_AMP` to roughly a quarter of the loudest splash you see. Because
`WOWBOT_SPIKE_MULT` is a ratio against the *median* of the last 1.5 s, it adapts
to loud ambience (music, waterfalls) without needing retuning.

Verify the detector logic without any hardware:

```bash
uv run python test_audio_detection.py
```

## Tuning Bobber Detection

In `bot/screen.py`:

| Parameter | Default | Description |
|---|---|---|
| `MATCH_THRESHOLD` | `0.5` | Template match sensitivity (lower = easier to find) |
| `CLICK_OFFSET_X` / `CLICK_OFFSET_Y` | `0` | Nudge the click point when `bobber.png` is not centred on the sprite. Override with `WOWBOT_CLICK_OFFSET_X` / `WOWBOT_CLICK_OFFSET_Y` in `.env`. |

## Troubleshooting

- **Bot doesn't press keys**: Run terminal as **Administrator**
- **Audio fails**: Run `uv run python main.py --list-audio`. Only devices with `in>0` can be captured; output-only devices cannot (this build has no WASAPI loopback). Enable **Stereo Mix**, or install VB-Audio Virtual Cable, then check the startup line `[Audio] Capturing from ...`
- **Capturing the microphone instead of the game**: the log says `falling back to the system default input`, or `--list-audio` shows the mic first. Set `WOWBOT_AUDIO_DEVICE` to the Stereo Mix index
- **Bobber not found**: Lower `MATCH_THRESHOLD` in `bot/screen.py`, or replace `bobber.png`
- **Splash not detected**: first make sure the game is audible on the device being captured (same output card, game not muted, Sound Effects volume > 0), then run `uv run python main.py --audio-debug`: `rms` must move at the splash. If it does but `triggers=0`, lower `WOWBOT_MIN_AMP` / `WOWBOT_SPIKE_MULT`; if `rms` never moves, it is a capture problem, not a threshold problem
- **Right-click doesn't catch**: The bot right-clicks the last place it saw the bobber, immediately after the splash. If the log shows `cursor landed at (x, y), not (x, y)`, something on the system is moving the pointer. If clicks land next to the bobber, set `WOWBOT_CLICK_OFFSET_X` / `WOWBOT_CLICK_OFFSET_Y` (e.g. `6` / `7`) to nudge the click onto the sprite. `post_loot_delay` in `FisherBot` controls the wait after a catch.