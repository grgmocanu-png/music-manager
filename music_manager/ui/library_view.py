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
    QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import analysis as an
from ..core import djcollections as djc
from ..core import library as lib
from ..core import libdb as ldb
from ..core.playlist import CAMELOT, compatible_keys, to_camelot, write_m3u
from .common import (
    AppState, Task, data_dir, fmt_duration, open_file, reveal_in_folder, reveal_label, settings,
)
from .dialogs import (
    ArtistCleanupDialog, BulkEditDialog, GenreCleanupDialog, SmartPlaylistDialog, TagFinderDialog,
    families, write_tags_in_background,
)
from .player import PlayerPanel

COLS = [("", "play"), ("#", "num"), ("Artist", "artist"), ("Title", "title"), ("Family", "family"), ("Genre", "genre"),
        ("Comments", "style"), ("Role", "role"), ("Energy", "energy"), ("BPM", "bpm"), ("Key", "key"), ("Year", "year"),
        ("Rating", "rating"), ("Duration", "duration"), ("Label", "label"), ("Date Added", "added"),
        ("Quality", "quality"), ("BPM / Key Source", "src"), ("Playlists", "playlists"), ("Folder", "folder"),
        ("Date Analyzed", "analyzed"), ("Cover", "cover"), ("Mood", "mood"), ("Grouping", "grouping"),
        ("Sugestie", "suggest")]
EDITABLE = {"artist", "title", "family", "genre", "style", "role", "energy", "bpm", "key", "year", "label",
            "mood", "grouping"}
DEFAULT_HIDDEN = {"src", "folder", "quality"}
VALUE_COLS = {   # coloanele după care se pot sugera playlisturi (valoarea unei piese)
    "artist": lambda it: it.track.artist.split(",")[0].strip(),
    "genre": lambda it: it.track.genre.strip(),
    "style": lambda it: (ldb.parse_comment(it.track.comment)[0] or it.track.comment).strip(),
    "energy": lambda it: f"Energy {it.energy}" if it.energy else "",
    "key": lambda it: it.camelot or it.key,
    "year": lambda it: it.track.year.strip(),
    "label": lambda it: it.track.label.strip(),
    "mood": lambda it: it.track.mood.strip(),
    "grouping": lambda it: it.track.grouping.strip(),
    "quality": lambda it: it.track.ext.upper(),
    "folder": lambda it: os.path.basename(os.path.dirname(it.path)),
    "cover": lambda it: "Cu copertă" if it.track.has_cover else "Fără copertă",
    "added": lambda it: (it.date_added or "")[:7],
    "rating": lambda it: f"{it.rating}★" if it.rating else "",
    "duration": lambda it: "",
}
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


def key_color(cam: str) -> QColor:
    """Culoarea cheii pe roata Camelot (ca în Traktor / Rekordbox), în tonuri stinse."""
    try:
        n = int(cam[:-1])
    except ValueError:
        return QColor("#9aa0a8")
    c = QColor()
    c.setHsl(int(((n - 1) * 30 + 200) % 360), 110, 165 if cam.endswith("B") else 140)
    return c


_FAM_COLORS = ["#a9bde6", "#9fd4c9", "#e8c39a", "#e5aca3", "#bfb5ee", "#b5d6a8", "#e2b0d6", "#d8d4a0",
               "#9fc6e0", "#e0b98f"]


def family_color(fam: str) -> QColor:
    from .dialogs import families
    fams = families()
    i = fams.index(fam) if fam in fams else sum(map(ord, fam))
    return QColor(_FAM_COLORS[i % len(_FAM_COLORS)])


def bpm_ranges() -> dict:
    try:
        raw = json.loads(settings().value("genres/bpm_ranges", "") or "{}")
        out = {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
    except (ValueError, TypeError, IndexError):
        out = {}
    return out or dict(ldb.DEFAULT_BPM_RANGES)


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
        self.on_edit = None           # funcție (piesă, coloană, valoare nouă) -> bool

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
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled
        if index.isValid() and COLS[index.column()][1] in EDITABLE:
            it = self.items[index.row()]
            if not (it.missing or it.offline):
                f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or self.on_edit is None:
            return False
        it = self.items[index.row()]
        col = COLS[index.column()][1]
        old = self.data(index, Qt.EditRole)
        value = str(value if value is not None else "").strip()
        if value == (old or ""):
            return False
        ok = self.on_edit(it, col, value)
        if ok:
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLS) - 1))
        return bool(ok)

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
            if col == "suggest":
                return f"✓ {it.suggest}" + (f" · {it.suggest_genre}" if it.suggest_genre else "") if it.suggest else ""
            if col == "playlists":
                return ", ".join(it.playlists)
            if col == "genre":
                return t.genre
            if col == "style":
                return t.comment
            if col == "analyzed":
                return "eroare" if it.an_error else (it.analyzed or "")[:10]
            if col == "cover":
                return "✓" if t.has_cover else ""
            if col == "role":
                return it.role or (f"{it.role_suggested}?" if it.role_suggested else "")
            if col == "energy":
                return it.energy or ""
            if col == "bpm":
                v = (f"{it.bpm:.1f}".rstrip("0").rstrip(".")) if it.bpm else ""
                return f"{v} ?" if it.bpm_suggest else v
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
            if col == "mood":
                return t.mood
            if col == "grouping":
                return t.grouping
            if col == "added":
                return (it.date_added or "")[:10]
            if col == "quality":
                return t.quality_label
            if col == "src":
                return " / ".join(x for x in (it.bpm_src, it.key_src) if x)
            if col == "folder":
                return os.path.dirname(it.path)
        if role == Qt.EditRole:
            return {"artist": t.artist, "title": t.title, "family": it.family, "genre": t.genre,
                    "style": t.comment, "role": it.role, "energy": str(it.energy or ""),
                    "bpm": (f"{it.bpm:.2f}".rstrip("0").rstrip(".") if it.bpm else ""), "key": it.key,
                    "year": t.year, "label": t.label, "mood": t.mood, "grouping": t.grouping}.get(col, "")
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
            if col == "cover":
                return 1 if t.has_cover else 0
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
            if col == "bpm" and it.bpm_suggest:
                return QBrush(QColor("#d68a1f"))
            if col == "key" and it.camelot:
                return QBrush(key_color(it.camelot))
            if col == "family" and it.family:
                if it.family_src != "manual":
                    return QBrush(QColor("#8f959d"))        # dedusă din gen, nu pusă de tine
                return QBrush(family_color(it.family))
            if col == "suggest" and it.suggest:
                return QBrush(QColor("#5fb3a6"))
        if role == Qt.ToolTipRole:
            if col == "family":
                if it.family_src == "manual":
                    return "Familie pusă de tine."
                return ("Dedusă din genul din tag (gri). Alege din ▾ sau click dreapta → Family ca s-o fixezi."
                        if it.family else "Fără familie. Alege din ▾ sau click dreapta → Family.")
            if col == "suggest" and it.suggest:
                return "Găsită de Taguri online. Click pe ✓ ca s-o accepți ca familie (nu se scrie nimic singur)."
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
            if col == "bpm" and it.bpm_suggest:
                return (f"BPM de verificat: pentru {it.family} pare să fie {it.bpm_suggest:g}.\n"
                        "Click dreapta → ×2 / ½ ca să-l corectezi (se scrie și în fișier).")
            if col == "analyzed":
                if it.an_error:
                    return f"Analiza a eșuat ({it.analyzed[:10]}): {it.an_error}\nClick dreapta → Analizează ca să încerci din nou."
                return f"Analizată pe {it.analyzed.replace('T', ' ')}" if it.analyzed else "Neanalizată"
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


