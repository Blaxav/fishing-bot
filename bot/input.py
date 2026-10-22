"""Mouse and keyboard simulation for WoW fishing bot.

Uses the raw Windows SendInput API via ctypes for maximum compatibility.
This is the lowest-level input method and cannot be distinguished from
real hardware input by games.
"""

import time
import ctypes
from ctypes import wintypes
from typing import Tuple

# ── Windows API types and constants ───────────────────────────────────

# SendInput (use_last_error so failed calls can be reported properly)
user32 = ctypes.WinDLL("user32", use_last_error=True)

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


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


def _send_keyboard(vk_code: int, key_up: bool = False) -> None:
    """Send a single keyboard event via SendInput."""
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP

    # Map virtual key to scan code using MapVirtualKey
    scan_code = user32.MapVirtualKeyW(vk_code, 0)  # MAPVK_VK_TO_VSC = 0

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


# ── Mouse helpers ─────────────────────────────────────────────────────

def get_cursor_pos() -> Tuple[int, int]:
    """Return the current OS cursor position in screen pixels."""
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


def _screen_size() -> Tuple[int, int]:
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def _send_mouse(dx: int, dy: int, flags: int) -> bool:
    """Send one mouse event via SendInput. Returns True when it was accepted."""
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.mouseData = 0
    inp.union.mi.dwFlags = flags
    inp.union.mi.time = 0
    inp.union.mi.dwExtraInfo = None
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
        print(f"[Input] SendInput failed (WinError {ctypes.get_last_error()})")
        return False
    return True


def move_to(x: int, y: int) -> Tuple[int, int]:
    """Move the cursor to screen pixel (x, y) and make sure it really got there.

    The move goes through SendInput so the game receives a genuine mouse move,
    then the result is verified with GetCursorPos. If the cursor ended up
    somewhere else (DPI mismatch, pointer acceleration, another program or the
    game itself warping the cursor, ...), SetCursorPos snaps it to the exact
    pixel before any button event is sent.

    MOUSEEVENTF_ABSOLUTE maps 0..65535 onto 0..(screen_size - 1), hence the
    -1 in the normalisation.
    """
    screen_width, screen_height = _screen_size()
    abs_x = int(round(x * 65535 / max(1, screen_width - 1)))
    abs_y = int(round(y * 65535 / max(1, screen_height - 1)))

    _send_mouse(abs_x, abs_y, MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE)

    actual = get_cursor_pos()
    if abs(actual[0] - x) > 1 or abs(actual[1] - y) > 1:
        user32.SetCursorPos(int(x), int(y))
        actual = get_cursor_pos()

    return actual


def click(x: int, y: int, button: str = "left") -> Tuple[int, int]:
    """Move the cursor to (x, y) and click there.

    Returns the cursor position the click was issued from, so callers can log
    or verify where the click actually landed.
    """
    actual = move_to(x, y)

    if button == "right":
        down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP

    # Give the game one frame to register the new cursor position
    time.sleep(0.02)
    _send_mouse(0, 0, down_flag)
    time.sleep(0.02)
    _send_mouse(0, 0, up_flag)

    return actual


def move_and_click(x: int, y: int) -> Tuple[int, int]:
    """Move the mouse to (x, y) and left-click."""
    return click(x, y, "left")


def right_click(x: int, y: int) -> Tuple[int, int]:
    """Move the mouse to (x, y) and right-click."""
    return click(x, y, "right")


def small_sleep(seconds: float = 0.5) -> None:
    """Short sleep utility."""
    time.sleep(seconds)