"""Screenshot the real Deck Studio window, even when it is behind another app.

PowerShell/ImageGrab only sees what is on top of the desktop.  PrintWindow asks
the window to paint itself into a bitmap, which is what we want for product
screenshots while the agent works in another window.
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image


try:  # measure and capture in device pixels, not virtualized ones
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()


def find(title: str = "Deck Studio") -> int:
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        raise SystemExit(f"window not found: {title}")
    return found[0]


def capture(hwnd: int, out: Path, flags: int = 2) -> Path:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hwnd_dc)
    dst = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, width, height)
    dst.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, dst.GetSafeHdc(), flags)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    win32gui.DeleteObject(bmp.GetHandle())
    dst.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return out


def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "Deck Studio"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out/window.png")
    hwnd = find(title)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.6)
    print(capture(hwnd, out))


if __name__ == "__main__":
    main()
