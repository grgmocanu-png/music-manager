"""Fereastra principală: bara de sus Playlisturi › Bibliotecă › Curățenie · Explore · Activități · ⚙."""
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
    # ordinea fluxului: Playlisturi › Bibliotecă › Curățenie; Explore separat; Setări din rotiță.
    # Conversia e un pas din Curățenie (pagină fără buton propriu în bară).
    PAGES = [("Playlisturi", "download"), ("Bibliotecă", "library"), ("Curățenie", "cleanup"),
             ("Explore", "explore"), ("Setări", "settings_tab"), ("Conversie", "convert")]
    NAV_OF = {"convert": "cleanup"}          # ce buton e aprins când ești pe o pagină fără buton

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 900)
        self.state = AppState()

        self.download = DownloadTab(self.state, self)
        self.cleanup = CleanupTab(self.state, self)
        self.convert = ConvertTab(self.state, self)
        self.library = LibraryView(self.state, self)
        self.cleanup.library = self.library
        from .explore import ExploreView
        self.explore = ExploreView(self.state, self.library, self.download, self)
        self.settings_tab = SettingsTab(self.state, self)
        self.settings_tab.spotify_changed.connect(self.download.refresh_buttons)
        self.settings_tab.library_changed.connect(lambda: self.library.reload())
        self.download.playlists.changed.connect(self.explore.rebuild_tree)

        # pagina Conversie, cu drum înapoi spre Curățenie
        conv = QWidget()
        cv = QVBoxLayout(conv)
        cv.setContentsMargins(12, 8, 12, 0)
        back = QPushButton("← Curățenie")
        back.setFlat(True)
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self._go_name("cleanup"))
        cv.addWidget(back, 0, Qt.AlignLeft)
        cv.addWidget(self.convert, 1)
        self._convert_page = conv

        top = QWidget()
        top.setObjectName("topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(16, 8, 16, 8)
        tl.setSpacing(4)
        logo = QLabel("Manager")
        logo.setObjectName("logo")
        tl.addWidget(logo)
        tl.addSpacing(22)
        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self._nav_buttons = {}
        for i, (title, attr) in enumerate(self.PAGES):
            self.stack.addWidget(self._convert_page if attr == "convert" else getattr(self, attr))
            if attr == "convert":
                continue
            if attr == "settings_tab":
                b = QPushButton("⚙")
                b.setObjectName("gear")
                b.setToolTip("Setări")
                b.setFixedSize(34, 30)
            else:
                b = QPushButton(title)
                b.setObjectName("navExplore" if attr == "explore" else "nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            self._nav_buttons[attr] = b
            if attr == "explore":
                tl.addSpacing(40)                      # Explore: separat, în alt stil
                tl.addWidget(b)
                tl.addStretch()
                from .jobs import JobsButton
                self.jobs_button = JobsButton(self)
                self.jobs_button.setObjectName("jobs")
                tl.addWidget(self.jobs_button)
                tl.addSpacing(8)
            elif attr == "settings_tab":
                tl.addWidget(b)
                tl.addSpacing(8)
            else:
                tl.addWidget(b)
                if attr != "cleanup":
                    arrow = QLabel("›")
                    arrow.setObjectName("navarrow")
                    tl.addWidget(arrow)
        self.nav.idClicked.connect(self._go)
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
        last = settings().value("window/page_name", "") or "library"
        self._go_name(last if any(a == last for _t, a in self.PAGES) else "library")

        geo = settings().value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)

    def _index(self, attr) -> int:
        return next(i for i, (_t, a) in enumerate(self.PAGES) if a == attr)

    def _go_name(self, attr):
        self._go(self._index(attr))

    def _go(self, i):
        prev = self.PAGES[self.stack.currentIndex()][1] if self.stack.count() else ""
        attr = self.PAGES[i][1]
        self._carry_selection(prev, attr)
        self.stack.setCurrentIndex(i)
        b = self._nav_buttons.get(self.NAV_OF.get(attr, attr))
        if b is not None:
            b.setChecked(True)
        settings().setValue("window/page_name", attr)
        self.download.refresh_buttons()
        if attr == "explore":
            self.explore.rebuild_tree()
        if attr in ("library", "explore"):
            # același player în ambele pagini: muzica merge mai departe, iar piesa e selectată
            getattr(self, attr).attach_player()
        if attr in ("library", "explore", "cleanup"):
            sel = getattr(self, "_carried", None)
            if sel:
                self._carried = None
                getattr(self, attr).focus_paths(sel)

    def _carry_selection(self, prev, nxt):
        """Piesele pe care ai dat click într-o pagină te urmăresc în cealaltă (Curățenie, Bibliotecă, Explore)."""
        if prev == nxt or prev not in ("library", "explore", "cleanup"):
            return
        try:
            paths = getattr(self, prev).selected_paths()
        except Exception:
            paths = []
        if paths:
            self._carried = paths

    # compatibilitate: alte taburi cer „du-mă la …”
    @property
    def sections(self):
        return self.stack

    def show_tab(self, widget):
        if widget is self.convert:
            widget = self._convert_page
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
        from .windows import close_all
        close_all()
        settings().setValue("window/geometry", self.saveGeometry())
        self.library.shutdown()
        for tab in (self.download, self.cleanup, self.convert, self.library, self.explore, self.settings_tab):
            task = getattr(tab, "task", None)
            if task and task.isRunning():
                task.cancel()
                task.wait(5000)
        super().closeEvent(e)
