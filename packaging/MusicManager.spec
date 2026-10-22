# PyInstaller: construiește aplicația ca folder (pornește repede), apoi
# Inno Setup face din el un installer obișnuit de Windows.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

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
    hiddenimports=["mutagen", "imageio_ffmpeg", "certifi"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MusicManager",
    console=False,
    icon=os.path.join(root, "packaging", "icon.ico") if os.path.exists(os.path.join(root, "packaging", "icon.ico")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MusicManager")
