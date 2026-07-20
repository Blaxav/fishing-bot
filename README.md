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

All constants are at the top of `bot/audio.py`:

| Parameter | Default | Description |
|---|---|---|
| `SPIKE_THRESHOLD_MULTIPLIER` | `3.0` | How many times louder than ambient the splash must be |
| `MIN_AMPLITUDE` | `0.03` | Minimum volume to consider |
| `NOISE_FLOOR_WINDOW` | `30` | Samples for ambient noise averaging |
| `COOLDOWN_SECONDS` | `0.8` | Ignore spikes for this long after each detection |

## Tuning Bobber Detection

In `bot/screen.py`:

| Parameter | Default | Description |
|---|---|---|
| `MATCH_THRESHOLD` | `0.5` | Template match sensitivity (lower = easier to find) |

## Troubleshooting

- **Bot doesn't press keys**: Run terminal as **Administrator**
- **Audio fails**: Run `uv run python main.py --list-audio` and check Stereo Mix is enabled
- **Bobber not found**: Lower `MATCH_THRESHOLD` in `bot/screen.py`, or replace `bobber.png`
- **Splash not detected**: Game must be audible (not muted). Try increasing `SPIKE_THRESHOLD_MULTIPLIER` if too sensitive, or lowering it if missing splashes
- **Right-click doesn't catch**: Make sure the bobber is still on screen when the click happens. Try adjusting `post_loot_delay` in `FisherBot` constructor