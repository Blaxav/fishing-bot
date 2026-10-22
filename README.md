# WoW Bot — Classic Fishing (Audio Detection)

A Python bot that automates fishing in WoW Classic. Uses **sound-based splash detection** for reliable fish bite detection — no more false positives from water ripples.

## How It Works

1. Presses key `1` to cast the fishing line
2. Finds the bobber on screen with OpenCV template matching
3. Listens for the **splash sound** via WASAPI audio loopback
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
# 1. Verify audio device is detected (should find Stereo Mix)
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

The bot captures system audio via WASAPI loopback. Your machine has **"Mixage stéréo" (Stereo Mix)** available (device 30) — the bot auto-detects it.

If audio doesn't work:
- Right-click speaker icon → **Sounds** → **Recording** tab
- Right-click **"Mixage stéréo"** → **Enable** (if disabled)
- Set it as **Default Device**
- Run `uv run python main.py --list-audio` to verify

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
| `WOWBOT_SPIKE_MULT` | `2.0` | How many times louder than the recent median level the splash must be. Scale-free, so it works at any volume. Lower = more sensitive. |
| `WOWBOT_MIN_AMP` | `0.0015` | Absolute floor, only meant to reject near-silence. **Must stay well below the level a splash reaches on your machine.** |
| `WOWBOT_COOLDOWN` | `0.8` | Ignore spikes for this long after each detection (seconds). |
| `WOWBOT_AUDIO_DEBUG` | `0` | Print the measured level and the reason each block was rejected. |

### Calibrating

```bash
uv run python main.py --audio-debug
```

Cast in-game and compare the `[Level] rms=` value while waiting with the value at
the moment of the splash. Typical numbers measured on this machine
(Stereo Mix loopback, once attenuation by the Windows mixer is included):

| Situation | `rms` |
|---|---|
| Idle / digital silence | ~0.00002 |
| Gameplay ambience | ~0.0008 |
| Splash | ~0.003 - 0.008 |

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
- **Audio fails**: Run `uv run python main.py --list-audio` and check Stereo Mix is enabled
- **Bobber not found**: Lower `MATCH_THRESHOLD` in `bot/screen.py`, or replace `bobber.png`
- **Splash not detected**: Game must be audible (not muted). Run `uv run python main.py --audio-debug` and check the `rms` value at a splash is above `WOWBOT_MIN_AMP`; lower `WOWBOT_MIN_AMP` / `WOWBOT_SPIKE_MULT` if bites are missed, raise them if the bot clicks at random times
- **Right-click doesn't catch**: The bot right-clicks the last place it saw the bobber, immediately after the splash. If the log shows `cursor landed at (x, y), not (x, y)`, something on the system is moving the pointer. If clicks land next to the bobber, set `WOWBOT_CLICK_OFFSET_X` / `WOWBOT_CLICK_OFFSET_Y` (e.g. `6` / `7`) to nudge the click onto the sprite. `post_loot_delay` in `FisherBot` controls the wait after a catch.