COMBO_COLS = ("role", "family", "genre", "key")


class EditDelegate(QStyledItemDelegate):
    """Role, Family, Genre și Key se aleg dintr-o listă (Family / Genre se pot și scrie); restul se scriu direct.
    Săgeata ▾ apare la hover; click pe ea deschide lista direct."""

    def createEditor(self, parent, option, index):
        from .combo_cells import make_combo
        col = COLS[index.column()][1]
        if col in COMBO_COLS:
            if col == "role":
                items = [""] + list(ldb.ROLES)
            elif col == "family":
                items = [""] + all_families()
            elif col == "genre":
                lv = _LIVE.get("library")
                items = [""] + [g for g, _n in ldb.genre_counts(lv.items if lv else []) if g]
            else:
                items = [""] + [f"{n}{l}" for n in range(1, 13) for l in "AB"]
            return make_combo(parent, items, editable=col in ("family", "genre"), delegate=self)
        return super().createEditor(parent, option, index)

    def paint(self, p, opt, index):
        QStyledItemDelegate.paint(self, p, opt, index)
        if COLS[index.column()][1] in COMBO_COLS:
            from .combo_cells import paint_arrow
            paint_arrow(p, opt, index)

    def editorEvent(self, event, model, option, index):
        from .combo_cells import arrow_click
        if COLS[index.column()][1] in COMBO_COLS and arrow_click(self, event, option, index):
            return True
        return super().editorEvent(event, model, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            v = index.data(Qt.EditRole) or ""
            if v and editor.findText(v) < 0:
                editor.insertItem(1, v)          # valoarea de acum rămâne aleasă (nu se pierde la ieșire)
            editor.setCurrentText(v)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.EditRole)
            return
        super().setModelData(editor, model, index)


def all_families(extra_items=()) -> list[str]:
    """Familiile pentru liste: cele din Setări + cele care există deja pe piese."""
    from .dialogs import families
    out = list(families())
    seen = {f.lower() for f in out}
    for f in extra_items:
        if f and f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    try:
        lv = _LIVE.get("library")
        if lv is not None:
            for it in lv.items:
                f = it.family
                if f and f.lower() not in seen:
                    seen.add(f.lower())
                    out.append(f)
    except Exception:
        pass
    return out


_LIVE: dict = {}


