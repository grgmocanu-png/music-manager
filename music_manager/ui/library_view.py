"""Biblioteca (tip Lexicon): toate piesele, filtre, playlisturi (manuale și inteligente),
player cu waveform, analiză BPM/tonalitate, colecții Traktor/Rekordbox/VirtualDJ, export."""
from __future__ import annotations

import json
import os
from datetime import datetime

from PySide6.QtCore import (
    QAbstractTableModel, QMimeData, QModelIndex, QObject, QSortFilterProxyModel, QStandardPaths, Qt,
    QThread, QTimer, Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableView,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import analysis as an
from ..core import djcollections as djc
from ..core import library as lib
from ..core import libdb as ldb
from ..core.playlist import CAMELOT, compatible_keys, to_camelot, write_m3u
from .common import (
    AppState, Task, data_dir, fmt_duration, open_file, reveal_in_folder, settings,
)
from .dialogs import (
    ArtistCleanupDialog, BulkEditDialog, GenreCleanupDialog, SmartPlaylistDialog, TagFinderDialog,
    families, write_tags_in_background,
)
from .player import PlayerPanel

COLS = [("", "play"), ("#", "num"), ("Artist", "artist"), ("Titlu", "title"), ("Familie", "family"), ("Gen", "genre"),
        ("Stil", "style"), ("Rol", "role"), ("E", "energy"), ("BPM", "bpm"), ("Cheie", "key"), ("An", "year"), ("★", "rating"),
        ("Durată", "duration"), ("Label", "label"), ("Adăugată", "added"), ("Calitate", "quality"),
        ("Sursă BPM / cheie", "src"), ("Playlisturi", "playlists"), ("Folder", "folder")]
DEFAULT_HIDDEN = {"src", "folder", "quality"}
SORT_ROLE = Qt.UserRole + 1
MIME = "application/x-musicmanager-paths"


def analysis_dir() -> str:
    return os.path.join(data_dir(), "analiza")


def documents_dirs() -> list[str]:
    out = [QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)]
    home = os.path.expanduser("~")
    out += [os.path.join(home, "Documents"), os.path.join(home, "OneDrive", "Documents"),
            os.path.join(home, "OneDrive", "Documente"), os.path.join(home, "Documente")]
    seen, res = set(), []
    for d in out:
        if d and os.path.isdir(d) and os.path.normcase(d) not in seen:
            seen.add(os.path.normcase(d))
            res.append(d)
    return res


def source_priority() -> list[str]:
    try:
        p = json.loads(settings().value("library/priority", "") or "[]")
    except ValueError:
        p = []
    return p if p and set(p) == set(ldb.DEFAULT_PRIORITY) else list(ldb.DEFAULT_PRIORITY)


# --------------------------------------------------------------------------
class ItemModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[ldb.LibItem] = []
        self.playing = ""

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def refresh_row(self, path: str):
        for i, it in enumerate(self.items):
            if it.path == path:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s][0]
        return None

    def flags(self, index):
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        md = QMimeData()
        md.setData(MIME, json.dumps([self.items[r].path for r in rows]).encode("utf-8"))
        return md

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        t = it.track
        col = COLS[index.column()][1]
        if role == Qt.DisplayRole:
            if col == "play":
                return "🔊" if it.path == self.playing else "▶"
            if col == "num":
                return index.row() + 1
            if col == "artist":
                return t.artist
            if col == "title":
                return t.display_title
            if col == "family":
                return it.family
            if col == "playlists":
                return ", ".join(it.playlists)
            if col == "genre":
                return t.genre
            if col == "style":
                return it.style
            if col == "role":
                return it.role or (f"{it.role_suggested}?" if it.role_suggested else "")
            if col == "energy":
                return it.energy or ""
            if col == "bpm":
                return (f"{it.bpm:.1f}".rstrip("0").rstrip(".")) if it.bpm else ""
            if col == "key":
                cam = it.camelot
                return f"{cam}  {CAMELOT.get(cam, '')}" if cam else it.key
            if col == "year":
                return t.year
            if col == "rating":
                return "★" * it.rating if it.rating else ""
            if col == "duration":
                return fmt_duration(t.duration)
            if col == "label":
                return t.label
            if col == "added":
                return (it.date_added or "")[:10]
            if col == "quality":
                return t.quality_label
            if col == "src":
                return " / ".join(x for x in (it.bpm_src, it.key_src) if x)
            if col == "folder":
                return os.path.dirname(it.path)
        if role == SORT_ROLE:
            if col == "num":
                return index.row()
            if col == "bpm":
                return it.bpm
            if col == "key":
                cam = it.camelot
                return int(cam[:-1]) * 2 + (cam[-1] == "B") if cam else 99
            if col == "rating":
                return it.rating
            if col == "energy":
                return it.energy
            if col == "role":
                r = it.role or it.role_suggested
                return ldb.ROLES.index(r) if r in ldb.ROLES else 99
            if col == "duration":
                return t.duration
            if col == "quality":
                return t.quality_score()
            if col == "year":
                return int(t.year) if t.year.isdigit() else 0
            v = self.data(index, Qt.DisplayRole)
            return str(v or "").lower()
        if role == Qt.ForegroundRole:
            if it.missing or it.offline:
                return QBrush(QColor("#c0392b" if it.missing else "#888888"))
            if col == "play" and it.path == self.playing:
                return QBrush(QColor("#27ae60"))
            if col == "role" and not it.role and it.role_suggested:
                return QBrush(QColor("#999999"))
        if role == Qt.ToolTipRole:
            if it.offline:
                return "Discul pe care e piesa nu e conectat: " + it.path
            if it.missing:
                return "Fișierul nu mai există: " + it.path
            if col == "src":
                return "De unde vine BPM-ul / tonalitatea (prioritatea se schimbă în Setări)"
            if col == "role":
                if it.role:
                    return "Rol pus de tine. Tastele 1–6 schimbă rolul, 0 îl șterge."
                if it.plays >= 2:
                    return f"Sugestie din seturile tale: cântată de {it.plays} ori, în medie la {it.avg_pos:.0%} din set."
                return "Sugestie din energie și BPM. Tastele 1–6: Opener, Warm-up, Build, Peak, Closer, After."
            if col == "energy":
                return f"Energie {it.energy}/10 (din {it.energy_src.lower()})" if it.energy else ""
            return it.path
        if role == Qt.FontRole and col == "role" and not it.role and it.role_suggested:
            f = QFont()
            f.setItalic(True)
            return f
        if role == Qt.TextAlignmentRole and col in ("bpm", "year", "duration", "num", "play", "energy"):
            return int(Qt.AlignCenter)
        return None


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.words: list[str] = []
        self.genres: set[str] = set()
        self.bpm_lo = self.bpm_hi = 0
        self.keys: set[str] = set()
        self.role = ""
        self.role_sugg = False
        self.families: set[str] = set()

    def set_filters(self, text="", genres=(), bpm_lo=0, bpm_hi=0, key="", harmonic=False, role="", role_sugg=False,
                    families=()):
        self.role, self.role_sugg = role, role_sugg
        self.families = set(families)
        self.words = lib.normalize(text).split()
        self.genres = {lib.normalize(g) for g in genres}
        self.bpm_lo, self.bpm_hi = bpm_lo, bpm_hi
        self.keys = (compatible_keys(key) if harmonic else {key}) if key else set()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        it = self.sourceModel().items[row]
        t = it.track
        if self.words:
            hay = lib.normalize(" ".join([t.artist, t.title, t.album, t.genre, t.comment, t.label, t.filename,
                                          it.family, " ".join(it.playlists)]))
            if not all(w in hay for w in self.words):
                return False
        if self.families and it.family not in self.families:
            return False
        if self.genres and lib.normalize(t.genre) not in self.genres:
            return False
        if (self.bpm_lo or self.bpm_hi) and not ldb._bpm_in(it.bpm, self.bpm_lo, self.bpm_hi):
            return False
        if self.keys and it.camelot not in self.keys:
            return False
        if self.role and (it.role_or_suggestion if self.role_sugg else it.role) != self.role:
            return False
        return True


