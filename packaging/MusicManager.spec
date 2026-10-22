# PyInstaller: construiește aplicația ca folder (pornește repede), apoi
# Inno Setup face din el un installer obișnuit de Windows.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = collect_data_files("imageio_ffmpeg") + collect_data_files("certifi")
binaries = collect_dynamic_libs("imageio_ffmpeg")
datas.append((os.path.join(root, "packaging", "icon.png"), "."))
ss = os.path.join(root, "sockseek")
if os.path.isdir(ss):
    datas.append((ss, "sockseek"))

a = Analysis(
    [os.path.join(root, "run.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=["mutagen", "imageio_ffmpeg", "certifi"] + collect_submodules("yt_dlp.extractor.youtube"),
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(root, "packaging", "icon.icns" if sys.platform == "darwin" else "icon.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MusicManager",
    console=False,
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MusicManager")

if sys.platform == "darwin":
    import re
    ver = re.search(r'__version__ = "([^"]+)"', open(os.path.join(root, "music_manager", "__init__.py")).read()).group(1)
    app = BUNDLE(
        coll,
        name="Music Manager.app",
        icon=icon if os.path.exists(icon) else None,
        bundle_identifier="ro.grgmocanu.musicmanager",
        version=ver,
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleShortVersionString": ver,
            "NSAppleEventsUsageDescription": "Music Manager deschide Finder ca să-ți arate fișierele.",
        },
    )
