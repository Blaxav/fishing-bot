"""Mouse and keyboard simulation for WoW fishing bot.

Uses the raw Windows SendInput API via ctypes for maximum compatibility.
This is the lowest-level input method and cannot be distinguished from
real hardware input by games.
"""

import time
import ctypes
from ctypes import wintypes

# ── Windows API types and constants ───────────────────────────────────

# SendInput
user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

# Virtual key codes
VK_CODES = {str(i): 0x30 + i for i in range(10)}  # 0-9
VK_CODES.update({chr(ord("a") + i): 0x41 + i for i in range(26)})  # a-z
VK_CODES.update({
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "backspace": 0x08,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
})


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


def _send_keyboard(vk_code: int, key_up: bool = False) -> None:
    """Send a single keyboard event via SendInput."""
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP

    # Map virtual key to scan code using MapVirtualKey
    mapvk = ctypes.windll.user32.MapVirtualKeyW
    scan_code = mapvk(vk_code, 0)  # MAPVK_VK_TO_VSC = 0

    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk_code
    inp.union.ki.wScan = scan_code
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None

    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _get_vk(key: str) -> int:
    """Convert a key string to a virtual key code."""
    key_lower = key.lower()
    if key_lower in VK_CODES:
        return VK_CODES[key_lower]
    raise ValueError(f"Unknown key: {key}")


def press_fishing_key(key: str = "1") -> None:
    """Press and release a key using the Win32 SendInput API."""
    vk = _get_vk(key)
    _send_keyboard(vk, key_up=False)
    time.sleep(0.02)
    _send_keyboard(vk, key_up=True)


def move_and_click(x: int, y: int) -> None:
    """Move the mouse to (x, y) and left-click."""
    # Get screen dimensions for absolute positioning
    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
    screen_height = ctypes.windll.user32.GetSystemMetrics(1)

    # Normalize coordinates (0–65535 range for absolute mouse input)
    abs_x = int(x * 65535 / screen_width)
    abs_y = int(y * 65535 / screen_height)

    # Move
    inp_move = INPUT()
    inp_move.type = INPUT_MOUSE
    inp_move.union.mi.dx = abs_x
    inp_move.union.mi.dy = abs_y
    inp_move.union.mi.mouseData = 0
    inp_move.union.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE
    inp_move.union.mi.time = 0
    inp_move.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_move), ctypes.sizeof(inp_move))

    time.sleep(0.05)

    # Left button down
    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.union.mi.dx = 0
    inp_down.union.mi.dy = 0
    inp_down.union.mi.mouseData = 0
    inp_down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    inp_down.union.mi.time = 0
    inp_down.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(inp_down))

    time.sleep(0.02)

    # Left button up
    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.union.mi.dx = 0
    inp_up.union.mi.dy = 0
    inp_up.union.mi.mouseData = 0
    inp_up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP
    inp_up.union.mi.time = 0
    inp_up.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))


def right_click(x: int, y: int) -> None:
    """Right-click at (x, y) using SendInput."""
    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
    screen_height = ctypes.windll.user32.GetSystemMetrics(1)

    abs_x = int(x * 65535 / screen_width)
    abs_y = int(y * 65535 / screen_height)

    inp_move = INPUT()
    inp_move.type = INPUT_MOUSE
    inp_move.union.mi.dx = abs_x
    inp_move.union.mi.dy = abs_y
    inp_move.union.mi.mouseData = 0
    inp_move.union.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE
    inp_move.union.mi.time = 0
    inp_move.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_move), ctypes.sizeof(inp_move))

    time.sleep(0.05)

    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.union.mi.dx = 0
    inp_down.union.mi.dy = 0
    inp_down.union.mi.mouseData = 0
    inp_down.union.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN
    inp_down.union.mi.time = 0
    inp_down.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(inp_down))

    time.sleep(0.02)

    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.union.mi.dx = 0
    inp_up.union.mi.dy = 0
    inp_up.union.mi.mouseData = 0
    inp_up.union.mi.dwFlags = MOUSEEVENTF_RIGHTUP
    inp_up.union.mi.time = 0
    inp_up.union.mi.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))


def small_sleep(seconds: float = 0.5) -> None:
    """Short sleep utility."""
    time.sleep(seconds)