class TrackTable(QTableView):
    role_key = Signal(str)          # tastele 1–6 / 0

    def keyPressEvent(self, e):
        k = e.text()
        if k and k in "0123456" and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self.role_key.emit("" if k == "0" else ldb.ROLES[int(k) - 1])
            return
        if e.key() == Qt.Key_Space:
            self.role_key.emit("__play__")
            return
        super().keyPressEvent(e)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = ItemModel(self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(23)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        h.setSectionsMovable(True)
        widths = [28, 38, 170, 230, 95, 100, 130, 70, 32, 55, 85, 48, 60, 55, 110, 82, 120, 160, 120]
        for i, w in enumerate(widths):
            self.setColumnWidth(i, w)
        state = settings().value("library/header3")
        restored = False
        if state is not None:
            try:
                restored = h.restoreState(state)
            except Exception:
                restored = False
        if not restored:
            for i, (_, key) in enumerate(COLS):
                self.setColumnHidden(i, key in DEFAULT_HIDDEN)
        h.sectionResized.connect(lambda *_: self._save_header())
        h.sectionMoved.connect(lambda *_: self._save_header())
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)

    def _save_header(self):
        settings().setValue("library/header3", self.horizontalHeader().saveState())

    def _header_menu(self, pos):
        """Click dreapta pe antet: alegi ce coloane vezi (ordinea o schimbi trăgând de antet)."""
        m = QMenu(self)
        m.addAction("Coloane afișate (trage de antet ca să schimbi ordinea):").setEnabled(False)
        for i, (title, key) in enumerate(COLS):
            if key in ("play", "num"):
                continue
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(not self.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: (self.setColumnHidden(i, not on), self._save_header()))
        m.addSeparator()
        m.addAction("Revino la coloanele implicite", self._reset_columns)
        m.exec(self.horizontalHeader().viewport().mapToGlobal(pos))

    def _reset_columns(self):
        h = self.horizontalHeader()
        for i in range(len(COLS)):
            h.moveSection(h.visualIndex(i), i)
            self.setColumnHidden(i, COLS[i][1] in DEFAULT_HIDDEN)
        self._save_header()

    def item_at(self, proxy_index) -> ldb.LibItem:
        return self.model_.items[self.proxy.mapToSource(proxy_index).row()]

    def selected(self) -> list[ldb.LibItem]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.item_at(self.proxy.index(r, 0)) for r in rows]

    def visible(self) -> list[ldb.LibItem]:
        return [self.item_at(self.proxy.index(r, 0)) for r in range(self.proxy.rowCount())]