class LibPill(EditDelegate):
    """Family ca etichetă colorată, Key cu punct colorat — și tot editabile."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind

    from .delegates import PillDelegate as _P

    def paint(self, p, opt, index):
        self._P.paint(self, p, opt, index)
        from .combo_cells import paint_arrow
        paint_arrow(p, opt, index)


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
    role_key = Signal(str)          # Space = play/stop; „rating:N” = tastele 1–5 / 0
    player_key = None               # funcție (eveniment) -> bool: tastele playerului

    def keyPressEvent(self, e):
        k = e.text()
        if self.state() == QAbstractItemView.EditingState:
            return super().keyPressEvent(e)
        if k and k in "012345" and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            self.role_key.emit(f"rating:{k}")
            return
        if e.key() == Qt.Key_Space:
            self.role_key.emit("__play__")
            return
        if e.key() == Qt.Key_Delete:
            self.role_key.emit("__delete__")
            return
        if self.player_key and self.player_key(e):
            return
        self.kbd_nav = True          # autoplay doar când schimbi piesa din taste (↑/↓)
        try:
            super().keyPressEvent(e)
        finally:
            self.kbd_nav = False

    kbd_nav = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = ItemModel(self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setItemDelegate(EditDelegate(self))
        keys = [k for _, k in COLS]
        self._pill_f = LibPill("pill", self)
        self._pill_k = LibPill("dot", self)
        self.setItemDelegateForColumn(keys.index("family"), self._pill_f)
        self.setItemDelegateForColumn(keys.index("key"), self._pill_k)
        self.setShowGrid(False)
        # editare: click pe o celulă a rândului deja selectat, sau F2
        # editarea pornește doar cu F2 sau din săgeata ▾ (click-ul doar selectează, dublu click = play)
        self.setEditTriggers(QAbstractItemView.EditKeyPressed)
        from .combo_cells import setup_view
        setup_view(self)
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
        widths = [28, 38, 170, 230, 95, 100, 160, 70, 55, 55, 85, 48, 60, 55, 110, 82, 120, 160, 120, 200, 95, 50]
        for i, w in enumerate(widths):
            self.setColumnWidth(i, w)
        restored = self._restore_columns()
        state = None if restored else settings().value("library/header3")
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
        self._save_header()

    def _save_header(self):
        """Coloanele (ordine, ascunse, lățimi) se țin minte după nume, nu după poziție,
        ca să rămână la fel și când apar coloane noi."""
        h = self.horizontalHeader()
        order = [COLS[h.logicalIndex(v)][1] for v in range(len(COLS))]
        data = {"order": order, "hidden": [k for i, (_, k) in enumerate(COLS) if self.isColumnHidden(i)],
                "widths": {k: self.columnWidth(i) for i, (_, k) in enumerate(COLS)}}
        settings().setValue("library/columns", json.dumps(data))

    def _restore_columns(self) -> bool:
        try:
            data = json.loads(settings().value("library/columns", "") or "")
        except (ValueError, TypeError):
            return False
        if not isinstance(data, dict):
            return False
        h = self.horizontalHeader()
        keys = [k for _, k in COLS]
        order = [k for k in data.get("order", []) if k in keys] + [k for k in keys if k not in data.get("order", [])]
        for vis, k in enumerate(order):
            h.moveSection(h.visualIndex(keys.index(k)), vis)
        hidden = set(data.get("hidden", []))
        for i, k in enumerate(keys):
            self.setColumnHidden(i, k in hidden)
            w = (data.get("widths") or {}).get(k)
            if w:
                self.setColumnWidth(i, int(w))
        return True

    def _header_menu(self, pos):
        """Click dreapta pe antet: alegi ce coloane vezi (ordinea o schimbi trăgând de antet)."""
        from .combo_cells import StayOpenMenu
        m = StayOpenMenu(self)
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
        rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
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
        self.force: set[str] = set()

    def add(self, paths, front=False, force=False):
        if force:
            self.force.update(paths)
        if front:
            self.queue = [p for p in self.queue if p not in paths]
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
            a = an.analyze(path, analysis_dir(), bpm_range=self.bpm_range, force=path in self.force)
            self.force.discard(path)
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
        _LIVE["library"] = self
        self.state = state
        self.db = ldb.LibraryDB(os.path.join(data_dir(), "biblioteca_db.json"))
        self.collections: list[djc.Collection] = []
        self.items: list[ldb.LibItem] = []
        self.by_path: dict[str, ldb.LibItem] = {}
        self.task: Task | None = None
        self.view = ("all",)
        self.mix_base: ldb.LibItem | None = None
        self._col_mtimes: dict[str, float] = {}

        state.files_moved.connect(self._files_moved)
        self.worker = AnalysisWorker(self)
        self.worker.result.connect(self._analysis_done)
        self.worker.status.connect(self._analysis_status)

        # --- stânga ---
        self.tree = SideTree()
        self.tree.setObjectName("sidebar")
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemExpanded.connect(self._expand_dir)
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
        tm.addAction("Analizează piesele selectate", lambda: self.analyze(self.table.selected()))
        tm.addAction("Analizează toate piesele neanalizate…", self.analyze_missing)
        tm.addAction("Taguri online (gen, an, label, copertă)…", lambda: self.tag_finder(self.table.visible()))
        tm.addSeparator()
        cm = tm.addMenu("Curăță categorii")
        for key, title in (("genre", "Genre"), ("family", "Family"), ("comment", "Comments"), ("artist", "Artist"),
                           ("label", "Label"), ("year", "Year"), ("album", "Album")):
            cm.addAction(f"{title}…", lambda k=key: self.category_cleanup(k))
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
        from .combo_cells import StayOpenMenu
        self.fam_menu = StayOpenMenu(self.fam_btn)
        self.fam_btn.setMenu(self.fam_menu)
        self.fam_btn.setStyleSheet("font-weight: bold;")
        self.genre_btn = QToolButton()
        self.genre_btn.setText("Gen: toate ▾")
        self.genre_btn.setPopupMode(QToolButton.InstantPopup)
        self.genre_menu = StayOpenMenu(self.genre_btn)
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
        self.title.setObjectName("pageTitle")
        self.count = QLabel()
        self.count.setStyleSheet("color: gray;")
        self.b_bpmkey = QPushButton("Scrie BPM & Key în fișiere")
        self.b_bpmkey.setToolTip("Unde BPM-ul / tonalitatea din Manager diferă de fișier sau de Traktor / Rekordbox")
        self.b_bpmkey.clicked.connect(self.bpm_key_review)
        self.b_delete = QPushButton("Șterge")
        self.b_delete.setObjectName("danger")
        self.b_delete.setToolTip("Trimite fișierele selectate în Coș (Delete). Într-un playlist, Delete doar le scoate.")
        self.b_delete.clicked.connect(lambda: self.delete_files(self.table.selected()))
        th = QHBoxLayout()
        th.addWidget(self.title)
        th.addStretch()
        th.addWidget(self.count)
        th.addSpacing(12)
        th.addWidget(self.b_bpmkey)
        th.addWidget(self.b_delete)

        self.table = TrackTable()
        self.table.doubleClicked.connect(lambda ix: self.play(self.table.item_at(ix)))
        self.table.model_.on_edit = self._edit_cell_multi
        self.table.clicked.connect(self._table_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.role_key.connect(self._role_key)

        self.player = PlayerPanel()
        self.table.player_key = self.player.handle_key
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)
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
        hint = QLabel("F2 editează celula · Space play · 1–5 rating · Delete șterge fișierul")
        hint.setObjectName("muted")
        srow = QHBoxLayout()
        srow.addWidget(self.status, 1)
        srow.addWidget(hint)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(self.player)
        lay.addLayout(srow)
        self._lay = lay

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
        changes = self._traktor_changes(cols)
        self._rebuild_items()
        self.state.set_tracks(tracks)
        if changes:
            QTimer.singleShot(400, lambda: self._show_traktor_changes(changes))
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
        if settings().value("library/auto_analyze2", False) in (True, "true"):
            self.analyze_missing(silent=True)
        self._auto_export()

    def _traktor_changes(self, cols):
        """Compară colecția Traktor cu cea de data trecută. Cea mai nouă schimbare câștigă:
        dacă ai schimbat BPM / Key / Rating în Traktor, valoarea pusă înainte în Manager e înlocuită."""
        snap_path = os.path.join(data_dir(), "traktor_snapshot.json")
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        new = ldb.traktor_snapshot(cols)
        if not new:
            return []
        changes = ldb.diff_snapshot(old, new)
        try:
            from ..core.safeio import write_json
            write_json(snap_path, new)
        except OSError:
            pass
        known = {djc.norm_path(t.path): t.path for t in self._tracks}
        # discul poate apărea altfel în Traktor (altă literă / numele volumului): potrivim și după folder + nume
        tails: dict = {}
        for k0, p0 in known.items():
            tails.setdefault((os.path.basename(os.path.dirname(k0)), os.path.basename(k0)), []).append(p0)
        out = []
        for k, field, a, b in changes:
            path = known.get(k)
            if not path:
                lst = tails.get((os.path.basename(os.path.dirname(k)), os.path.basename(k)))
                path = lst[0] if lst and len(lst) == 1 else None
            if not path:
                continue
            e = self.db.extra(path)
            if field == "bpm":
                e.user_bpm = 0.0
            elif field == "key":
                e.user_key = ""
            elif field == "rating":
                e.rating = 0
            out.append((path, field, a, b))
        if out:
            self.db.save()
        return out

    def _show_traktor_changes(self, changes):
        from .library_dialogs import TraktorChangesDialog
        dlg = TraktorChangesDialog(changes, self)
        dlg.on_write = lambda jobs: write_tags_in_background(self, jobs, None, "Scriu în fișiere schimbările din Traktor")
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    def _rebuild_items(self):
        # date analizate deja (din cache) -> în baza bibliotecii, ca să fie folosite imediat
        for t in self._tracks:
            e = self.db.extra(t.path)
            if not (e.an_bpm or e.an_key) or not e.an_energy or not e.an_date:
                a = an.load_cached(t.path, analysis_dir())
                if a is not None:
                    if not a.error:
                        e.an_bpm = e.an_bpm or a.bpm
                        e.an_key = e.an_key or a.key
                        e.an_drops = e.an_drops or a.drops
                        e.an_energy = e.an_energy or a.energy
                    e.an_error = a.error
                    e.an_date = e.an_date or an.cached_date(t.path, analysis_dir(), a)
        self.items = ldb.build_items(self._tracks, self.db, self.collections, source_priority(),
                                     getattr(self, "history", {}))
        self.by_path = {djc.norm_path(it.path): it for it in self.items}
        self._auto_relocate()
        self._annotate(self.items)
        self.db.save()
        self._fill_genre_menu()
        self._update_bpmkey_button()

    def _annotate(self, items):
        """Familia de gen și playlisturile în care apare fiecare piesă."""
        from .dialogs import genre_mapper, load_tag_results
        mapper = genre_mapper()
        ranges = bpm_ranges()
        sugg = load_tag_results()          # ce au găsit Taguri online (propuneri, nu se scriu singure)
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
            manual = self.db.extra(it.path).user_family if not (it.missing or it.offline) else ""
            it.family = manual or fam
            it.family_src = "manual" if manual else ("gen" if fam else "")
            s = sugg.get(djc.norm_path(it.path))
            if s and s.get("genre") and s["genre"] != it.family:
                it.suggest = s["genre"]
                it.suggest_genre = ", ".join((s.get("styles") or [])[:1])
            else:
                it.suggest, it.suggest_genre = "", ""
            it.bpm_suggest = ldb.bpm_suggestion(it.bpm, it.family, ranges) if it.bpm_src != "Manual" else 0.0
            it.playlists = member.get(djc.norm_path(it.path), [])

    def _reloc(self, path: str) -> str:
        k = djc.norm_path(path)
        new = self.db.relocations.get(k) or getattr(self, "auto_reloc", {}).get(k)
        return djc.norm_path(new) if new else k

    def _auto_relocate(self):
        """Piesele din playlisturile Traktor / Rekordbox care nu mai sunt la calea veche
        (ex. scanate de pe stick) sunt căutate în biblioteca deja importată."""
        self.auto_reloc = {}
        by_name: dict[str, list] = {}
        by_tag: dict[tuple, list] = {}
        for it in self.items:
            by_name.setdefault(os.path.basename(it.path).lower(), []).append(it)
            if it.track.artist and it.track.title:
                by_tag.setdefault((lib.normalize(it.track.artist), lib.normalize(it.track.title)), []).append(it)
        found = 0
        name_idx = None
        for c in self.collections:
            if c.error:
                continue
            for paths in c.playlists.values():
                for p in paths:
                    k = djc.norm_path(p)
                    if k in self.by_path or k in self.auto_reloc or k in self.db.relocations:
                        continue
                    if os.path.exists(p):
                        continue
                    cands = by_name.get(os.path.basename(p.replace("\\", "/")).lower(), [])
                    if len(cands) != 1:
                        info = c.tracks.get(k)
                        if info and info.artist and info.title:
                            tc = by_tag.get((lib.normalize(info.artist), lib.normalize(info.title)), [])
                            if info.duration:
                                tc = [x for x in tc if not x.track.duration or abs(x.track.duration - info.duration) < 4]
                            cands = tc if tc else cands
                    if not cands:              # nume vechi, necorectat: „04. artist - titlu.mp3”
                        if name_idx is None:
                            name_idx = ldb.name_index(self.items)
                        info = c.tracks.get(k)
                        hit = ldb.auto_match_by_name(p, self.items, info.duration if info else 0, name_idx)
                        cands = [hit] if hit is not None else []
                    if cands:
                        self.auto_reloc[k] = cands[0].path
                        found += 1
        return found

    def _files_moved(self, moves):
        for old, new in moves:
            if new:
                self.db.relink(old, new)
        self.db.save()
        self.reload(quiet=True)

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
        top = add(None, f"🎵 Toată biblioteca ({len(self.items)})", ("all",), True)
        # folderele bibliotecii, cu numele lor de pe disc; subfolderele se citesc când deschizi nodul
        for d in self.library_folders():
            self._dir_node(top, d, os.path.basename(d.rstrip("\\/")) or d)
        expanded = getattr(self, "_tree_expanded", set())
        if ("all",) in expanded or not expanded:
            top.setExpanded(True)
        add(None, f"🆕 Noi — folderul de download ({n_new})", ("new",))
        n_nofam = sum(1 for it in self.items if not it.family)
        if n_nofam:
            add(None, f"◌ Fără Family ({n_nofam})", ("nofam",))
        n_sug = sum(1 for it in self.items if it.suggest)
        if n_sug:
            add(None, f"✓ Cu sugestie de Family ({n_sug})", ("sugg",))
        missing = self._missing_items()
        if missing:
            add(None, f"✗ Lipsă / offline ({len(missing)})", ("missing",))
        n_bpm = sum(1 for it in self.items if it.bpm_suggest)
        if n_bpm:
            add(None, f"❓ BPM de verificat ({n_bpm})", ("bpmcheck",))
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

    # ===== arborele de foldere =====
    @staticmethod
    def _subdirs(d) -> list[str]:
        try:
            with os.scandir(d) as it:
                return sorted((e.path for e in it if e.is_dir(follow_symlinks=False)
                               and not e.name.startswith((".", "$")) and e.name != lib.DUPLICATES_FOLDER
                               and e.name != "System Volume Information"),
                              key=lambda p: os.path.basename(p).lower())
        except OSError:
            return []

    def _dir_node(self, parent, d, text=None):
        k = djc.norm_path(d) + os.sep
        n = sum(1 for it in self.items if djc.norm_path(it.path).startswith(k))
        it = QTreeWidgetItem([f"📁 {text or os.path.basename(d) or d} ({n})"])
        it.setData(0, Qt.UserRole, ("dir", d))
        it.setToolTip(0, d)
        parent.addChild(it)
        if self._subdirs(d):
            ph = QTreeWidgetItem(["…"])
            ph.setData(0, Qt.UserRole, ("hdr",))
            it.addChild(ph)
        if ("dir", d) == tuple(self.view):
            self.tree.setCurrentItem(it)
        return it

    def _expand_dir(self, item):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "dir":
            return
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChild(0)
            for sd in self._subdirs(data[1]):
                self._dir_node(item, sd)

    def _dir_items(self, d):
        k = djc.norm_path(d) + os.sep
        return [it for it in self.items if djc.norm_path(it.path).startswith(k)]

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

    def _vocal_items(self):
        from ..core.mixability import name_is_vocal
        return [it for it in self.items
                if name_is_vocal(" ".join([it.track.title, it.track.filename, it.track.comment, it.track.album]))]

    def items_for(self, view) -> tuple[str, list[ldb.LibItem]]:
        kind = view[0]
        if kind == "new":
            return "Noi — folderul de download", self._items_new()
        if kind == "not_traktor":
            return "Nu sunt în colecția Traktor", [it for it in self.items if not it.in_traktor]
        if kind == "missing":
            return "Lipsă / offline (din playlisturi)", self._missing_items()
        if kind == "dir":
            return f"📁 {view[1]}", self._dir_items(view[1])
        if kind == "nofam":
            return "Fără Family", [it for it in self.items if not it.family]
        if kind == "sugg":
            return "Cu sugestie de Family (Taguri online)", [it for it in self.items if it.suggest]
        if kind == "vocal":
            return "Acapella & vocal (după nume și taguri)", self._vocal_items()
        if kind == "bpmcheck":
            return "BPM de verificat (dublu / jumătate?)", [it for it in self.items if it.bpm_suggest]
        if kind == "pl":
            p = self.db.get_playlist(view[1])
            if p:
                return p.name, self._resolve(p.tracks)
        if kind == "smart":
            p = self.db.get_playlist(view[1])
            if p:
                return f"⚡ {p.name}", self._smart_items(p)
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
        order = all_families([it.family for it in self.items]) + ["(nerecunoscut)"]
        for f in order:
            if f not in counts:
                continue
            act = QAction(f"{f}  ({counts[f]})", self.fam_menu)
            act.setCheckable(True)
            act.setChecked(f in current)
            act.toggled.connect(self.apply_filters)
            self.fam_menu.addAction(act)
        if len(counts) > 10:
            from .combo_cells import add_search
            add_search(self.fam_menu, "Caută o familie…")

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
        if len(counts) > 10:
            from .combo_cells import add_search
            add_search(self.genre_menu, "Caută un gen…")

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
        elif COLS[ix.column()][1] == "suggest":
            it = self.table.item_at(ix)
            if it.suggest:
                sel = self.table.selected()
                self.accept_suggestion(sel if any(s is it for s in sel) else [it])

    # ===== Family =====
    def set_family(self, items, fam):
        """Family pusă de tine (pe toate piesele selectate). Gol = revine la cea dedusă din gen."""
        for it in items:
            if it.missing or it.offline:
                continue
            self.db.extra(it.path).user_family = fam
        self.db.save()
        self._annotate(items)
        for it in items:
            self.table.model_.refresh_row(it.path)
        self._fill_family_menu()
        self.rebuild_tree()
        self.status.setText(f"Family „{fam or 'din gen'}” la {len(items)} piese.")

    def accept_suggestion(self, items):
        items = [i for i in items if i.suggest]
        if not items:
            return
        jobs = []
        for it in items:
            self.db.extra(it.path).user_family = it.suggest
            if not it.track.genre.strip():          # genul gol: îl completăm cu familia (convenția Manager)
                jobs.append((it.path, {"genre": it.suggest}, None))
                it.track.genre = it.suggest
        self.db.save()
        self._annotate(items)
        for it in items:
            self.table.model_.refresh_row(it.path)
        if jobs:
            write_tags_in_background(self, jobs, None, "Scriu genul acceptat")
        self._fill_family_menu()
        self.rebuild_tree()
        self.status.setText(f"Am acceptat sugestia la {len(items)} piese.")

    def play(self, it: ldb.LibItem):
        if it.missing or it.offline:
            QMessageBox.information(self, "Player", "Fișierul nu e disponibil (lipsă sau pe un disc deconectat).")
            return
        a = an.load_cached(it.path, analysis_dir())
        t = it.track
        info = " · ".join(x for x in (t.genre, t.comment, f"{it.bpm:g} BPM" if it.bpm else "",
                                      it.camelot, t.year, t.label) if x)
        self.player.load(it.path, f"{t.artist} - {t.display_title}", info, t.duration, a, bpm=it.bpm)
        if a is None:
            self.worker.add([it.path], front=True)
        self.table.model_.playing = it.path
        self.table.viewport().update()

    def _row_changed(self, cur, _prev):
        """Treci la altă piesă cu ↑/↓ cât cântă ceva: pornește singură (autoplay).
        Click-ul (stânga/dreapta) doar selectează."""
        if not cur.isValid() or not self.table.kbd_nav or not self.player.is_playing():
            return
        it = self.table.item_at(cur)
        if it is not None and it.path != self.player.path and not (it.missing or it.offline):
            self.play(it)

    def attach_player(self):
        """Playerul comun vine în pagina asta, cu tot ce cântă."""
        if self._lay.indexOf(self.player) < 0:
            self._lay.insertWidget(1, self.player)
        self.focus_playing()

    def _row_of(self, path) -> int:
        k = djc.norm_path(path)
        for r in range(self.table.proxy.rowCount()):
            if djc.norm_path(self.table.item_at(self.table.proxy.index(r, 0)).path) == k:
                return r
        return -1

    def selected_paths(self) -> list[str]:
        return [i.path for i in self.table.selected() if not (i.missing or i.offline)]

    def focus_paths(self, paths):
        """Selectează piesele venite din altă pagină (Curățenie / Explore) și le aduce la vedere."""
        keys = [djc.norm_path(p) for p in paths]
        known = [k for k in keys if k in self.by_path]
        if not known:
            self.status.setText("Piesa selectată nu e în Bibliotecă." if len(keys) == 1 else
                                "Piesele selectate nu sunt în Bibliotecă.")
            return

        def rows():
            want = set(known)
            return [r for r in range(self.table.proxy.rowCount())
                    if djc.norm_path(self.table.item_at(self.table.proxy.index(r, 0)).path) in want]
        found = rows()
        if not found and self.view != ("all",):
            self.show_view(("all",))
            found = rows()
        if not found:
            self.clear_filters()
            found = rows()
        if not found:
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        selm = self.table.selectionModel()
        sel = QItemSelection()
        last = self.table.proxy.columnCount() - 1
        for r in found:
            sel.select(self.table.proxy.index(r, 0), self.table.proxy.index(r, last))
        selm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        ix = self.table.proxy.index(found[0], 2)
        selm.setCurrentIndex(ix, QItemSelectionModel.NoUpdate)
        self.table.scrollTo(ix, QAbstractItemView.PositionAtCenter)
        self.table.setFocus()

    def focus_playing(self):
        """Arată piesa care cântă (venind din Explore): o selectează, ca să meargă click dreapta pe ea."""
        p = self.player.path
        if not p:
            return
        self.table.model_.playing = p
        if djc.norm_path(p) not in self.by_path:
            self.status.setText("Piesa care cântă nu e în bibliotecă — o poți copia din Explore („Copiază în ▾”). "
                                f"[{os.path.basename(p)}]")
            self.table.viewport().update()
            return
        row = self._row_of(p)
        if row < 0 and self.view != ("all",):
            self.show_view(("all",))
            row = self._row_of(p)
        if row < 0:
            self.clear_filters()
            row = self._row_of(p)
        if row >= 0:
            ix = self.table.proxy.index(row, 2)
            self.table.setCurrentIndex(ix)
            self.table.selectRow(row)
            self.table.scrollTo(ix, QAbstractItemView.PositionAtCenter)
        self.table.viewport().update()

    def _play_next(self, step):
        if not self.isVisible():
            return
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
        """Doar piesele care n-au fost analizate niciodată (cele eșuate nu se reiau singure)."""
        todo = [it.path for it in self.items
                if not it.analyzed and not it.missing and not it.offline and (not it.bpm or not it.key or not it.energy)]
        todo = [p for p in todo if an.load_cached(p, analysis_dir()) is None]
        if not todo:
            if not silent:
                QMessageBox.information(self, "Analiză", "Nu e nicio piesă neanalizată.")
            return
        if not silent and QMessageBox.question(
                self, "Analiză", f"{len(todo)} piese n-au fost analizate niciodată. Le analizez acum, în fundal? "
                "(Poți opri oricând din Activități.)") != QMessageBox.Yes:
            return
        lo = int(settings().value("analysis/bpm_lo", 85) or 85)
        self.worker.bpm_range = (lo, lo * 2 + 5)
        self.worker.add(todo)

    def analyze(self, items):
        """Analizează DOAR piesele alese. Cele deja analizate (cu succes) le reiau doar dacă vrei."""
        items = [it for it in items if not it.missing and not it.offline]
        if not items:
            return
        done = [it for it in items if it.analyzed and not it.an_error]
        todo = [it for it in items if it not in done]
        if done:
            if len(done) == 1:
                q = (f"„{done[0].track.artist} - {done[0].track.display_title}” a fost deja analizată pe "
                     f"{done[0].analyzed[:10]}. O analizez din nou?")
            else:
                q = f"{len(done)} din {len(items)} piese au fost deja analizate. Le analizez și pe ele din nou?"
            r = QMessageBox.question(self, "Analiză", q, QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                todo = items
        if todo:
            lo = int(settings().value("analysis/bpm_lo", 85) or 85)
            self.worker.bpm_range = (lo, lo * 2 + 5)
            self.worker.add([it.path for it in todo], front=True, force=True)

    def _analysis_status(self, left):
        if left:
            self.status.setText(f"Analizez BPM / tonalitate / waveform… mai sunt {left} piese")
        elif self.status.text().startswith("Analizez"):
            self.status.setText("Analiza s-a terminat.")

    def _analysis_done(self, path, a):
        e = self.db.extra(path)
        if not a.error:
            e.an_bpm, e.an_key, e.an_drops, e.an_energy = a.bpm, a.key, a.drops, a.energy
        e.an_error = a.error
        e.an_date = a.analyzed_at or e.an_date
        it = self.by_path.get(djc.norm_path(path))
        if it is not None:
            new = ldb.build_items([it.track], self.db, self.collections, source_priority(),
                                  getattr(self, "history", {}))[0]
            for f in ("bpm", "key", "bpm_src", "key_src", "drops", "energy", "energy_src", "role", "role_suggested",
                      "analyzed", "an_error"):
                setattr(it, f, getattr(new, f))
            self.table.model_.refresh_row(path)
        if path == self.player.path:
            self.player.set_analysis(a)
        if it is not None and not a.error:
            self._auto_bpmkey(it, a)
        if not self.worker.queue:
            self.db.save()
            self._flush_bpmkey()
            self._update_bpmkey_button()

    # ===== BPM / Key în fișiere =====
    def _in_dj(self, it) -> bool:
        return any(not c.error and c.lookup(it.path) is not None for c in self.collections)

    def _auto_bpmkey(self, it, a):
        """După analiză: scriem BPM-ul / tonalitatea DOAR în fișierele care n-au deloc și care nu sunt
        în Traktor / Rekordbox (acolo valorile lor rămân neatinse)."""
        if settings().value("library/auto_bpmkey", True) in (False, "false") or it.missing or it.offline:
            return
        if self._in_dj(it):
            return
        t = it.track
        f = {}
        if not (t.bpm or "").strip() and a.bpm:
            f["bpm"] = str(int(round(it.bpm or a.bpm)))
        if not (t.key or "").strip() and (it.key or a.key):
            f["key"] = it.key or a.key
        if f:
            if not hasattr(self, "_bpmkey_pending"):
                self._bpmkey_pending = []
            self._bpmkey_pending.append((it.path, f, None))
            for k, v in f.items():
                setattr(t, k, v)
            if len(self._bpmkey_pending) >= 25:
                self._flush_bpmkey()

    def _flush_bpmkey(self):
        jobs = getattr(self, "_bpmkey_pending", [])
        if jobs:
            self._bpmkey_pending = []
            write_tags_in_background(self, jobs, None, "Scriu BPM / Key din analiză (doar unde lipseau)")

    def bpm_key_rows(self):
        """Piesele unde valoarea din Manager diferă de fișier sau de Traktor / Rekordbox."""
        rows = []
        for it in self.items:
            if it.missing or it.offline:
                continue
            t = it.track
            fb = float(t.bpm) if (t.bpm or "").replace(".", "", 1).isdigit() else 0.0
            fk = to_camelot(t.key) or (t.key or "")
            mb, mk = it.bpm, (it.camelot or it.key)
            dj_vals = []
            for c in self.collections:
                if c.error:
                    continue
                info = c.lookup(it.path)
                if info is not None:
                    dj_vals.append((c.source, info.bpm, to_camelot(info.key) or info.key))
            bpm_diff_file = bool(mb) and abs(round(mb) - round(fb)) >= 1
            key_diff_file = bool(mk) and mk != fk
            dj_diff = [(src, b, k) for src, b, k in dj_vals
                       if (mb and b and abs(mb - b) > 0.6) or (mk and k and mk != k)]
            if bpm_diff_file or key_diff_file or dj_diff:
                rows.append({"it": it, "file": (fb, fk), "mgr": (mb, mk), "src": (it.bpm_src, it.key_src),
                             "dj": dj_vals, "dj_diff": dj_diff, "to_file": bpm_diff_file or key_diff_file})
        return rows

    def _update_bpmkey_button(self):
        if not hasattr(self, "b_bpmkey"):
            return
        n = sum(1 for r in self.bpm_key_rows() if r["to_file"] or r["dj_diff"])
        self.b_bpmkey.setText(f"BPM & Key: {n} diferențe" if n else "BPM & Key ✓")
        self.b_bpmkey.setObjectName("primary" if n else "")
        self.b_bpmkey.style().unpolish(self.b_bpmkey)
        self.b_bpmkey.style().polish(self.b_bpmkey)

    def bpm_key_review(self):
        from .library_dialogs import BpmKeyDialog
        dlg = BpmKeyDialog(self.bpm_key_rows(), self)

        def write(jobs):
            write_tags_in_background(self, jobs, lambda: (self.reload(quiet=True)), "Scriu BPM / Key în fișiere")
        dlg.on_write = write
        from .windows import tool_window
        tool_window(dlg)

    # ===== meniul pieselor =====
    def _table_menu(self, pos):
        sel = self.table.selected()
        if not sel:
            return
        ix = self.table.indexAt(pos)
        col_key = COLS[ix.column()][1] if ix.isValid() else ""
        m = QMenu(self)
        lost = [i for i in sel if i.missing or i.offline]
        if lost:
            m.addAction("🔍 Caută piesa în bibliotecă (după nume, fără număr)…", lambda: self.find_in_library(lost[0]))
            m.addAction("Caută piesele lipsă într-un folder…", lambda: self.relocate([i.path for i in lost]))
            m.addSeparator()
        m.addAction("▶ Redă", lambda: self.play(sel[0]))
        fm = m.addMenu(f"Family ({len(sel)} piese)" if len(sel) > 1 else "Family")
        for f in all_families():
            fm.addAction(f, lambda f=f: self.set_family(sel, f))
        fm.addSeparator()
        fm.addAction("Altă familie…", lambda: (lambda r: self.set_family(sel, r[0].strip()) if r[1] and r[0].strip()
                                                else None)(QInputDialog.getText(self, "Family", "Numele familiei:")))
        fm.addAction("Revino la cea dedusă din gen", lambda: self.set_family(sel, ""))
        if any(i.suggest for i in sel):
            m.addAction("✓ Acceptă sugestia (Family)", lambda: self.accept_suggestion(sel))
        add = m.addMenu("Adaugă în playlist")
        paths = [i.path for i in sel]
        sugg, create = self._suggest_playlists(sel, col_key)
        if sugg or create:
            what = {"family": "familia", "bpm": "intervalul de BPM", "role": "rolul"}.get(
                col_key, COLS[[k for _, k in COLS].index(col_key)][0] if col_key in [k for _, k in COLS] else "")
            add.addAction(f"Potrivite după {what}:").setEnabled(False)
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
        bm = m.addMenu("BPM")
        with_bpm = [i for i in sel if i.bpm and not (i.missing or i.offline)]
        if with_bpm:
            one = with_bpm[0]
            bm.addAction(f"×2" + (f"   ({one.bpm:g} → {one.bpm * 2:g})" if len(with_bpm) == 1 else f"   ({len(with_bpm)} piese)"),
                         lambda: self.scale_bpm(with_bpm, 2))
            bm.addAction(f"½" + (f"   ({one.bpm:g} → {one.bpm / 2:g})" if len(with_bpm) == 1 else f"   ({len(with_bpm)} piese)"),
                         lambda: self.scale_bpm(with_bpm, 0.5))
            sug = [i for i in with_bpm if i.bpm_suggest]
            if sug:
                bm.addAction(f"Acceptă sugestia ({len(sug)} piese)", lambda: self.accept_bpm(sug))
        else:
            bm.setEnabled(False)
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
        m.addAction(reveal_label(), lambda: reveal_in_folder(sel[0].path))
        m.addAction("Sortează în foldere…", lambda: self.sort_into_folders(sel))
        from .dialogs import browser_search_menu
        browser_search_menu(m, sel[0].track.artist, sel[0].track.display_title)
        m.addSeparator()
        m.addAction(f"Șterge fișierul… ({len(sel)})" if len(sel) > 1 else "Șterge fișierul…",
                    lambda: self.delete_files(sel))
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ===== editare direct în tabel =====
    TAG_FIELDS = {"artist": "artist", "title": "title", "genre": "genre", "style": "comment",
                  "year": "year", "label": "label", "mood": "mood", "grouping": "grouping"}

    def _edit_cell_multi(self, it, col, value) -> bool:
        """Dacă piesa editată face parte dintr-o selecție de mai multe, valoarea se pune la toate cele selectate."""
        sel = self.table.selected()
        if len(sel) > 1 and any(s is it for s in sel):
            ok = self.edit_cell(it, col, value)
            if ok:
                for other in sel:
                    if other is not it and not (other.missing or other.offline):
                        self.edit_cell(other, col, value)
                        self.table.model_.refresh_row(other.path)
                self.status.setText(f"Am pus „{value}” la {len(sel)} piese.")
            return ok
        return self.edit_cell(it, col, value)

    def edit_cell(self, it, col, value) -> bool:
        """Ai modificat o celulă. Tagurile se scriu în fișier (în fundal); restul rămâne în aplicație."""
        e = self.db.extra(it.path)
        if col in self.TAG_FIELDS:
            field = self.TAG_FIELDS[col]
            setattr(it.track, field, value)
            write_tags_in_background(self, [(it.path, {field: value}, None)], None,
                                     f"Scriu {COLS[[k for _, k in COLS].index(col)][0]}")
            if col in ("genre", "style"):
                self._annotate([it])
        elif col == "family":
            e.user_family = value
            it.family = value
            if not value:
                self._annotate([it])
            self._fill_family_menu()
        elif col == "role":
            if value and value not in ldb.ROLES:
                match = next((r for r in ldb.ROLES if r.lower().startswith(value.lower())), "")
                if not match:
                    QMessageBox.information(self, "Role", "Rolul poate fi: " + ", ".join(ldb.ROLES))
                    return False
                value = match
            self.set_role([it], value)
        elif col == "energy":
            if value and not (value.isdigit() and 1 <= int(value) <= 10):
                QMessageBox.information(self, "Energy", "Energia e un număr de la 1 la 10 (sau gol).")
                return False
            e.user_energy = int(value) if value else 0
            self._refresh_item(it)
            self._write_comments([it], quiet=True)
        elif col == "bpm":
            try:
                v = float(value.replace(",", ".")) if value else 0.0
            except ValueError:
                QMessageBox.information(self, "BPM", "Scrie BPM-ul ca număr (ex. 124 sau 123.5), sau gol.")
                return False
            if v and not 40 <= v <= 250:
                QMessageBox.information(self, "BPM", "BPM-ul trebuie să fie între 40 și 250.")
                return False
            e.user_bpm = v
            self._refresh_item(it)
            if v:
                write_tags_in_background(self, [(it.path, {"bpm": str(int(round(v)))}, None)], None, "Scriu BPM-ul")
        elif col == "key":
            if value and not to_camelot(value):
                QMessageBox.information(self, "Key", "Nu recunosc tonalitatea. Exemple: 8A, 11B, Am, F#m, Db.")
                return False
            e.user_key = value
            self._refresh_item(it)
            if value:
                write_tags_in_background(self, [(it.path, {"key": value}, None)], None, "Scriu tonalitatea")
        else:
            return False
        self.db.save()
        self.status.setText(f"Am schimbat {COLS[[k for _, k in COLS].index(col)][0]} la "
                            f"„{it.track.artist} - {it.track.display_title}”.")
        return True

    def _free_player(self, paths):
        """Piesa care cântă e printre cele șterse / mutate: trecem la următoarea din listă (care rămâne)."""
        gone = {djc.norm_path(p) for p in paths}

        def nxt():
            vis = self.table.visible()
            cur = djc.norm_path(self.player.path)
            idx = next((i for i, it in enumerate(vis) if djc.norm_path(it.path) == cur), -1)
            for it in vis[idx + 1:] + vis[:max(0, idx)]:
                if djc.norm_path(it.path) not in gone and not (it.missing or it.offline):
                    return lambda it=it: self.play(it)
            return None
        self.player.free(paths, nxt)
        if self.player.path and djc.norm_path(self.player.path) in gone:
            self.player.release()

    def sort_into_folders(self, items):
        from .sort_tool import items_from_library, open_sort
        open_sort(items_from_library(items), self, self, self.title.text())

    def delete_files(self, items):
        items = [i for i in items if not (i.missing or i.offline)]
        if not items:
            return
        keys = {djc.norm_path(i.path) for i in items}
        in_pl = sum(1 for p in self.db.playlists if p.kind == "manual"
                    for t in p.tracks if djc.norm_path(t) in keys)
        in_tk = sum(1 for i in items if i.in_traktor)
        q = f"Trimit {len(items)} fișiere în Coșul de gunoi?"
        if in_pl:
            q += f"\n\nApar de {in_pl} ori în playlisturile Manager — le scot de acolo."
        if in_tk:
            q += f"\n{in_tk} sunt în Traktor: acolo vor apărea ca lipsă (colecția Traktor n-o modific)."
        q += "\n\nPe discurile fără Coș (stick, card, rețea) nu le șterg definitiv: le mut în „_De șters” lângă ele."
        if QMessageBox.question(self, "Șterge", q) != QMessageBox.Yes:
            return
        self._free_player([i.path for i in items])
        from .fileops import delete_in_background, delete_summary

        def done(ok, moved, errors):
            gone = {djc.norm_path(p) for p in ok} | {djc.norm_path(a) for a, _b in moved}
            for p in self.db.playlists:
                if p.kind == "manual":
                    p.tracks = [t for t in p.tracks if djc.norm_path(t) not in gone]
            self.db.save()
            self.status.setText(delete_summary(ok, moved))
            if errors:
                QMessageBox.warning(self, "Erori", "\n".join(errors[:15]))
            self.reload(quiet=True)
        delete_in_background(f"Bibliotecă: șterg {len(items)} fișiere", [i.path for i in items], done)

    def scale_bpm(self, items, factor):
        for it in items:
            self.db.extra(it.path).user_bpm = round(it.bpm * factor, 2)
        self._after_bpm(items)

    def accept_bpm(self, items):
        for it in items:
            self.db.extra(it.path).user_bpm = it.bpm_suggest
        self._after_bpm(items)

    def _after_bpm(self, items):
        """BPM-ul decis de tine: rămâne în Manager și se scrie și în fișier (îl vede și Rekordbox)."""
        for it in items:
            self._refresh_item(it)
            it.bpm_suggest = 0.0
        self.db.save()
        jobs = [(it.path, {"bpm": str(int(round(it.bpm)))}, None) for it in items if it.bpm and not it.missing]
        if jobs:
            write_tags_in_background(self, jobs, None, "Scriu BPM-ul")
        self.rebuild_tree()
        if self.view[0] == "bpmcheck":
            self.show_view(self.view)
        else:
            for it in items:
                self.table.model_.refresh_row(it.path)

    def _refresh_item(self, it):
        new = ldb.build_items([it.track], self.db, self.collections, source_priority(),
                              getattr(self, "history", {}))[0]
        for f in ("bpm", "key", "bpm_src", "key_src", "energy", "energy_src", "role", "role_suggested"):
            setattr(it, f, getattr(new, f))
        self.table.model_.refresh_row(it.path)

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
        if role.startswith("rating:"):
            self.set_rating(sel, int(role.split(":")[1]))
            return
        if role == "__delete__":
            # într-un playlist, Delete doar scoate piesa din playlist; altfel șterge fișierul (cu întrebare)
            if self.view and self.view[0] == "pl":
                self.remove_from_playlist(sel)
            else:
                self.delete_files(sel)
            return

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

    def _suggest_playlists(self, sel, col_key: str = ""):
        """Sugestii după coloana pe care ai dat click dreapta:
        Gen / Familie / Stil → familia; BPM → intervalul de 10; Rol → rolul. Altfel, nimic."""
        base = sel[0] if sel else None
        if not base:
            return [], ""
        if col_key == "family":
            target = base.family
            if not target:
                return [], ""
            match = lambda fam, bpm, role, name: fam == target or name.lower() == target.lower()  # noqa: E731
            create = target
        elif col_key == "bpm":
            if not base.bpm:
                return [], ""
            lo, hi = ldb.bpm_bucket(base.bpm)
            create = f"{lo}–{hi}"
            match = lambda fam, bpm, role, name: (bpm and lo <= bpm < hi) or name.replace("-", "–") == create  # noqa: E731
        elif col_key == "role":
            target = base.role_or_suggestion
            if not target:
                return [], ""
            match = lambda fam, bpm, role, name: role == target or name.lower() == target.lower()  # noqa: E731
            create = target
        elif col_key in VALUE_COLS:
            fn = VALUE_COLS[col_key]
            target = fn(base)
            if not target:
                return [], ""
            out = []
            for p in self.db.playlists:
                if p.kind != "manual":
                    continue
                items = self._resolve(p.tracks) if p.tracks else []
                if p.name.lower() == target.lower() or (items and ldb.top_value(items, fn) == target):
                    out.append(p)
            create = "" if any(p.name.lower() == target.lower() for p in self.db.playlists) else target
            return out[:10], create
        else:
            return [], ""
        out = []
        for p in self.db.playlists:
            if p.kind != "manual":
                continue
            fam, bpm, role = ldb.playlist_profile(self._resolve(p.tracks)) if p.tracks else ("", 0, "")
            if match(fam, bpm, role, p.name):
                out.append(p)
        if any(p.name.lower() == create.lower() for p in self.db.playlists):
            create = ""
        return out[:10], create

    def show_mixable(self, it):
        self.mix_base = it
        self.rebuild_tree()
        self.show_view(("mix",))

    def set_rating(self, items, n):
        """Ratingul rămâne în Manager și se scrie și în fișier (merge cu piesa când o copiezi / muți;
        Traktor îl citește la reimport)."""
        jobs = []
        for it in items:
            self.db.extra(it.path).rating = n
            it.rating = n
            it.track.rating = n
            self.table.model_.refresh_row(it.path)
            if not (it.missing or it.offline):
                jobs.append((it.path, {"rating": str(n)}, None))
        self.db.save()
        if jobs:
            write_tags_in_background(self, jobs, None, f"Scriu ratingul ({n}★)")

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
        from .windows import tool_window
        tool_window(dlg)    # fereastră separată: nu blochează, are buton în bara de task-uri

    def remove_danceability(self):
        jobs = []
        for it in self.items:
            if it.missing or not ldb.has_junk_comment(it.track.comment):
                continue
            style, role, energy = ldb.parse_comment(it.track.comment)
            jobs.append((it.path, {"comment": ldb.compose_comment(style, role, energy)}, None))
        if not jobs:
            QMessageBox.information(self, "Comentarii", "Nu am găsit „Danceability” în comentarii.")
            return
        if QMessageBox.question(self, "Comentarii", f"Șterg „Danceability” din Comentariul a {len(jobs)} fișiere? "
                                "Stilul, rolul și energia rămân.") != QMessageBox.Yes:
            return
        write_tags_in_background(self, jobs, self.reload, "Șterg „Danceability”")

    def category_cleanup(self, field):
        if field == "family":
            return self.genre_cleanup()
        from .library_dialogs import CategoryCleanupDialog
        dlg = CategoryCleanupDialog(self.items, field, self)
        dlg.on_applied = lambda *_: self.reload(quiet=True)
        dlg.exec()

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

    def _smart_items(self, p, rules=None):
        """Piesele unui playlist inteligent. Unul făcut dintr-un playlist normal își păstrează piesele
        puse de mână: regulile fie adaugă din bibliotecă, fie filtrează doar piesele lui."""
        rules = rules if rules is not None else p.rules
        if not p.tracks:
            return ldb.smart_filter(self.items, rules)
        base = self._resolve(p.tracks)
        if not (rules.get("rules") or []):
            return base
        if rules.get("scope") == "own":
            return ldb.smart_filter(base, rules)
        seen = {djc.norm_path(i.path) for i in base}
        return base + [i for i in ldb.smart_filter(self.items, rules) if djc.norm_path(i.path) not in seen]

    def edit_smart(self, pid, convert=False):
        p = self.db.get_playlist(pid) if pid else None
        fixed = len(p.tracks) if p else 0
        rules = p.rules if (p and (p.kind == "smart" or (p.rules or {}).get("rules"))) else None
        if convert and rules is None:
            rules = {"match": "all", "rules": [{"field": "genre", "op": "este oricare din", "value": ""}]}
        dlg = SmartPlaylistDialog(p.name if p else "", rules,
                                  lambda r: len(self._smart_items(p, r)) if p else len(ldb.smart_filter(self.items, r)),
                                  self, fixed_count=fixed)
        if dlg.exec() != QDialog.Accepted:
            return
        if not p:
            p = self.db.new_playlist(dlg.name.text().strip(), "smart")
        p.kind = "smart"
        p.name = dlg.name.text().strip()
        p.rules = dlg.rules()
        self.db.save()
        self.rebuild_tree()
        self.show_view(("smart", p.id))
        self._auto_export()

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        d = item.data(0, Qt.UserRole) if item else None
        if d and d[0] == "dir":
            m = QMenu(self)
            items = self._dir_items(d[1])
            m.addAction(reveal_label(), lambda: open_file(d[1]))
            m.addAction(f"Sortează în foldere… ({len(items)} piese)", lambda: self.sort_into_folders(items))
            m.addAction(f"Analizează ({len(items)} piese)", lambda: self.analyze(items))
            m.addAction("Taguri online…", lambda: self.tag_finder(items))
            m.exec(self.tree.viewport().mapToGlobal(pos))
            return
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
                m.addAction("Transformă în playlist obișnuit (cu piesele de acum)", lambda: self._freeze(p, items))
            else:
                m.addAction("Fă-l inteligent (adaugă reguli)…", lambda: self.edit_smart(p.id, convert=True))
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

    def set_relocation(self, old: str, new: str):
        """Piesa lipsă `old` este de fapt fișierul `new` din bibliotecă."""
        self.db.relocations[djc.norm_path(old)] = new
        for p in self.db.playlists:            # în playlisturile tale schimbăm direct calea
            p.tracks = [new if djc.norm_path(t) == djc.norm_path(old) else t for t in p.tracks]
        self.db.save()

    def find_in_library(self, missing):
        from .library_dialogs import FindInLibraryDialog
        info = None
        for c in self.collections:
            info = c.tracks.get(djc.norm_path(missing.path)) if not c.error else None
            if info:
                break
        dlg = FindInLibraryDialog(missing, self.items, self, info.duration if info else 0)
        if dlg.exec() and dlg.chosen is not None:
            self.set_relocation(missing.path, dlg.chosen.path)
            self._annotate(self.items)
            self.rebuild_tree()
            self.show_view(self.view)
            self.status.setText(f"Am legat „{os.path.basename(missing.path)}” de {dlg.chosen.path}")

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
        """Inteligent → normal, pe loc: rămân piesele de acum; regulile se țin minte (le poți readuce)."""
        p.kind = "manual"
        p.tracks = [i.path for i in items]
        self.db.save()
        self.rebuild_tree()
        self.show_view(("pl", p.id))

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
