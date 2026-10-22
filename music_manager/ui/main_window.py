"""Fereastra principală."""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget

from .. import APP_NAME, __version__
from .common import AppState, settings
from .convert_tab import ConvertTab
from .library_tab import LibraryTab
from .playlist_tab import PlaylistTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1300, 820)
        self.state = AppState()

        self.tabs = QTabWidget()
        self.library = LibraryTab(self.state, self)
        self.convert = ConvertTab(self.state, self)
        self.playlists = PlaylistTab(self.state, self)
        self.tabs.addTab(self.library, "Bibliotecă")
        self.tabs.addTab(self.playlists, "Playlisturi")
        self.tabs.addTab(self.convert, "Conversie")
        self.setCentralWidget(self.tabs)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}"))

        geo = settings().value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)

    def closeEvent(self, e):
        settings().setValue("window/geometry", self.saveGeometry())
        for tab in (self.library, self.convert, self.playlists):
            task = getattr(tab, "task", None)
            if task and task.isRunning():
                task.cancel()
                task.wait(5000)
        super().closeEvent(e)