class SideTree(QTreeWidget):
    dropped = Signal(str, list)     # id playlist, căi

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _pl_at(self, pos):
        it = self.itemAt(pos)
        d = it.data(0, Qt.UserRole) if it else None
        return d[1] if d and d[0] == "pl" else None

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME) and self._pl_at(e.position().toPoint()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        pid = self._pl_at(e.position().toPoint())
        if pid and e.mimeData().hasFormat(MIME):
            paths = json.loads(bytes(e.mimeData().data(MIME)).decode("utf-8"))
            self.dropped.emit(pid, paths)
            e.acceptProposedAction()


# --------------------------------------------------------------------------
class AnalysisWorker(QObject):
    """Analizează piesele una câte una, ca activitate în fundal (se poate pune pe pauză).
    Cele cerute pentru player trec primele."""
    result = Signal(str, object)
    status = Signal(int)          # câte au rămas

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[str] = []
        self.stop_flag = False
        self.bpm_range = (85, 175)
        self.job = None

    def add(self, paths, front=False):
        new = [p for p in paths if p not in self.queue]
        if front:
            self.queue[:0] = new
        else:
            self.queue.extend(new)
        self.status.emit(len(self.queue))
        if self.job is None or not self.job.isRunning():
            from .jobs import Job
            self.job = Job("Analiză BPM / tonalitate / energie / waveform", self._loop, writes=False)
            self.job.start()

    def _loop(self, progress, cancelled):
        done = 0
        while self.queue and not self.stop_flag:
            if cancelled():
                break
            path = self.queue.pop(0)
            done += 1
            progress(done, done + len(self.queue), os.path.basename(path))
            self.status.emit(len(self.queue))
            if not os.path.exists(path):
                continue
            a = an.analyze(path, analysis_dir(), bpm_range=self.bpm_range)
            self.result.emit(path, a)
        self.status.emit(0)

    def wait(self, ms=3000):
        if self.job:
            self.job.cancel()
            self.job.wait(ms)


# --------------------------------------------------------------------------
class LibraryView(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.db = ldb.LibraryDB(os.path.join(data_dir(), "biblioteca_db.json"))
        self.collections: list[djc.Collection] = []
        self.items: list[ldb.LibItem] = []
        self.by_path: dict[str, ldb.LibItem] = {}
        self.task: Task | None = None
        self.view = ("all",)
        self.mix_base: ldb.LibItem | None = None
        self._col_mtimes: dict[str, float] = {}

        self.worker = AnalysisWorker(self)
        self.worker.result.connect(self._analysis_done)
        self.worker.status.connect(self._analysis_status)

        # --- stânga ---
        self.tree = SideTree()
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.dropped.connect(self._drop_to_playlist)
        b_new = QPushButton("+ Playlist")
        b_smart = QPushButton("+ Inteligent")
        b_new.clicked.connect(self.new_playlist)
        b_smart.clicked.connect(lambda: self.edit_smart(None))
        lb = QHBoxLayout()
        lb.addWidget(b_new)
        lb.addWidget(b_smart)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.tree, 1)
        ll.addLayout(lb)

        # --- bara de sus ---
        self.b_reload = QPushButton("⟳ Actualizează")
        self.b_reload.setToolTip("Rescanează folderele și recitește colecțiile Traktor / Rekordbox / VirtualDJ")
        self.b_reload.clicked.connect(self.reload)
        tools = QToolButton()
        tools.setText("Unelte ▾")
        tools.setPopupMode(QToolButton.InstantPopup)
        tm = QMenu(tools)
        tm.addAction("Analizează BPM / tonalitate unde lipsesc", self.analyze_missing)
        tm.addAction("Taguri online (gen, an, label, copertă)…", lambda: self.tag_finder(self.table.visible()))
        tm.addSeparator()
        tm.addAction("Curăță genurile…", self.genre_cleanup)
        tm.addAction("Curăță artiștii…", self.artist_cleanup)
        tm.addAction("Caută piesele mutate…", self.find_moved)
        tm.addSeparator()
        tm.addAction("Exportă toate playlisturile în Rekordbox (XML)", self.export_all_rekordbox)
        tm.addAction("Exportă toate playlisturile pentru Traktor (NML)", self.export_all_traktor)
        tools.setMenu(tm)
        exp = QToolButton()
        exp.setText("Exportă ▾")
        exp.setPopupMode(QToolButton.InstantPopup)
        em = QMenu(exp)
        em.addAction("Ce e afișat acum → Traktor (NML)…", lambda: self.export_traktor(*self._current_for_export()))
        em.addAction("Ce e afișat acum → M3U (orice player / stick)…", lambda: self.export_m3u(*self._current_for_export()))
        em.addAction("Ce e afișat acum → copiază piesele pe stick / folder…", lambda: self.copy_to_folder(*self._current_for_export()))
        em.addAction("Ce e afișat acum → CSV…", lambda: self.export_csv(self._current_for_export()[1]))
        em.addSeparator()
        em.addAction("Toate playlisturile mele → Rekordbox (XML)", self.export_all_rekordbox)
        em.addAction("Toate playlisturile mele → Traktor (NML)", self.export_all_traktor)
        exp.setMenu(em)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Caută artist, titlu, gen, stil, label…")
        self.search.setClearButtonEnabled(True)
        self.fam_btn = QToolButton()
        self.fam_btn.setText("Familie: toate ▾")
        self.fam_btn.setPopupMode(QToolButton.InstantPopup)
        self.fam_menu = QMenu(self.fam_btn)
        self.fam_btn.setMenu(self.fam_menu)
        self.fam_btn.setStyleSheet("font-weight: bold;")
        self.genre_btn = QToolButton()
        self.genre_btn.setText("Gen: toate ▾")
        self.genre_btn.setPopupMode(QToolButton.InstantPopup)
        self.genre_menu = QMenu(self.genre_btn)
        self.genre_btn.setMenu(self.genre_menu)
        self.bpm_lo, self.bpm_hi = QSpinBox(), QSpinBox()
        for s in (self.bpm_lo, self.bpm_hi):
            s.setRange(0, 250)
            s.setSpecialValueText("—")
            s.setMaximumWidth(60)
        self.key = QComboBox()
        self.key.addItem("Orice cheie", "")
        for code, note in CAMELOT.items():
            self.key.addItem(f"{code}  {note}", code)
        self.harm = QCheckBox("+ compatibile")
        self.role_f = QComboBox()
        self.role_f.addItem("Orice rol", ("", False))
        for r in ldb.ROLES:
            self.role_f.addItem(r, (r, False))
        for r in ldb.ROLES:
            self.role_f.addItem(f"{r} (+ sugestii)", (r, True))
        self.role_f.currentIndexChanged.connect(self.apply_filters)
        b_clear = QPushButton("✕")
        b_clear.setToolTip("Șterge filtrele")
        b_clear.setMaximumWidth(30)
        b_clear.clicked.connect(self.clear_filters)
        for w in (self.search,):
            w.textChanged.connect(self.apply_filters)
        for w in (self.bpm_lo, self.bpm_hi):
            w.valueChanged.connect(self.apply_filters)
        self.key.currentIndexChanged.connect(self.apply_filters)
        self.harm.toggled.connect(self.apply_filters)

        bar = QHBoxLayout()
        bar.addWidget(self.b_reload)
        bar.addWidget(tools)
        bar.addWidget(exp)
        bar.addSpacing(10)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.fam_btn)
        bar.addWidget(self.genre_btn)
        bar.addWidget(QLabel("BPM"))
        bar.addWidget(self.bpm_lo)
        bar.addWidget(QLabel("–"))
        bar.addWidget(self.bpm_hi)
        bar.addWidget(self.key)
        bar.addWidget(self.harm)
        bar.addWidget(self.role_f)
        bar.addWidget(b_clear)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.count = QLabel()
        self.count.setStyleSheet("color: gray;")
        th = QHBoxLayout()
        th.addWidget(self.title)
        th.addStretch()
        th.addWidget(self.count)

        self.table = TrackTable()
        self.table.doubleClicked.connect(lambda ix: self.play(self.table.item_at(ix)))
        self.table.clicked.connect(self._table_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.role_key.connect(self._role_key)

        self.player = PlayerPanel()
        self.player.request_next.connect(self._play_next)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(bar)
        cl.addLayout(th)
        cl.addWidget(self.table, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 5)
        split.setSizes([230, 1100])

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(self.player)
        lay.addWidget(self.status)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._periodic)
        self.timer.start(3 * 60 * 1000)
        self.rebuild_tree()
        QTimer.singleShot(300, self.reload)

    # ===== încărcare =====
    def library_folders(self) -> list[str]:
        folders = [f for f in self.state.folders if os.path.isdir(f)]
        dl = settings().value("download/dir", "") or ""
        if dl and os.path.isdir(dl) and not any(
                os.path.normcase(os.path.abspath(dl)).startswith(os.path.normcase(os.path.abspath(f)) + os.sep)
                or os.path.normcase(os.path.abspath(dl)) == os.path.normcase(os.path.abspath(f)) for f in folders):
            folders.append(dl)
        return folders

    def collection_paths(self) -> list[tuple[str, str]]:
        out = []
        tk = settings().value("collections/traktor", "") or djc.find_traktor_collection(documents_dirs())
        if tk and os.path.isfile(tk) and settings().value("collections/use_traktor", True) not in (False, "false"):
            out.append(("Traktor", tk))
        rb = settings().value("collections/rekordbox_xml", "") or ""
        if rb and os.path.isfile(rb):
            out.append(("Rekordbox", rb))
        vdj = settings().value("collections/virtualdj", "") or djc.find_virtualdj_db(documents_dirs())
        if vdj and os.path.isfile(vdj) and settings().value("collections/use_virtualdj", True) not in (False, "false"):
            out.append(("VirtualDJ", vdj))
        return out

    def reload(self, quiet=False):
        if self.task and self.task.isRunning():
            return
        folders = self.library_folders()
        cols = self.collection_paths()
        cache = lib.LibraryCache(os.path.join(data_dir(), "biblioteca.json"))

        def work(progress, cancelled):
            tracks = lib.scan(folders, cache, lambda d, t: progress(d, t, f"Citesc tagurile: {d}/{t}"),
                              cancelled) if folders else []
            parsed = []
            history = {}
            for name, path in cols:
                if name == "Traktor":
                    progress(0, 0, "Citesc istoricul seturilor din Traktor…")
                    history = ldb.parse_traktor_history(os.path.join(os.path.dirname(path), "History"))
                progress(0, 0, f"Citesc colecția {name}…")
                c = {"Traktor": djc.parse_traktor, "Rekordbox": djc.parse_rekordbox_xml,
                     "VirtualDJ": djc.parse_virtualdj}[name](path)
                parsed.append(c)
            return tracks, parsed, history

        from .jobs import Job
        self.task = Job("Încarc Biblioteca", work, writes=False)
        self.task.progress.connect(lambda d, t, txt: self.status.setText(txt))
        self.task.succeeded.connect(lambda r: self._loaded(r, quiet))
        self.task.failed.connect(lambda m: self.status.setText("Eroare la încărcare: " + m))
        self.b_reload.setEnabled(False)
        if not quiet:
            self.status.setText("Încarc biblioteca…")
        self.task.start()

    def _loaded(self, result, quiet):
        tracks, cols, history = result
        self.history = history
        self.b_reload.setEnabled(True)
        self.collections = cols
        self._col_mtimes = {c.path: c.mtime for c in cols}
        self._tracks = tracks
        self._rebuild_items()
        self.state.set_tracks(tracks)
        msgs = [f"{len(self.items)} piese"]
        for c in cols:
            msgs.append(f"{c.source}: {len(c.tracks)} piese" if not c.error else f"{c.source}: eroare ({c.error[:60]})")
        if history:
            msgs.append(f"istoric Traktor: {len(history)} piese cântate în seturi")
        if not cols:
            msgs.append("nicio colecție DJ găsită (vezi Setări)")
        self.status.setText(" · ".join(msgs))
        self.rebuild_tree()
        self.show_view(self.view)
        if settings().value("library/auto_analyze", True) not in (False, "false"):
            self.analyze_missing(silent=True)
        self._auto_export()

    def _rebuild_items(self):
        # date analizate deja (din cache) -> în baza bibliotecii, ca să fie folosite imediat
        for t in self._tracks:
            e = self.db.extra(t.path)
            if not (e.an_bpm or e.an_key):
                a = an.load_cached(t.path, analysis_dir())
                if a and not a.error:
                    e.an_bpm, e.an_key, e.an_drops = a.bpm, a.key, a.drops
            if not e.an_energy:
                a = an.load_cached(t.path, analysis_dir())
                if a and not a.error:
                    e.an_energy = a.energy
        self.items = ldb.build_items(self._tracks, self.db, self.collections, source_priority(),
                                     getattr(self, "history", {}))
        self.by_path = {djc.norm_path(it.path): it for it in self.items}
        self._annotate(self.items)
        self.db.save()
        self._fill_genre_menu()

    def _annotate(self, items):
        """Familia de gen și playlisturile în care apare fiecare piesă."""
        from .dialogs import genre_mapper
        mapper = genre_mapper()
        member: dict[str, list[str]] = {}
        for p in self.db.playlists:
            if p.kind == "manual":
                for t in p.tracks:
                    member.setdefault(self._reloc(t), []).append(p.name)
        for c in self.collections:
            if c.error:
                continue
            for name, paths in c.playlists.items():
                for t in paths:
                    lst = member.setdefault(self._reloc(t), [])
                    label = f"{c.source}: {name}"
                    if label not in lst:
                        lst.append(label)
        for it in items:
            g = it.track.genre.strip()
            fam = mapper.family_of(g) if g else ""
            if not fam:
                style = it.style.split(",")[0].strip()
                fam = mapper.family_of(style) if style else ""
            it.family = fam
            it.playlists = member.get(djc.norm_path(it.path), [])

    def _reloc(self, path: str) -> str:
        k = djc.norm_path(path)
        new = self.db.relocations.get(k)
        return djc.norm_path(new) if new else k

    def _periodic(self):
        """La câteva minute: piese noi în folderul de download sau colecții modificate în Traktor etc."""
        if self.task and self.task.isRunning():
            return
        changed = False
        for name, path in self.collection_paths():
            try:
                if os.path.getmtime(path) != self._col_mtimes.get(path):
                    changed = True
            except OSError:
                pass
        dl = settings().value("download/dir", "") or ""
        if dl and os.path.isdir(dl):
            known = {djc.norm_path(it.path) for it in self.items}
            for f in lib.find_audio_files([dl]):
                if djc.norm_path(f) not in known:
                    changed = True
                    break
        if changed:
            self.reload(quiet=True)

    # ===== copacul din stânga =====
    def rebuild_tree(self):
        sel = self.view
        if self.items:
            self._annotate(self.items)      # coloana „Playlisturi” rămâne la zi după orice schimbare
        self.tree.clear()

        def add(parent, text, data, bold=False):
            it = QTreeWidgetItem([text])
            it.setData(0, Qt.UserRole, data)
            if bold:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            if data == sel:
                self.tree.setCurrentItem(it)
            return it

        n_new = len(self._items_new()) if self.items else 0
        add(None, f"🎵 Toată biblioteca ({len(self.items)})", ("all",), True)
        add(None, f"🆕 Noi — folderul de download ({n_new})", ("new",))
        if any(c.source == "Traktor" and not c.error for c in self.collections):
            n = sum(1 for it in self.items if not it.in_traktor)
            add(None, f"⚠ Nu sunt în Traktor ({n})", ("not_traktor",))
        missing = self._missing_items()
        if missing:
            add(None, f"✗ Lipsă / offline ({len(missing)})", ("missing",))
        manual = add(None, "Playlisturi", ("hdr",), True)
        smart = add(None, "Playlisturi inteligente", ("hdr",), True)
        for p in self.db.playlists:
            if p.kind == "smart":
                add(smart, f"⚡ {p.name}", ("smart", p.id))
            else:
                add(manual, f"♪ {p.name} ({len(p.tracks)})", ("pl", p.id))
        for c in self.collections:
            if c.playlists and not c.error:
                ext = add(None, f"Din {c.source} (doar citire)", ("hdr",), True)
                for name in sorted(c.playlists, key=str.lower):
                    add(ext, f"{name} ({len(c.playlists[name])})", ("ext", c.source, name))
                ext.setExpanded(False)
        manual.setExpanded(True)
        smart.setExpanded(True)
        if self.mix_base:
            add(None, f"🎚 Se mixează cu: {self.mix_base.track.artist} - {self.mix_base.track.display_title}",
                ("mix",))

    def _tree_clicked(self, item, _col):
        d = item.data(0, Qt.UserRole)
        if d and d[0] != "hdr":
            self.show_view(tuple(d))

    # ===== ce se afișează =====
    def _items_new(self):
        dl = settings().value("download/dir", "") or ""
        if not dl:
            return []
        base = os.path.normcase(os.path.abspath(dl)) + os.sep
        return [it for it in self.items if os.path.normcase(os.path.abspath(it.path)).startswith(base)]

    def _missing_items(self):
        out, seen = [], set()
        paths = [t for p in self.db.playlists for t in p.tracks]
        for path in paths:
            k = self._reloc(path)
            if k in self.by_path or k in seen:
                continue
            seen.add(k)
            drive = os.path.splitdrive(path)[0]
            offline = bool(drive) and not os.path.exists(drive + os.sep)
            if os.path.exists(path) and not offline:
                continue
            t = lib.Track(path=path, title=os.path.splitext(os.path.basename(path))[0])
            out.append(ldb.LibItem(track=t, missing=not offline, offline=offline))
        return out

    def _resolve(self, paths) -> list[ldb.LibItem]:
        out = []
        missing = {djc.norm_path(m.path): m for m in self._missing_items()}
        for p in paths:
            k = self._reloc(p)
            it = self.by_path.get(k) or missing.get(k)
            if it is None:
                t = lib.Track(path=p, title=os.path.splitext(os.path.basename(p))[0])
                it = ldb.LibItem(track=t, missing=not os.path.exists(p))
            out.append(it)
        return out

    def items_for(self, view) -> tuple[str, list[ldb.LibItem]]:
        kind = view[0]
        if kind == "new":
            return "Noi — folderul de download", self._items_new()
        if kind == "not_traktor":
            return "Nu sunt în colecția Traktor", [it for it in self.items if not it.in_traktor]
        if kind == "missing":
            return "Lipsă / offline (din playlisturi)", self._missing_items()
        if kind == "pl":
            p = self.db.get_playlist(view[1])
            if p:
                return p.name, self._resolve(p.tracks)
        if kind == "smart":
            p = self.db.get_playlist(view[1])
            if p:
                return f"⚡ {p.name}", ldb.smart_filter(self.items, p.rules)
        if kind == "ext":
            c = next((c for c in self.collections if c.source == view[1]), None)
            if c:
                return f"{view[1]}: {view[2]}", self._resolve(c.playlists.get(view[2], []))
        if kind == "mix" and self.mix_base:
            return (f"Se mixează cu {self.mix_base.track.artist} - {self.mix_base.track.display_title} "
                    f"({self.mix_base.camelot or '?'} · {self.mix_base.bpm or '?'} BPM)"), \
                ldb.mixable(self.mix_base, self.items)
        return "Toată biblioteca", self.items

    def show_view(self, view):
        self.view = view
        title, items = self.items_for(view)
        self.title.setText(title)
        sort_col = self.table.horizontalHeader().sortIndicatorSection()
        sort_ord = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.model_.set_items(items)
        if view[0] in ("pl", "ext", "mix"):
            self.table.sortByColumn(1, Qt.AscendingOrder)     # ordinea playlistului
        else:
            self.table.sortByColumn(sort_col if sort_col > 1 else 2, sort_ord)
        self.apply_filters()

    def _fill_family_menu(self):
        current = {a.text().split("  (")[0] for a in self.fam_menu.actions() if a.isCheckable() and a.isChecked()}
        self.fam_menu.clear()
        a = self.fam_menu.addAction("Toate familiile")
        a.triggered.connect(self._clear_families)
        self.fam_menu.addSeparator()
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.family or "(nerecunoscut)"] = counts.get(it.family or "(nerecunoscut)", 0) + 1
        from .dialogs import families
        order = families() + ["(nerecunoscut)"]
        for f in order:
            if f not in counts:
                continue
            act = QAction(f"{f}  ({counts[f]})", self.fam_menu)
            act.setCheckable(True)
            act.setChecked(f in current)
            act.toggled.connect(self.apply_filters)
            self.fam_menu.addAction(act)

    def _clear_families(self):
        for a in self.fam_menu.actions():
            if a.isCheckable():
                a.blockSignals(True)
                a.setChecked(False)
                a.blockSignals(False)
        self.apply_filters()

    def _fill_genre_menu(self):
        self._fill_family_menu()
        current = {a.text() for a in self.genre_menu.actions() if a.isCheckable() and a.isChecked()}
        self.genre_menu.clear()
        counts = ldb.genre_counts(self.items)
        a = self.genre_menu.addAction("Toate genurile")
        a.triggered.connect(self._clear_genres)
        self.genre_menu.addSeparator()
        for g, n in counts:
            if not g:
                continue
            act = QAction(g, self.genre_menu)
            act.setCheckable(True)
            act.setChecked(g in current)
            act.setToolTip(f"{n} piese")
            act.toggled.connect(self.apply_filters)
            self.genre_menu.addAction(act)

    def _clear_genres(self):
        for a in self.genre_menu.actions():
            if a.isCheckable():
                a.blockSignals(True)
                a.setChecked(False)
                a.blockSignals(False)
        self.apply_filters()

    def apply_filters(self):
        genres = [a.text() for a in self.genre_menu.actions() if a.isCheckable() and a.isChecked()]
        self.genre_btn.setText(f"Gen: {', '.join(genres)[:30]} ▾" if genres else "Gen: toate ▾")
        role, sugg = self.role_f.currentData() or ("", False)
        fams = [a.text().split("  (")[0] for a in self.fam_menu.actions() if a.isCheckable() and a.isChecked()]
        fams = ["" if f == "(nerecunoscut)" else f for f in fams]
        self.fam_btn.setText(f"Familie: {', '.join(f or '?' for f in fams)[:30]} ▾" if fams else "Familie: toate ▾")
        self.table.proxy.set_filters(self.search.text(), genres, self.bpm_lo.value(), self.bpm_hi.value(),
                                     self.key.currentData() or "", self.harm.isChecked(), role, sugg, fams)
        shown = self.table.proxy.rowCount()
        total = len(self.table.model_.items)
        dur = sum(it.track.duration for it in self.table.visible())
        self.count.setText(f"{shown} din {total} piese · {fmt_duration(dur)}")

    def clear_filters(self):
        self.search.clear()
        self.bpm_lo.setValue(0)
        self.bpm_hi.setValue(0)
        self.key.setCurrentIndex(0)
        self.harm.setChecked(False)
        self.role_f.setCurrentIndex(0)
        self._clear_families()
        self._clear_genres()

    # ===== player =====
    def _table_clicked(self, ix):
        if COLS[ix.column()][1] == "play":
            self.play(self.table.item_at(ix))

    def play(self, it: ldb.LibItem):
        if it.missing or it.offline:
            QMessageBox.information(self, "Player", "Fișierul nu e disponibil (lipsă sau pe un disc deconectat).")
            return
        a = an.load_cached(it.path, analysis_dir())
        t = it.track
        info = " · ".join(x for x in (t.genre, t.comment, f"{it.bpm:g} BPM" if it.bpm else "",
                                      it.camelot, t.year, t.label) if x)
        self.player.load(it.path, f"{t.artist} - {t.display_title}", info, t.duration, a)
        if a is None:
            self.worker.add([it.path], front=True)
        self.table.model_.playing = it.path
        self.table.viewport().update()

    def _play_next(self, step):
        vis = self.table.visible()
        if not vis:
            return
        cur = self.player.path
        idx = next((i for i, it in enumerate(vis) if it.path == cur), -1)
        nxt = idx + step
        if 0 <= nxt < len(vis):
            self.play(vis[nxt])
            self.table.selectRow(nxt)

    # ===== analiză =====
    def analyze_missing(self, silent=False):
        todo = [it.path for it in self.items if (not it.bpm or not it.key or not it.energy) and not it.missing]
        todo = [p for p in todo if an.load_cached(p, analysis_dir()) is None]
        if not todo:
            if not silent:
                QMessageBox.information(self, "Analiză", "Toate piesele au deja BPM și tonalitate.")
            return
        lo = int(settings().value("analysis/bpm_lo", 85) or 85)
        self.worker.bpm_range = (lo, lo * 2 + 5)
        self.worker.add(todo)

    def analyze(self, items):
        self.worker.add([it.path for it in items if not it.missing], front=True)

    def _analysis_status(self, left):
        if left:
            self.status.setText(f"Analizez BPM / tonalitate / waveform… mai sunt {left} piese")
        elif self.status.text().startswith("Analizez"):
            self.status.setText("Analiza s-a terminat.")

    def _analysis_done(self, path, a):
        e = self.db.extra(path)
        if not a.error:
            e.an_bpm, e.an_key, e.an_drops, e.an_energy = a.bpm, a.key, a.drops, a.energy
        it = self.by_path.get(djc.norm_path(path))
        if it is not None:
            new = ldb.build_items([it.track], self.db, self.collections, source_priority(),
                                  getattr(self, "history", {}))[0]
            for f in ("bpm", "key", "bpm_src", "key_src", "drops", "energy", "energy_src", "role", "role_suggested"):
                setattr(it, f, getattr(new, f))
            self.table.model_.refresh_row(path)
        if path == self.player.path:
            self.player.set_analysis(a)
        if not self.worker.queue:
            self.db.save()

    # ===== meniul pieselor =====
    def _table_menu(self, pos):
        sel = self.table.selected()
        if not sel:
            return
        m = QMenu(self)
        m.addAction("▶ Redă", lambda: self.play(sel[0]))
        add = m.addMenu("Adaugă în playlist")
        paths = [i.path for i in sel]
        sugg, create = self._suggest_playlists(sel)
        if sugg or create:
            add.addAction("Potrivite (familie + BPM):").setEnabled(False)
            for p in sugg:
                add.addAction(f"★ {p.name}", lambda pid=p.id: self.add_to_playlist(pid, paths))
            if create:
                add.addAction(f"＋ Creează „{create}”", lambda name=create: self.new_playlist(paths, name))
            add.addSeparator()
            add.addAction("Toate playlisturile:").setEnabled(False)
        for p in self.db.playlists:
            if p.kind == "manual":
                add.addAction(p.name, lambda pid=p.id: self.add_to_playlist(pid, paths))
        add.addSeparator()
        add.addAction("Playlist nou…", lambda: self.new_playlist(paths))
        if self.view[0] == "pl":
            m.addAction("Scoate din playlist", lambda: self.remove_from_playlist(sel))
            m.addAction("Mută mai sus", lambda: self.move_in_playlist(sel, -1))
            m.addAction("Mută mai jos", lambda: self.move_in_playlist(sel, 1))
        m.addAction("🎚 Piese care se mixează cu asta", lambda: self.show_mixable(sel[0]))
        rm = m.addMenu("Rol în set")
        for i, r in enumerate(ldb.ROLES):
            rm.addAction(f"{r}   ({i + 1})", lambda r=r: self.set_role(sel, r))
        rm.addSeparator()
        rm.addAction("Acceptă sugestiile", lambda: self.accept_suggestions(sel))
        rm.addAction("Șterge rolul   (0)", lambda: self.set_role(sel, ""))
        m.addAction("Scrie rolul și energia în Comentariu", lambda: self.write_role_energy(sel))
        m.addSeparator()
        m.addAction(f"Editează {len(sel)} piese…", lambda: self.bulk_edit(sel))
        r = m.addMenu("Rating")
        for n in range(6):
            r.addAction("★" * n if n else "fără", lambda n=n: self.set_rating(sel, n))
        m.addAction("Taguri online (gen, an, label, copertă)…", lambda: self.tag_finder(sel))
        m.addAction("Analizează (BPM, tonalitate, waveform)", lambda: self.analyze(sel))
        m.addAction("Scrie BPM-ul și tonalitatea în fișiere", lambda: self.write_bpm_key(sel))
        m.addSeparator()
        exp = m.addMenu("Exportă selecția")
        exp.addAction("Playlist M3U…", lambda: self.export_m3u("Selecție", sel))
        exp.addAction("CSV…", lambda: self.export_csv(sel))
        m.addAction("Trimite la Conversie", lambda: self.state.send_to_convert.emit([i.path for i in sel]))
        m.addAction("Caută în MixesDB (browser)", lambda: self.mixesdb(sel[0]))
        m.addAction("Arată în folder", lambda: reveal_in_folder(sel[0].path))
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ===== rol în set / energie =====
    def _role_key(self, role):
        sel = self.table.selected()
        if not sel:
            return
        if role == "__play__":
            if self.player.path == sel[0].path:
                self.player.toggle()
            else:
                self.play(sel[0])
            return
        self.set_role(sel, role)

    def set_role(self, items, role, write=True):
        for it in items:
            self.db.extra(it.path).role = role
            it.role = role
            if not role:
                it.role_suggested = ldb.suggest_role(it.energy, it.bpm, it.plays, it.avg_pos)
            self.table.model_.refresh_row(it.path)
        self.db.save()
        if write:
            self._write_comments(items, quiet=True)

    def accept_suggestions(self, items):
        todo = [it for it in items if not it.role and it.role_suggested]
        for it in todo:
            self.db.extra(it.path).role = it.role_suggested
            it.role = it.role_suggested
            self.table.model_.refresh_row(it.path)
        self.db.save()
        self._write_comments(todo, quiet=True)

    def write_role_energy(self, items):
        self._write_comments(items, quiet=False)

    def _write_comments(self, items, quiet):
        jobs = []
        for it in items:
            if it.missing or it.offline:
                continue
            style, old_role, old_e = ldb.parse_comment(it.track.comment)
            new = ldb.compose_comment(style, it.role, it.energy)
            if new != it.track.comment:
                jobs.append((it.path, {"comment": new}, None))
                it.track.comment = new
        if not jobs:
            return
        if not quiet and QMessageBox.question(
                self, "Comentariu", f"Scriu rolul și energia în Comentariul a {len(jobs)} fișiere?\n"
                "(ex. „Deep House | Peak | E7” — se vede în Traktor și Rekordbox)") != QMessageBox.Yes:
            return
        self._comment_jobs = getattr(self, "_comment_jobs", []) + jobs
        if not getattr(self, "_comment_timer", None):
            self._comment_timer = QTimer(self)
            self._comment_timer.setSingleShot(True)
            self._comment_timer.timeout.connect(self._flush_comments)
        self._comment_timer.start(1500)      # grupăm apăsările rapide de taste

    def _flush_comments(self):
        jobs, self._comment_jobs = getattr(self, "_comment_jobs", []), []
        if not jobs:
            return
        from ..core import tags as tagmod

        def work(progress, cancelled):
            errs = []
            for path, f, _ in jobs:
                try:
                    cur = tagmod.read_tags(path).fields
                    tagmod.write_tags(path, {**cur, **f})
                except Exception as e:
                    errs.append(f"{os.path.basename(path)}: {e}")
            return errs

        from .jobs import Job
        t = Job(f"Scriu rolul / energia ({len(jobs)})", work)
        t.succeeded.connect(lambda errs: self.status.setText(
            f"Am scris rolul în {len(jobs) - len(errs)} fișiere." + (f" {len(errs)} erori (fișiere deschise în alt program?)." if errs else "")))
        self._comment_task = t
        t.start()

    def mixesdb(self, it):
        import urllib.parse
        import webbrowser
        q = urllib.parse.quote_plus(f"{it.track.artist} {it.track.display_title}")
        webbrowser.open(f"https://www.mixesdb.com/w/index.php?search={q}")

    def _suggest_playlists(self, sel):
        """Playlisturile tale care se potrivesc piesei (aceeași familie, BPM în ±5), plus unul nou de creat."""
        base = next((i for i in sel if i.family or i.bpm), None)
        if not base:
            return [], ""
        out = []
        for p in self.db.playlists:
            if p.kind != "manual" or not p.tracks:
                continue
            fam, bpm = ldb.playlist_profile(self._resolve(p.tracks))
            fam_ok = not base.family or not fam or fam == base.family
            bpm_ok = not base.bpm or not bpm or ldb._bpm_in(base.bpm, bpm - 5, bpm + 5)
            if fam_ok and bpm_ok and (fam == base.family or (bpm and base.bpm)):
                out.append(p)
        create = ""
        if base.bpm:
            lo, hi = ldb.bpm_bucket(base.bpm)
            create = f"{base.family + ' ' if base.family else ''}{lo}–{hi}"
            if any(p.name.lower() == create.lower() for p in self.db.playlists):
                create = ""
        return out[:8], create

    def show_mixable(self, it):
        self.mix_base = it
        self.rebuild_tree()
        self.show_view(("mix",))

    def set_rating(self, items, n):
        for it in items:
            self.db.extra(it.path).rating = n
            it.rating = n
            self.table.model_.refresh_row(it.path)
        self.db.save()

    def bulk_edit(self, items):
        dlg = BulkEditDialog(items, self)
        if dlg.exec() != QDialog.Accepted:
            return
        for it in items:
            e = self.db.extra(it.path)
            if dlg.result_rating is not None:
                e.rating = dlg.result_rating
            if dlg.result_bpm is not None:
                e.user_bpm = dlg.result_bpm
            if dlg.result_key is not None:
                e.user_key = dlg.result_key
        self.db.save()
        if dlg.result_fields:
            jobs = []
            for it in items:
                if it.missing:
                    continue
                f = dict(dlg.result_fields)
                if "comment" in f:      # stilul nou, cu rolul și energia păstrate
                    _, role, energy = ldb.parse_comment(it.track.comment)
                    f["comment"] = ldb.compose_comment(f["comment"], it.role or role, it.energy or energy)
                jobs.append((it.path, f, None))
            write_tags_in_background(self, jobs, self.reload, f"Scriu tagurile în {len(jobs)} fișiere")
        else:
            self._rebuild_items()
            self.show_view(self.view)

    def write_bpm_key(self, items):
        jobs = []
        for it in items:
            f = {}
            if it.bpm:
                f["bpm"] = str(int(round(it.bpm)))
            if it.key:
                f["key"] = it.key
            if f and not it.missing:
                jobs.append((it.path, f, None))
        if jobs and QMessageBox.question(self, "BPM / tonalitate", f"Scriu BPM-ul și tonalitatea în {len(jobs)} "
                                         "fișiere? (Traktor și Rekordbox le pot citi la reimport.)") == QMessageBox.Yes:
            write_tags_in_background(self, jobs, self.reload)

    def tag_finder(self, items):
        tracks = [it.track for it in items if not it.missing]
        if not tracks:
            return
        from .settings_tab import spotify_client
        dlg = TagFinderDialog(tracks, spotify_client(), self)
        dlg.on_applied = self.reload
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()          # nu blochează: poți lucra în alte taburi cât caută

    def genre_cleanup(self):
        dlg = GenreCleanupDialog(self.items, self)
        dlg.on_applied = self.reload
        dlg.exec()

    def artist_cleanup(self):
        dlg = ArtistCleanupDialog(self.items, self)
        if not dlg.groups:
            QMessageBox.information(self, "Artiști", "Nu am găsit artiști scriși în mai multe feluri.")
            return
        dlg.on_applied = self.reload
        dlg.exec()

    def find_moved(self):
        missing = [m for m in self._missing_items() if m.missing]
        if not missing:
            QMessageBox.information(self, "Piese mutate", "Nu lipsește nicio piesă din playlisturi.")
            return
        index: dict[str, list] = {}
        for it in self.items:
            index.setdefault(os.path.basename(it.path).lower(), []).append(it.track)
        fixed = 0
        for m in missing:
            new = ldb.find_moved(m.path, index)
            if new:
                self.db.relink(m.path, new)
                fixed += 1
        self.db.save()
        self.rebuild_tree()
        self.show_view(self.view)
        QMessageBox.information(self, "Piese mutate", f"Am regăsit {fixed} din {len(missing)} piese lipsă. "
                                + ("Restul nu mai sunt în folderele bibliotecii." if fixed < len(missing) else ""))

    # ===== playlisturi =====
    def new_playlist(self, paths=None, name=None):
        if not name:
            name, ok = QInputDialog.getText(self, "Playlist nou", "Nume:")
            if not ok or not name.strip():
                return
        p = self.db.new_playlist(name.strip())
        if paths:
            p.tracks = list(dict.fromkeys(paths))
        self.db.save()
        self.rebuild_tree()
        self.show_view(("pl", p.id))
        self._auto_export()

    def add_to_playlist(self, pid, paths):
        p = self.db.get_playlist(pid)
        if not p:
            return
        have = {djc.norm_path(x) for x in p.tracks}
        for x in paths:
            if djc.norm_path(x) not in have:
                p.tracks.append(x)
                have.add(djc.norm_path(x))
        self.db.save()
        self.rebuild_tree()
        if self.view == ("pl", pid):
            self.show_view(self.view)
        self.status.setText(f"Adăugat în „{p.name}”.")
        self._auto_export()

    def _drop_to_playlist(self, pid, paths):
        self.add_to_playlist(pid, paths)

    def remove_from_playlist(self, items):
        p = self.db.get_playlist(self.view[1])
        if not p:
            return
        rm = {djc.norm_path(i.path) for i in items}
        p.tracks = [t for t in p.tracks if djc.norm_path(t) not in rm]
        self.db.save()
        self.rebuild_tree()
        self.show_view(self.view)
        self._auto_export()

    def move_in_playlist(self, items, step):
        p = self.db.get_playlist(self.view[1])
        if not p:
            return
        keys = [djc.norm_path(t) for t in p.tracks]
        idxs = sorted(keys.index(djc.norm_path(i.path)) for i in items if djc.norm_path(i.path) in keys)
        if not idxs or (step < 0 and idxs[0] == 0) or (step > 0 and idxs[-1] == len(keys) - 1):
            return
        for i in (idxs if step < 0 else reversed(idxs)):
            p.tracks[i], p.tracks[i + step] = p.tracks[i + step], p.tracks[i]
        self.db.save()
        self.show_view(self.view)
        self._auto_export()

    def edit_smart(self, pid):
        p = self.db.get_playlist(pid) if pid else None
        dlg = SmartPlaylistDialog(p.name if p else "", p.rules if p else None,
                                  lambda r: len(ldb.smart_filter(self.items, r)), self)
        if dlg.exec() != QDialog.Accepted:
            return
        if not p:
            p = self.db.new_playlist(dlg.name.text().strip(), "smart")
        p.name = dlg.name.text().strip()
        p.rules = dlg.rules()
        self.db.save()
        self.rebuild_tree()
        self.show_view(("smart", p.id))
        self._auto_export()

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        d = item.data(0, Qt.UserRole) if item else None
        if not d or d[0] not in ("pl", "smart", "ext"):
            return
        m = QMenu(self)
        title, items = self.items_for(tuple(d))
        name = title.lstrip("⚡ ").split(": ", 1)[-1]
        if d[0] in ("pl", "smart"):
            p = self.db.get_playlist(d[1])
            m.addAction("Redenumește…", lambda: self._rename_pl(p))
            if d[0] == "smart":
                m.addAction("Editează regulile…", lambda: self.edit_smart(p.id))
                m.addAction("Transformă în playlist obișnuit", lambda: self._freeze(p, items))
            m.addAction("Duplică", lambda: self._duplicate(p, items))
            m.addAction("Șterge", lambda: self._delete_pl(p))
        else:
            m.addAction("Copiază ca playlist propriu", lambda: self._copy_ext(name, items))
        n_missing = sum(1 for i in items if i.missing or i.offline)
        if n_missing:
            m.addAction(f"Relocă piesele lipsă ({n_missing})… — alegi folderul unde sunt acum",
                        lambda: self.relocate([i.path for i in items if i.missing or i.offline]))
        m.addSeparator()
        m.addAction("Exportă M3U (pentru orice player / stick)…", lambda: self.export_m3u(name, items))
        m.addAction("Exportă pentru Traktor (NML)…", lambda: self.export_traktor(name, items))
        m.addAction("Exportă CSV…", lambda: self.export_csv(items))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def relocate(self, paths):
        folder = QFileDialog.getExistingDirectory(self, "În ce folder sunt acum piesele? (se caută și în subfoldere)")
        if not folder:
            return
        from .jobs import Job
        job = Job("Relocare piese", lambda progress, cancelled: ldb.relocate_by_name(paths, folder), writes=False)

        def done(found):
            for old, new in found.items():
                self.db.relocations[djc.norm_path(old)] = new
                for p in self.db.playlists:            # în playlisturile tale schimbăm direct calea
                    p.tracks = [new if djc.norm_path(t) == djc.norm_path(old) else t for t in p.tracks]
            self.db.save()
            self._annotate(self.items)
            self.rebuild_tree()
            self.show_view(self.view)
            QMessageBox.information(self, "Relocare", f"Am găsit {len(found)} din {len(paths)} piese lipsă."
                                    + ("" if len(found) == len(paths) else
                                       "\nRestul nu sunt în folderul ales (sau au alt nume de fișier)."))
            if not any(djc.norm_path(f) == djc.norm_path(folder) or
                       djc.norm_path(found_p).startswith(djc.norm_path(f)) for f in self.library_folders()
                       for found_p in list(found.values())[:1]):
                if found and QMessageBox.question(self, "Relocare", "Folderul nu e în Bibliotecă. Îl adaug, "
                                                  "ca piesele să apară cu toate datele?") == QMessageBox.Yes:
                    self.state.set_folders(self.state.folders + [os.path.normpath(folder)])
                    self.reload()
        job.succeeded.connect(done)
        job.start()

    def _rename_pl(self, p):
        name, ok = QInputDialog.getText(self, "Redenumește", "Nume:", text=p.name)
        if ok and name.strip():
            p.name = name.strip()
            self.db.save()
            self.rebuild_tree()
            self._auto_export()

    def _delete_pl(self, p):
        if QMessageBox.question(self, "Șterge", f"Șterg playlistul „{p.name}”? (Piesele rămân pe disc.)") == QMessageBox.Yes:
            self.db.delete_playlist(p.id)
            self.db.save()
            self.view = ("all",)
            self.rebuild_tree()
            self.show_view(self.view)
            self._auto_export()

    def _duplicate(self, p, items):
        q = self.db.new_playlist(p.name + " (copie)", p.kind)
        q.rules = json.loads(json.dumps(p.rules))
        q.tracks = list(p.tracks) if p.kind == "manual" else []
        self.db.save()
        self.rebuild_tree()

    def _freeze(self, p, items):
        q = self.db.new_playlist(p.name)
        q.tracks = [i.path for i in items]
        self.db.save()
        self.rebuild_tree()
        self.show_view(("pl", q.id))

    def _copy_ext(self, name, items):
        q = self.db.new_playlist(name)
        q.tracks = [i.path for i in items]
        self.db.save()
        self.rebuild_tree()
        self.show_view(("pl", q.id))

    # ===== export =====
    def _current_for_export(self):
        title, _ = self.items_for(self.view)
        return title.lstrip("⚡ ").split(": ", 1)[-1], [i for i in self.table.visible() if not i.missing]

    def copy_to_folder(self, name, items):
        d = QFileDialog.getExistingDirectory(self, "Alege folderul sau stick-ul")
        if not d:
            return
        dest_dir = os.path.join(d, lib.safe_name(name) or "Playlist")
        numbered = QMessageBox.question(self, "Numerotare", "Pun numere în fața fișierelor, ca ordinea să se "
                                        "păstreze pe playere (ex. „01 Artist - Titlu”)?") == QMessageBox.Yes
        import shutil
        from .jobs import Job

        def work(progress, cancelled):
            os.makedirs(dest_dir, exist_ok=True)
            copied, errors = [], []
            w = max(2, len(str(len(items))))
            for i, it in enumerate(items, 1):
                if cancelled():
                    break
                fname = os.path.basename(it.path)
                if numbered:
                    fname = f"{i:0{w}} {fname}"
                dst = lib.unique_path(os.path.join(dest_dir, fname))
                try:
                    shutil.copy2(it.path, dst)
                    copied.append(lib.Track(**{**it.track.__dict__, "path": dst}))
                except OSError as e:
                    errors.append(f"{fname}: {e.strerror or e}")
                progress(i, len(items), fname)
            write_m3u(os.path.join(dest_dir, (lib.safe_name(name) or "Playlist") + ".m3u8"), copied)
            return len(copied), errors

        job = Job(f"Copiez „{name}” pe stick ({len(items)})", work, writes=False)
        job.succeeded.connect(lambda r: QMessageBox.information(
            self, "Copiere", f"Am copiat {r[0]} piese în:\n{dest_dir}" + (f"\n{len(r[1])} erori." if r[1] else "")))
        job.start()

    def export_m3u(self, name, items):
        path, _ = QFileDialog.getSaveFileName(self, "Exportă M3U", lib.safe_name(name) + ".m3u8", "M3U8 (*.m3u8)")
        if path:
            write_m3u(path, [i.track for i in items if not i.missing])

    def export_csv(self, items):
        path, _ = QFileDialog.getSaveFileName(self, "Exportă CSV", "piese.csv", "CSV (*.csv)")
        if path:
            ldb.export_csv(path, items)

    def traktor_dir(self) -> str:
        d = settings().value("export/traktor_dir", "") or os.path.join(documents_dirs()[0] if documents_dirs()
                                                                         else os.path.expanduser("~"),
                                                                         "Music Manager", "Traktor")
        os.makedirs(d, exist_ok=True)
        return d

    def rekordbox_xml_path(self) -> str:
        p = settings().value("export/rekordbox_xml", "") or os.path.join(
            documents_dirs()[0] if documents_dirs() else os.path.expanduser("~"), "Music Manager", "rekordbox.xml")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def export_traktor(self, name, items):
        path = os.path.join(self.traktor_dir(), lib.safe_name(name) + ".nml")
        ldb.export_traktor_nml(path, name, [i for i in items if not i.missing])
        QMessageBox.information(self, "Traktor", f"Am scris:\n{path}\n\nÎn Traktor: click dreapta pe „Playlists” → "
                                "Import Playlist → alege fișierul. Cue-urile și grid-urile tale rămân neatinse.")

    def _all_playlists(self):
        out = []
        for p in self.db.playlists:
            _, items = self.items_for(("smart" if p.kind == "smart" else "pl", p.id))
            out.append((p.name, [i for i in items if not i.missing]))
        return out

    def export_all_rekordbox(self, silent=False):
        path = self.rekordbox_xml_path()
        ldb.export_rekordbox_xml(path, self._all_playlists())
        if not silent:
            QMessageBox.information(self, "Rekordbox", f"Am scris:\n{path}\n\nÎn Rekordbox (o singură dată): "
                                    "Preferences → Advanced → rekordbox xml → Imported Library → alege fișierul. "
                                    "Apoi playlisturile apar în stânga la „rekordbox xml”; apasă refresh după "
                                    "fiecare export.")

    def export_all_traktor(self):
        n = 0
        for name, items in self._all_playlists():
            ldb.export_traktor_nml(os.path.join(self.traktor_dir(), lib.safe_name(name) + ".nml"), name, items)
            n += 1
        QMessageBox.information(self, "Traktor", f"Am scris {n} playlisturi în:\n{self.traktor_dir()}\n\n"
                                "În Traktor: click dreapta pe „Playlists” → Import Playlist.")
        open_file(self.traktor_dir())

    def _auto_export(self):
        if settings().value("export/auto_rekordbox", False) in (True, "true") and self.db.playlists:
            try:
                self.export_all_rekordbox(silent=True)
            except OSError:
                pass

    def shutdown(self):
        self.worker.stop_flag = True
        self.worker.wait(3000)
        self.player.stop()
        self.db.save()
