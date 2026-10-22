"""Componente comune ale interfeței: lucru în fundal, tabelul de piese, starea aplicației."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QSettings, QSortFilterProxyModel,
    QStandardPaths, Qt, QThread, QUrl, Signal,
)
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from ..core.library import Track, normalize
from ..core.playlist import to_camelot


def data_dir() -> str:
    d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    os.makedirs(d, exist_ok=True)
    return d


def undo_dir() -> str:
    return os.path.join(data_dir(), "anulare")


def settings() -> QSettings:
    return QSettings()


def fmt_duration(secs: float) -> str:
    secs = int(round(secs or 0))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def open_file(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal_in_folder(path: str) -> None:
    """Arată fișierul în Finder / Explorer."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
    except Exception:
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class Task(QThread):
    """Rulează o funcție lungă în fundal, ca fereastra să nu înghețe.

    Funcția primește (progress, cancelled):
      progress(done, total, text="") — actualizează bara de progres
      cancelled() — True dacă utilizatorul a apăsat „Oprește”
    """
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self, mode=True):
        """mode poate fi True sau un mod de oprire (ex. „după piesa curentă”)."""
        self._cancel = mode

    def run(self):
        try:
            result = self._fn(lambda d, t, txt="": self.progress.emit(d, t, txt),
                              lambda: self._cancel)
            self.succeeded.emit(result)
        except Exception as e:
            self.failed.emit(f"{e.__class__.__name__}: {e}")


class AppState(QObject):
    """Biblioteca încărcată, comună tuturor taburilor."""
    tracks_changed = Signal()
    send_to_convert = Signal(list)    # listă de căi
    send_to_playlist = Signal(list)   # listă de Track

    def __init__(self):
        super().__init__()
        self.tracks: list[Track] = []
        raw = settings().value("library/folders", []) or []
        if isinstance(raw, str):
            raw = [raw]
        self.folders: list[str] = [f for f in raw if f]

    def set_folders(self, folders: list[str]):
        self.folders = folders
        settings().setValue("library/folders", folders)

    def set_tracks(self, tracks: list[Track]):
        self.tracks = tracks
        self.tracks_changed.emit()


# --- tabelul de piese -------------------------------------------------------
COLUMNS = [
    ("Artist", "artist"), ("Titlu", "title"), ("Album", "album"), ("Gen", "genre"),
    ("An", "year"), ("BPM", "bpm"), ("Cheie", "key"), ("Durată", "duration"),
    ("Calitate", "quality"), ("Folder", "folder"),
]
SORT_ROLE = Qt.UserRole + 1
TRACK_ROLE = Qt.UserRole + 2


class TrackModel(QAbstractTableModel):
    def __init__(self, tracks: list[Track] | None = None, parent=None):
        super().__init__(parent)
        self.tracks: list[Track] = tracks or []

    def set_tracks(self, tracks: list[Track]):
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        col = COLUMNS[index.column()][1]
        if role == TRACK_ROLE:
            return t
        if role == Qt.DisplayRole:
            if col == "title":
                return t.display_title
            if col == "duration":
                return fmt_duration(t.duration)
            if col == "quality":
                return t.quality_label
            if col == "folder":
                return os.path.dirname(t.path)
            if col == "key":
                cam = to_camelot(t.key)
                return f"{cam} ({t.key})" if cam and cam.upper() != t.key.upper() else (t.key or "")
            return getattr(t, col)
        if role == SORT_ROLE:
            if col in ("bpm", "year", "track"):
                v = getattr(t, col)
                return int(v) if v.isdigit() else -1
            if col == "duration":
                return t.duration
            if col == "quality":
                return t.quality_score()
            if col == "key":
                cam = to_camelot(t.key)
                return (int(cam[:-1]) * 2 + (cam[-1] == "B")) if cam else 99
            if col == "title":
                return t.display_title.lower()
            if col == "folder":
                return t.path.lower()
            return str(getattr(t, col)).lower()
        if role == Qt.ToolTipRole:
            return t.path + (f"\n⚠ {t.error}" if t.error else "")
        if role == Qt.ForegroundRole and t.error:
            return QColor("#c0392b")
        if role == Qt.TextAlignmentRole and col in ("year", "bpm", "duration"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class TrackFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self._words: list[str] = []

    def set_text(self, text: str):
        self._words = normalize(text).split()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self._words:
            return True
        t: Track = self.sourceModel().tracks[row]
        hay = normalize(" ".join([t.artist, t.title, t.album, t.genre, t.filename]))
        return all(w in hay for w in self._words)


class TrackTable(QTableView):
    def __init__(self, parent=None, hidden_columns=()):
        super().__init__(parent)
        self.source = TrackModel(parent=self)
        self.proxy = TrackFilterProxy(self)
        self.proxy.setSourceModel(self.source)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.AscendingOrder)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(24)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        widths = {"artist": 180, "title": 240, "album": 160, "genre": 110, "year": 50,
                  "bpm": 50, "key": 80, "duration": 60, "quality": 150}
        for i, (_, col) in enumerate(COLUMNS):
            if col in widths:
                self.setColumnWidth(i, widths[col])
            if col in hidden_columns:
                self.setColumnHidden(i, True)
        self.double_click_handler = lambda t: open_file(t.path)
        self.doubleClicked.connect(lambda idx: self.double_click_handler(self.track_at(idx)))

    def set_tracks(self, tracks):
        self.source.set_tracks(tracks)

    def track_at(self, proxy_index) -> Track:
        return self.source.tracks[self.proxy.mapToSource(proxy_index).row()]

    def selected_tracks(self) -> list[Track]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.track_at(self.proxy.index(r, 0)) for r in rows]

    def visible_tracks(self) -> list[Track]:
        return [self.track_at(self.proxy.index(r, 0)) for r in range(self.proxy.rowCount())]
