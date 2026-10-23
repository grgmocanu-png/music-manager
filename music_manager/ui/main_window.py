"""Fereastra principală: bara de sus cu paginile (Download, Curățenie, Conversie, Bibliotecă, Explore, Setări)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from .cleanup_tab import CleanupTab
from .common import AppState, settings
from .convert_tab import ConvertTab
from .download_tab import DownloadTab
from .library_view import LibraryView
from .settings_tab import SettingsTab


class MainWindow(QMainWindow):
    PAGES = [("Download", "download"), ("Curățenie", "cleanup"), ("Conversie", "convert"),
             ("Bibliotecă", "library"), ("Explore", "explore"), ("Setări", "settings_tab")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 900)
        self.state = AppState()

        self.download = DownloadTab(self.state, self)
        self.cleanup = CleanupTab(self.state, self)
        self.convert = ConvertTab(self.state, self)
        self.library = LibraryView(self.state, self)
        from .explore import ExploreView
        self.explore = ExploreView(self.state, self.library, self.download, self)
        self.settings_tab = SettingsTab(self.state, self)
        self.settings_tab.spotify_changed.connect(self.download.refresh_buttons)
        self.settings_tab.library_changed.connect(lambda: self.library.reload())
        self.download.playlists.changed.connect(self.explore.rebuild_tree)

        # bara de sus (ca în machetă): Manager · Fișiere: Download Curățenie Conversie | Bibliotecă Explore Setări
        top = QWidget()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(16, 8, 16, 8)
        tl.setSpacing(4)
        logo = QLabel("Manager")
        logo.setObjectName("logo")
        tl.addWidget(logo)
        tl.addSpacing(18)
        lbl = QLabel("FIȘIERE")
        lbl.setObjectName("navlabel")
        tl.addWidget(lbl)
        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        for i, (title, attr) in enumerate(self.PAGES):
            if attr == "library":
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setObjectName("navsep")
                tl.addSpacing(6)
                tl.addWidget(sep)
                tl.addSpacing(6)
            b = QPushButton(title)
            b.setCheckable(True)
            b.setObjectName("nav")
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            tl.addWidget(b)
            self.stack.addWidget(getattr(self, attr))
        self.nav.idClicked.connect(self._go)
        tl.addStretch()
        from .jobs import JobsButton
        self.jobs_button = JobsButton(self)
        self.jobs_button.setObjectName("jobs")
        tl.addWidget(self.jobs_button)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("muted")
        tl.addWidget(ver)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(top)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar().hide()
        last = int(settings().value("window/page", 3) or 3)
        self._go(last if 0 <= last < len(self.PAGES) else 3)

        geo = settings().value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)

    def _go(self, i):
        self.stack.setCurrentIndex(i)
        self.nav.button(i).setChecked(True)
        settings().setValue("window/page", i)
        self.download.refresh_buttons()
        if self.PAGES[i][1] == "explore":
            self.explore.rebuild_tree()

    # compatibilitate: alte taburi cer „du-mă la …”
    @property
    def sections(self):
        return self.stack

    def show_tab(self, widget):
        i = self.stack.indexOf(widget)
        if i >= 0:
            self._go(i)

    def closeEvent(self, e):
        from PySide6.QtWidgets import QMessageBox
        from .jobs import manager
        writing = [j for j in manager().active() if j.writes]
        if writing and QMessageBox.question(
                self, "Activități în curs",
                f"Mai sunt {len(writing)} activități care modifică fișiere ({writing[0].name}…).\n"
                "Dacă închizi acum, se opresc. Închid oricum?") != QMessageBox.Yes:
            e.ignore()
            return
        manager().shutdown()
        settings().setValue("window/geometry", self.saveGeometry())
        self.library.shutdown()
        for tab in (self.download, self.cleanup, self.convert, self.library, self.explore, self.settings_tab):
            task = getattr(tab, "task", None)
            if task and task.isRunning():
                task.cancel()
                task.wait(5000)
        super().closeEvent(e)
