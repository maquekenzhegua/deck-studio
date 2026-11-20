"""Desktop shell: a real window, not a terminal.

The web UI is served by a loopback-only uvicorn instance and rendered by the
Edge WebView2 runtime that ships with Windows.  Nothing leaves the machine.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    """DECK_PORT pins the port: headless verification needs to know where to
    connect, and a fixed port makes logs reproducible."""
    pin = os.environ.get("DECK_PORT", "").strip()
    if pin.isdigit():
        return int(pin)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _dpi_aware() -> float:
    """Use physical pixels, then tell the caller the display scale.

    On a 200% laptop panel a DPI-unaware process asks for 1232 px and gets a
    1232 px window whose WebView2 viewport is only 616 CSS px wide — every
    layout overflows.  Declaring per-monitor awareness keeps window geometry
    and the CSS viewport in the same coordinate system.
    """
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:  # noqa: BLE001
        return 1.0


def _work_area() -> tuple[int, int]:
    """Work area in physical pixels, so the window never opens off-screen."""
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    if not ok:
        return 1280, 800
    return rect.right - rect.left, rect.bottom - rect.top


def _wait(url: str, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=0.6) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.12)
    return False


def main() -> None:
    import uvicorn
    import webview

    from app.server import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="deck-uvicorn", daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/?v={int(time.time())}"
    if not _wait(url + "health"):
        raise SystemExit("本地服务没有启动，请检查端口或依赖。")

    if os.environ.get("DECK_HEADLESS") == "1":
        # Server-only mode: used to smoke-test a packaged build without
        # putting a window on someone's screen.
        print(f"DECK_READY http://127.0.0.1:{port}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return
        finally:
            server.should_exit = True
            thread.join(timeout=3)

    scale = _dpi_aware()
    area_w, area_h = _work_area()
    # With per-monitor awareness pywebview passes pixels straight through, and
    # WebView2 maps one CSS pixel to `scale` device pixels.  Size the window in
    # device pixels so the UI still gets ~1250 CSS px on a 200% panel.
    width = max(int(1040 * scale), min(int(1230 * scale), area_w - 110))
    height = max(int(680 * scale), min(int(740 * scale), area_h - 130))
    webview.create_window(
        "Deck Studio",
        url,
        width=width,
        height=height,
        x=max(0, (area_w - width) // 2),
        y=max(0, (area_h - height) // 2),
        min_size=(int(980 * scale), int(640 * scale)),
        background_color="#080A0D",
        text_select=True,
    )
    try:
        webview.start(gui="edgechromium", debug=os.environ.get("DECK_DEBUG") == "1")
    finally:
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
