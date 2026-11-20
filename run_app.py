"""Dev launcher for Deck Studio."""

import ctypes
import multiprocessing

try:  # before anything imports WinForms/WebView2
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

from app.desktop import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
