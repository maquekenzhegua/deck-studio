# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for Deck Studio.

One folder, not one file: pythonnet + WebView2 + matplotlib want their DLLs
and data files next to the executable, and a one-file build would unpack
~200 MB to a temp directory on every launch.  The folder is zipped for
distribution; the exe inside is what the shortcut points at.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = [
    ("app/static", "app/static"),
    ("samples/data", "samples/data"),
    ("docs", "docs"),
    ("assets", "assets"),
]
datas += collect_data_files("webview")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "webview.platforms.edgechromium",
    "clr_loader",
    "pythonnet",
    "PIL._tkinter_finder",
    # imported lazily inside endpoints, so static analysis cannot see them
    "tools.preview",
    "tools.qa",
]

a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook", "nbconvert", "nbformat",
        "bokeh", "panel", "holoviews", "hvplot", "datashader",
        "h5py", "scipy", "sklearn", "numba", "llvmlite",
        "torch", "tensorflow", "cv2", "plotly", "dash",
        "pytest", "sphinx", "docutils", "sqlalchemy",
        "matplotlib.backends.backend_qt5agg",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_webagg",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Deck Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/deck.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Deck Studio",
)
