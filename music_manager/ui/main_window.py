"""Fereastra principală: două secțiuni (Fișiere, Bibliotecă) plus Setări."""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget

from .. import APP_NAME, __version__
from .cleanup_tab import CleanupTab
from .common import AppState, settings
from .convert_tab import ConvertTab
from .download_tab import DownloadTab
from .library_view import LibraryView
from .settings_tab import SettingsTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 860)
        self.state = AppState()

        self.download = DownloadTab(self.state, self)
        self.cleanup = CleanupTab(self.state, self)
        self.convert = ConvertTab(self.state, self)
        self.library = LibraryView(self.state, self)
        self.settings_tab = SettingsTab(self.state, self)
        self.settings_tab.spotify_changed.connect(self.download.refresh_buttons)
        self.settings_tab.library_changed.connect(lambda: self.library.reload())

        self.files = QTabWidget()
        self.files.addTab(self.download, "Download")
        self.files.addTab(self.cleanup, "Curățenie")
        self.files.addTab(self.convert, "Conversie")


        self.sections = QTabWidget()
        self.sections.setDocumentMode(True)
        self.sections.addTab(self.files, "📁  Fișiere")
        self.sections.addTab(self.library, "🎛  Bibliotecă")
        self.sections.addTab(self.settings_tab, "⚙  Setări")
        self.sections.setStyleSheet("QTabWidget#sections > QTabBar::tab { padding: 8px 22px; font-size: 14px; }")
        self.sections.setObjectName("sections")
        self.sections.currentChanged.connect(lambda _: self.download.refresh_buttons())
        self.setCentralWidget(self.sections)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}"))

        geo = settings().value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)

    def show_tab(self, widget):
        if self.sections.indexOf(widget) >= 0:
            self.sections.setCurrentWidget(widget)
            return
        if self.files.indexOf(widget) >= 0:
            self.sections.setCurrentWidget(self.files)
            self.files.setCurrentWidget(widget)

    def closeEvent(self, e):
        settings().setValue("window/geometry", self.saveGeometry())
        self.library.shutdown()
        for tab in (self.download, self.cleanup, self.convert, self.library, self.settings_tab):
            task = getattr(tab, "task", None)
            if task and task.isRunning():
                task.cancel()
                task.wait(5000)
        super().closeEvent(e)
