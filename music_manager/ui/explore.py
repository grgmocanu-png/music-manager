"""Explore: asculți și analizezi orice muzică din Manager, pe categorii — biblioteca, playlisturile,
Traktor, familiile, folderele din calculator, playlisturile online și ce ai descărcat de pe Soulseek.

Pe fiecare piesă: mixabilitate (scor 1–10 cu motive), BPM constant, 4/4, puls, intro/outro ritmic,
cel mai bun segment, voce / tobe / tip (acapella fără AI), folosire sugerată. Bife (X sau click),
Copiază / Mută în bibliotecă, playlist sau folder, Sortează în foldere, Șterge (în Coș).
"""
from __future__ import annotations

import json
import os
import shutil

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QSplitter,
    QTableView, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import analysis as an
from ..core import library as lib
from ..core import mixability as mx
from .common import (
    analysis_dir, after_moves, data_dir, fmt_duration, mix_dir, reveal_in_folder, reveal_label, settings,
)

COLS = [
    ("", "check"), ("Artist", "artist"), ("Title", "title"), ("Mix", "mix"), ("Rating", "rating"), ("BPM", "bpm"),
    ("Const.", "const"), ("4/4", "four"), ("Key", "key"), ("Energy", "energy"), ("Puls", "pulse"),
    ("Drop+P", "dropp"), ("In / Out", "inout"), ("Segment", "segment"),
    ("Folosire", "use"), ("Family", "family"), ("Genre", "genre"), ("Year", "year"),
    ("Duration", "duration"), ("Folder", "folder"),
]
KEYS = [k for _, k in COLS]
# capul coloanei rămâne scurt; la hover: numele întreg + ce înseamnă
HEADER_TIPS = {
    "check": ("Bifă", "X sau click: bifează piesele pentru Copiază / Mută / Sortează / Șterge."),
    "artist": ("Artist", ""),
    "title": ("Titlu", "Portocaliu = piesa care cântă."),
    "mix": ("Mixabilitate", "Scor 1–10: cât de ușor se mixează (beat stabil, intro / outro ritmice, kick). "
                            "F2 = pui tu scorul (✎)."),
    "rating": ("Rating", "Stelele tale: 1–5 (0 șterge), click pe stele sau click dreapta. Se scriu în fișier."),
    "bpm": ("BPM", "Din Bibliotecă (Traktor / Rekordbox / pus de tine), altfel din analiză."),
    "const": ("BPM constant", "„da” = tempo-ul nu variază: se sincronizează fără corecții. ±N = cât variază."),
    "four": ("Kick 4/4", "„da” = kick pe fiecare timp (house, techno). „nu” = breakbeat / fără kick regulat."),
    "key": ("Tonalitate (Camelot)", ""),
    "energy": ("Energie 1–10", "Din analiză sau din Comentariu."),
    "pulse": ("Puls ritmic %", "Cât la sută din piesă are beat clar."),
    "dropp": ("Beat după drop", "„da” = după drop revine beat-ul (bun pentru mixat din drop)."),
    "inout": ("Intro / outro ritmic", "Câte secunde de beat are la început / la sfârșit."),
    "segment": ("Segment mixabil", "Cea mai lungă porțiune cu beat continuu; playerul pornește de acolo."),
    "use": ("Cum o folosești", "direct · segment · overlay · ½ tempo · intro / outro de set · greu de mixat. "
                               "F2 = alegi tu (✎)."),
    "family": ("Familie de gen", ""),
    "genre": ("Gen (din tag)", ""),
    "year": ("An", ""),
    "duration": ("Durată", ""),
    "folder": ("Folder", "Unde e fișierul pe disc."),
}
USES = ["direct", "segment", "overlay", "½ tempo", "intro / outro de set", "greu de mixat"]


# --- valorile puse de mână (Mix, Folosire): au prioritate față de analiză ---------
_MANUAL: dict | None = None


def _manual_path() -> str:
    return os.path.join(data_dir(), "mixabilitate_manual.json")


def manual() -> dict:
    global _MANUAL
    if _MANUAL is None:
        try:
            with open(_manual_path(), encoding="utf-8") as f:
                _MANUAL = json.load(f) or {}
        except (OSError, ValueError):
            _MANUAL = {}
    return _MANUAL


def save_manual():
    from ..core.safeio import write_json
    try:
        write_json(_manual_path(), manual(), ensure_ascii=False)
    except OSError:
        pass
SORT_ROLE = Qt.UserRole + 1
SORT_LEVELS = [("Family", "family"), ("Genre", "genre"), ("Folosire", "use"),
               ("Mix (≥7 / restul)", "mixband"), ("BPM (din 10 în 10)", "bpm10"), ("Key", "key"), ("Artist", "artist")]


class Row:
    __slots__ = ("track", "family", "bpm", "key", "mix", "checked", "missing", "rating")

    def __init__(self, track, family="", bpm=0.0, key="", missing=False, rating=None):
        self.track, self.family, self.bpm, self.key = track, family, bpm, key
        self.mix: mx.MixInfo | None = None
        self.checked = False
        self.missing = missing
        self.rating = int(rating if rating is not None else (getattr(track, "rating", 0) or 0))

    @property
    def path(self):
        return self.track.path

    def manual(self) -> dict:
        return manual().get(_norm(self.path), {}) if self.path else {}

    @property
    def score(self) -> int:
        """Scorul: cel pus de tine, altfel cel din analiză (-1 = neanalizată)."""
        v = self.manual().get("score")
        if v:
            return int(v)
        m = self.mix
        return m.score if (m is not None and not m.error) else -1

    @property
    def use(self) -> str:
        v = self.manual().get("use")
        if v:
            return v
        m = self.mix
        return m.use if (m is not None and not m.error) else ""

    def value(self, key):
        """Valoarea pentru sortarea în foldere."""
        m = self.mix
        if key == "family":
            return self.family or "_Necunoscut"
        if key == "genre":
            return self.track.genre or "_Necunoscut"
        if key == "use":
            return self.use or "_Neanalizate"
        if key == "mixband":
            sc = self.score
            return "_Neanalizate" if sc < 0 else ("Mix 7+" if sc >= 7 else "Mix sub 7")
        if key == "bpm10":
            b = self.bpm or (m.bpm if m else 0)
            return f"{int(b // 10 * 10)}-{int(b // 10 * 10) + 10}" if b else "_Fără BPM"
        if key == "key":
            return self.key or (m.key if m else "") or "_Fără Key"
        if key == "artist":
            return (self.track.artist.split(",")[0].strip() or "_Necunoscut")
        return "_Necunoscut"


class ExploreModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self.playing = ""

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def refresh(self, path):
        for i, r in enumerate(self.rows):
            if r.path == path:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[s][0]
        if o == Qt.Horizontal and role == Qt.ToolTipRole:
            full, expl = HEADER_TIPS.get(COLS[s][1], (COLS[s][0], ""))
            return f"<b>{full}</b>" + (f"<br>{expl}" if expl else "")
        return None

    def flags(self, index):
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        col = COLS[index.column()][1]
        if index.column() == 0:
            f |= Qt.ItemIsUserCheckable
        if col in ("mix", "use") and not self.rows[index.row()].missing:
            f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 0:
            self.rows[index.row()].checked = (Qt.CheckState(value) == Qt.Checked)
            self.dataChanged.emit(index, index)
            return True
        if role == Qt.EditRole:
            r = self.rows[index.row()]
            col = COLS[index.column()][1]
            v = str(value or "").strip().rstrip("✎").strip()
            k = _norm(r.path)
            d = dict(manual().get(k, {}))
            if col == "mix":
                if v and not (v.isdigit() and 1 <= int(v) <= 10):
                    QMessageBox.information(None, "Mixabilitate",
                                            "Scorul e un număr de la 1 la 10 (gol = cel din analiză).")
                    return False
                if v:
                    d["score"] = int(v)
                else:
                    d.pop("score", None)
            elif col == "use":
                if v:
                    d["use"] = v
                else:
                    d.pop("use", None)
            else:
                return False
            if d:
                manual()[k] = d
            else:
                manual().pop(k, None)
            save_manual()
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLS) - 1))
            return True
        return False

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        t, m = r.track, r.mix
        col = COLS[index.column()][1]
        ok = m is not None and not m.error
        if role == Qt.CheckStateRole and col == "check":
            return Qt.Checked if r.checked else Qt.Unchecked
        if role == Qt.EditRole:
            if col == "mix":
                return str(r.score) if r.score > 0 else ""
            if col == "use":
                return r.use
            return None
        if role == Qt.DisplayRole and col == "rating":
            return "★" * r.rating if r.rating else ""
        if role == Qt.DisplayRole and col in ("mix", "use") and r.manual().get("score" if col == "mix" else "use"):
            return f"{r.score}✎" if col == "mix" else f"{r.use} ✎"
        if role == Qt.DisplayRole:
            if col == "artist":
                return t.artist
            if col == "title":
                return t.display_title
            if col == "family":
                return r.family
            if col == "genre":
                return t.genre
            if col == "year":
                return t.year
            if col == "duration":
                return fmt_duration(t.duration)
            if col == "folder":
                return os.path.dirname(t.path)
            if col == "bpm":
                b = r.bpm or (m.bpm if ok else 0)
                return f"{b:.1f}".rstrip("0").rstrip(".") if b else ("—" if ok else "")
            if col == "key":
                return r.key or (m.key if ok else "")
            if m is None:
                return "" if col != "mix" else "·"
            if m.error:
                return "eroare" if col == "mix" else ""
            if col == "mix":
                return str(m.score)
            if col == "const":
                return (f"da ±{m.bpm_drift:g}" if m.bpm_const else (f"±{m.bpm_drift:g}" if m.pulse >= 30 else "—"))
            if col == "four":
                return m.four
            if col == "energy":
                return m.energy or ""
            if col == "pulse":
                return f"{m.pulse}%"
            if col == "dropp":
                return m.drop_pulse
            if col == "inout":
                return f"{m.intro_s}s / {m.outro_s}s" if (m.intro_s or m.outro_s) else "—"
            if col == "segment":
                return m.segment or "—"
            if col == "use":
                return m.use
            return ""
        if role == SORT_ROLE:
            if col == "check":
                return int(r.checked)
            if col == "mix":
                return r.score
            if col == "rating":
                return r.rating
            if col == "bpm":
                return r.bpm or (m.bpm if ok else 0)
            if col == "pulse":
                return m.pulse if ok else -1
            if col == "energy":
                return m.energy if ok else -1
            if col == "duration":
                return t.duration
            if col == "inout":
                return (m.intro_s + m.outro_s) if ok else -1
            if col == "segment":
                return (m.seg_end - m.seg_start) if ok else -1
            v = self.data(index, Qt.DisplayRole)
            return str(v or "").lower()
        if role == Qt.ForegroundRole:
            if r.missing:
                return QBrush(QColor("#888888"))
            if col == "mix" and r.score > 0:
                sc = r.score
                return QBrush(QColor("#9fe0d2" if sc >= 8 else "#ebcf96" if sc >= 5 else "#d99b8c"))
            if col == "rating":
                return QBrush(QColor("#e0a458"))
            if col == "const" and ok and m.bpm_const:
                return QBrush(QColor("#9fe0d2"))
            if col == "title" and r.path == self.playing:
                return QBrush(QColor("#e0a458"))
            if col == "key" and (r.key or (m.key if ok else "")):
                from .library_view import key_color
                from ..core.playlist import to_camelot
                return QBrush(key_color(to_camelot(r.key or m.key) or ""))
            if col == "family" and r.family:
                from .library_view import family_color
                return QBrush(family_color(r.family))
            if col == "use" and r.use:
                return QBrush(QColor({"direct": "#9fe0d2", "segment": "#ebcf96", "overlay": "#c79bd6",
                                      "½ tempo": "#8fa8e0"}.get(r.use, "#b8bcc2")))
        if role == Qt.ToolTipRole:
            if r.missing:
                return "Nedescărcată — fără fișier nu se poate asculta sau analiza."
            if col == "mix" and r.manual().get("score"):
                return f"Pus de tine: {r.score}/10 (analiza: {m.score if ok else '—'}). Click dreapta → Revino la analiză."
            if col == "mix" and m is not None:
                return (f"Mix {m.score}/10: {m.reasons}" if ok else f"Analiza a eșuat: {m.error}")
            if col == "use" and r.manual().get("use"):
                return f"Pus de tine (analiza: {m.use if ok else '—'})."
            return t.path
        if role == Qt.TextAlignmentRole and col in ("mix", "bpm", "pulse", "energy", "duration"):
            return int(Qt.AlignCenter)
        return None


class ExploreProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.words: list[str] = []
        self.f_mix = self.f_const = self.f_four = self.f_vocal = self.f_rating = False

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        m = r.mix
        ok = m is not None and not m.error
        if self.words:
            t = r.track
            hay = lib.normalize(" ".join([t.artist, t.title, t.genre, t.comment, t.filename, r.family]))
            if not all(w in hay for w in self.words):
                return False
        if self.f_mix and not r.score >= 7:
            return False
        if self.f_rating and r.rating < 3:
            return False
        if self.f_const and not (ok and m.bpm_const):
            return False
        if self.f_four and not (ok and m.four == "da"):
            return False
        if self.f_vocal:
            t = r.track
            named = mx.name_is_vocal(" ".join([t.title, t.filename, t.comment, t.album]))
            if not (named or (ok and (m.kind in ("acapella", "vocal + puțin instrumental") or m.voice >= 55))):
                return False
        return True


class ExploreTable(QTableView):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view

    def keyPressEvent(self, e):
        k, mod = e.key(), e.modifiers()
        plain = not mod & (Qt.ControlModifier | Qt.AltModifier)
        if k == Qt.Key_Space and plain:
            self.view.toggle_play()
            return
        if k == Qt.Key_X and plain:
            self.view.toggle_checks()
            return
        if k == Qt.Key_C and plain:
            self.view.copy_to_last()
            return
        if k == Qt.Key_M and plain:
            self.view.move_to_last()
            return
        if k == Qt.Key_Delete:
            self.view.delete_rows()
            return
        if e.text() and e.text() in "012345" and plain and self.state() != QAbstractItemView.EditingState:
            self.view.set_rating(self.view._selected(), int(e.text()))
            return
        if self.view.player.handle_key(e):
            return
        self.kbd_nav = True          # autoplay doar când schimbi piesa din taste (↑/↓)
        try:
            super().keyPressEvent(e)
        finally:
            self.kbd_nav = False

    kbd_nav = False


class ExploreView(QWidget):
    def __init__(self, state, library, download, parent=None):
        super().__init__(parent)
        self.state, self.library, self.download = state, library, download
        self.source = ("all",)
        self._scan_cache: dict[str, list] = {}
        self.task = None

        # --- stânga: sursele ---
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setObjectName("sidebar")
        self.tree.itemClicked.connect(self._source_clicked)
        self.tree.itemExpanded.connect(self._expand_dir)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        b_add = QPushButton("+ Adaugă folder")
        b_add.clicked.connect(self.add_folder)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.tree, 1)
        ll.addWidget(b_add)

        # --- sus: titlu, analiză, filtre, căutare ---
        self.title = QLabel("Toată muzica")
        self.title.setObjectName("pageTitle")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.b_analyze = QPushButton("Analizează")
        self.b_analyze.setObjectName("primary")
        self.b_analyze.setToolTip("Mixabilitatea pieselor afișate care n-au fost analizate (o singură dată pe piesă).")
        self.b_analyze.clicked.connect(lambda: self.analyze_visible())
        self.filters = {}
        bar = QHBoxLayout()
        bar.addWidget(self.title)
        bar.addWidget(self.count)
        bar.addSpacing(10)
        bar.addWidget(self.b_analyze)
        bar.addSpacing(10)
        for key, label in (("f_mix", "Mix ≥ 7"), ("f_rating", "Rating ≥ ★★★"), ("f_const", "BPM constant"),
                           ("f_four", "4/4")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("chip")
            b.toggled.connect(lambda on, k=key: self._set_filter(k, on))
            self.filters[key] = b
            bar.addWidget(b)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Caută…")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._search)
        bar.addWidget(self.search)

        # --- tabelul ---
        self.model = ExploreModel(self)
        self.proxy = ExploreProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = ExploreTable(self)
        self.table.setModel(self.proxy)
        # dublu click = play; editarea (Mix, Folosire) cu F2
        self.table.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.table.clicked.connect(self._cell_clicked)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        h.setSectionsMovable(True)
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)
        widths = {"check": 30, "artist": 170, "title": 220, "mix": 48, "rating": 70, "bpm": 56, "const": 70,
                  "four": 66, "key": 56, "energy": 56, "pulse": 54, "dropp": 60, "inout": 80, "segment": 90,
                  "use": 130, "family": 110, "genre": 110, "year": 50, "duration": 60}
        for i, k in enumerate(KEYS):
            if k in widths:
                self.table.setColumnWidth(i, widths[k])
        hidden = set(json.loads(settings().value("explore/hidden", "") or '["genre", "year", "folder"]'))
        for i, k in enumerate(KEYS):
            self.table.setColumnHidden(i, k in hidden)
        from .delegates import PillDelegate

        class UsePill(PillDelegate):
            def createEditor(self, parent, option, index):
                cb = QComboBox(parent)
                cb.addItems(["(din analiză)"] + USES)
                cb.activated.connect(lambda _i=0, cb=cb: (self.commitData.emit(cb), self.closeEditor.emit(cb)))
                return cb

            def setEditorData(self, editor, index):
                editor.setCurrentText(index.data(Qt.EditRole) or "(din analiză)")

            def setModelData(self, editor, model, index):
                t = editor.currentText()
                model.setData(index, "" if t.startswith("(") else t, Qt.EditRole)

        self._delegates = [PillDelegate("badge", self.table), PillDelegate("pill", self.table),
                           PillDelegate("dot", self.table), UsePill("pill", self.table)]
        for d, k in zip(self._delegates, ("mix", "family", "key", "use")):
            self.table.setItemDelegateForColumn(KEYS.index(k), d)
        self.table.doubleClicked.connect(lambda ix: self.play(self._row_at(ix)))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        # --- jos: acțiuni ---
        self.n_checked = QLabel("0 bifate")
        self.b_copy = QToolButton()
        self.b_copy.setText("Copiază în ▾")
        self.b_copy.setPopupMode(QToolButton.InstantPopup)
        self.b_copy.setMenu(QMenu(self.b_copy))
        self.b_copy.menu().aboutToShow.connect(lambda: self._fill_dest_menu(self.b_copy.menu(), move=False))
        self.b_move = QToolButton()
        self.b_move.setText("Mută în ▾")
        self.b_move.setPopupMode(QToolButton.InstantPopup)
        self.b_move.setMenu(QMenu(self.b_move))
        self.b_move.menu().aboutToShow.connect(lambda: self._fill_dest_menu(self.b_move.menu(), move=True))
        b_sort = QPushButton("Sortează în foldere…")
        b_sort.clicked.connect(self.sort_into_folders)
        b_del = QPushButton("Șterge")
        b_del.setObjectName("danger")
        b_del.clicked.connect(self.delete_rows)
        b_rev = QPushButton(reveal_label())
        b_rev.clicked.connect(lambda: (lambda r: reveal_in_folder(r.path) if r else None)(self._current()))
        hint = QLabel("X bifă · 1–5 rating · F2 editează Mix / Folosire · C copiază · M mută · Del șterge · Space play")
        hint.setObjectName("muted")
        actions = QHBoxLayout()
        actions.addWidget(self.n_checked)
        for w in (self.b_copy, self.b_move, b_sort, b_rev, b_del):
            actions.addWidget(w)
        actions.addStretch()
        actions.addWidget(hint)
        self.model.dataChanged.connect(lambda *_: self._update_checked())
        self.model.modelReset.connect(self._update_checked)

        # --- player: același obiect ca în Bibliotecă (îl mută fereastra principală între pagini) ---
        self.player = library.player
        self.player.request_next.connect(self._play_next)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)
        self.status = QLabel("")
        self.status.setObjectName("muted")

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(bar)
        cl.addWidget(self.table, 1)
        cl.addLayout(actions)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(center)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 1100])
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(self.status)
        self._lay = lay
        self.rebuild_tree()
        state.tracks_changed.connect(self._library_changed)

    # ===== surse =====
    def folders(self) -> list[str]:
        try:
            return [f for f in json.loads(settings().value("explore/folders", "") or "[]") if f]
        except ValueError:
            return []

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Alege un folder cu muzică (se iau și subfolderele)")
        if not d:
            return
        f = self.folders()
        if d not in f:
            f.append(os.path.normpath(d))
            settings().setValue("explore/folders", json.dumps(f))
        self.rebuild_tree()
        self.show_source(("folder", os.path.normpath(d)))

    def _remove_folder(self, d):
        settings().setValue("explore/folders", json.dumps([x for x in self.folders() if x != d]))
        self._scan_cache.pop(d, None)
        self.rebuild_tree()

    def rebuild_tree(self):
        self.tree.clear()
        lv = self.library

        def add(parent, text, data, bold=False):
            it = QTreeWidgetItem([text])
            it.setData(0, Qt.UserRole, data)
            if bold:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            return it

        add(None, "Toată muzica", ("all",), True)
        libh = add(None, "BIBLIOTECA MANAGER", ("hdr",), True)
        add(libh, f"Toată biblioteca ({len(lv.items)})", ("lib", "all"))
        if any(p.kind == "manual" for p in lv.db.playlists):
            mine = add(libh, "Playlisturile mele", ("hdr",))
            for p in lv.db.playlists:
                if p.kind == "manual":
                    add(mine, f"{p.name} ({len(p.tracks)})", ("lib", "pl", p.id))
        for c in lv.collections:
            if c.playlists and not c.error:
                ext = add(libh, f"Din {c.source}", ("hdr",))
                for name in sorted(c.playlists, key=str.lower):
                    add(ext, f"{name} ({len(c.playlists[name])})", ("lib", "ext", c.source, name))
        fams: dict[str, int] = {}
        for it in lv.items:
            if it.family:
                fams[it.family] = fams.get(it.family, 0) + 1
        if fams:
            fh = add(libh, "După Family", ("hdr",))
            for f in sorted(fams):
                add(fh, f"{f} ({fams[f]})", ("lib", "fam", f))
        libh.setExpanded(True)
        fh = add(None, "FOLDERE DIN CALCULATOR", ("hdr",), True)
        for d in self.folders():
            self._dir_node(fh, d, os.path.basename(d) or d)
        fh.setExpanded(True)
        store = self.download.store
        online = [p for p in store.items]
        if online:
            oh = add(None, "PLAYLISTURI ONLINE", ("hdr",), True)
            for p in online:
                add(oh, f"{p.name} · {'YouTube' if p.source == 'youtube' else 'Spotify'}", ("online", p.id))
            oh.setExpanded(True)
        from .settings_tab import download_dir
        dl = download_dir()
        if dl and os.path.isdir(dl):
            sh = add(None, "DESCĂRCATE DE PE SOULSEEK", ("hdr",), True)
            self._dir_node(sh, dl, f"Toate ({os.path.basename(dl) or dl})").setExpanded(True)
            sh.setExpanded(True)

    # ===== arborele de foldere (subfolderele se citesc când deschizi nodul) =====
    @staticmethod
    def _subdirs(d) -> list[str]:
        try:
            with os.scandir(d) as it:
                return sorted((e.path for e in it if e.is_dir(follow_symlinks=False)
                               and not e.name.startswith((".", "$"))
                               and e.name not in ("System Volume Information",)),
                              key=lambda p: os.path.basename(p).lower())
        except OSError:
            return []

    def _dir_node(self, parent, d, text=None):
        it = QTreeWidgetItem([text or os.path.basename(d) or d])
        it.setData(0, Qt.UserRole, ("folder", d))
        it.setToolTip(0, d)
        parent.addChild(it)
        if self._subdirs(d):
            ph = QTreeWidgetItem(["…"])
            ph.setData(0, Qt.UserRole, ("hdr",))
            it.addChild(ph)
        return it

    def _expand_dir(self, item):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "folder":
            return
        if item.childCount() == 1 and (item.child(0).data(0, Qt.UserRole) or ("",))[0] == "hdr" \
                and item.child(0).text(0) == "…":
            item.takeChild(0)
            for sd in self._subdirs(data[1]):
                self._dir_node(item, sd)

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        data = it.data(0, Qt.UserRole) if it else None
        if not data or data[0] != "folder" or data[1] not in self.folders():
            return
        m = QMenu(self)
        m.addAction("Rescanează folderul", lambda: (self._scan_cache.pop(data[1], None), self.show_source(data)))
        m.addAction("Scoate folderul din Explore", lambda: self._remove_folder(data[1]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _source_clicked(self, item, _col):
        data = item.data(0, Qt.UserRole)
        if data and data[0] != "hdr":
            self.show_source(data)

    def _library_changed(self):
        self.rebuild_tree()
        if self.source[0] in ("lib", "all", "online"):
            self.show_source(self.source)

    def _lib_rows(self, items) -> list[Row]:
        out = []
        for it in items:
            out.append(Row(it.track, it.family, it.bpm, it.camelot or it.key, it.missing or it.offline, it.rating))
        return out

    def _track_rows(self, tracks) -> list[Row]:
        from .dialogs import genre_mapper
        mapper = genre_mapper()
        by = self.library.by_path
        out = []
        for t in tracks:
            it = by.get(_norm(t.path))
            if it is not None:
                out.append(Row(it.track, it.family, it.bpm, it.camelot or it.key, rating=it.rating))
            else:
                from ..core.playlist import to_camelot
                fam = mapper.family_of(t.genre) if t.genre else ""
                b = float(t.bpm) if (t.bpm or "").replace(".", "").isdigit() else 0.0
                out.append(Row(t, fam, b, to_camelot(t.key) or t.key))
        return out

    def _online_rows(self, pid) -> list[Row]:
        p = self.download.store.get(pid)
        if not p:
            return []
        by = self.library.by_path
        known = {_norm(r.path): r for r in self._all_scanned()}
        out = []
        seen = set()
        rows = p.last_rows or []
        for r in rows:
            tdict = r.get("t") or {}
            tid = tdict.get("id", "")
            path = (p.files or {}).get(tid) or r.get("p") or ""
            if path and os.path.exists(path):
                k = _norm(path)
                if k in seen:
                    continue
                seen.add(k)
                it = by.get(k)
                if it is not None:
                    out.append(Row(it.track, it.family, it.bpm, it.camelot or it.key, rating=it.rating))
                    continue
                t = known.get(k).track if k in known else _safe_track(path)
                out.extend(self._track_rows([t]))
            else:
                t = lib.Track(path="", artist=", ".join(tdict.get("artists") or []), title=tdict.get("title", ""),
                              duration=float(tdict.get("duration") or 0))
                out.append(Row(t, "", 0.0, "", missing=True))
        return out

    def _all_scanned(self) -> list[Row]:
        out = []
        for rows in self._scan_cache.values():
            out.extend(rows)
        return out

    def show_source(self, source):
        self.source = source
        kind = source[0]
        lv = self.library
        if kind == "lib":
            sub = source[1]
            if sub == "all":
                title, rows = "Toată biblioteca", self._lib_rows(lv.items)
            elif sub == "pl":
                p = lv.db.get_playlist(source[2])
                title, rows = (p.name if p else "Playlist"), self._lib_rows(lv._resolve(p.tracks) if p else [])
            elif sub == "ext":
                c = next((c for c in lv.collections if c.source == source[2]), None)
                title = f"{source[2]}: {source[3]}"
                rows = self._lib_rows(lv._resolve(c.playlists.get(source[3], [])) if c else [])
            else:
                title, rows = source[2], self._lib_rows([it for it in lv.items if it.family == source[2]])
            self._show(title, rows)
        elif kind == "online":
            p = self.download.store.get(source[1])
            self._show(p.name if p else "Playlist online", self._online_rows(source[1]))
        elif kind == "folder":
            d = source[1]
            if d in self._scan_cache:
                self._show(os.path.basename(d) or d, self._scan_cache[d])
                return
            # subfolder al unui folder deja citit: filtrăm din ce avem
            nd = _norm(d).rstrip("\\/") + os.sep
            for c, rows in list(self._scan_cache.items()):
                if nd.startswith(_norm(c).rstrip("\\/") + os.sep):
                    sub = [r for r in rows if _norm(r.path).startswith(nd)]
                    self._show(os.path.basename(d) or d, sub)
                    return
            self._scan(d)
        else:      # toată muzica: biblioteca + folderele Explore + download (fără dubluri)
            rows, seen = [], set()
            for r in self._lib_rows(lv.items) + self._all_scanned():
                k = _norm(r.path)
                if k not in seen and not r.missing:
                    seen.add(k)
                    rows.append(r)
            self._show("Toată muzica", rows)
            for d in self.folders():
                if d not in self._scan_cache:
                    self._scan(d, then_show=False)

    def _scan(self, d, then_show=True):
        from .jobs import Job
        cache = lib.LibraryCache(os.path.join(data_dir(), "explore_cache.json"))

        def work(progress, cancelled):
            return lib.scan([d], cache, lambda a, b: progress(a, b, f"Citesc {os.path.basename(d)}: {a}/{b}"),
                            cancelled)

        job = Job(f"Explore: citesc {os.path.basename(d) or d}", work, writes=False)
        job.progress.connect(lambda a, b, txt: self.status.setText(txt))

        def done(tracks):
            self._scan_cache[d] = self._track_rows(tracks)
            self.status.setText(f"{len(tracks)} piese în {d}")
            if then_show and self.source == ("folder", d):
                self._show(os.path.basename(d) or d, self._scan_cache[d])
            elif self.source == ("all",):
                self.show_source(("all",))
        job.succeeded.connect(done)
        job.failed.connect(lambda m: self.status.setText("Eroare: " + m))
        if then_show:
            self.title.setText(os.path.basename(d) or d)
            self.model.set_rows([])
            self.status.setText("Citesc folderul…")
        job.start()

    def _show(self, title, rows):
        md = mix_dir()
        for r in rows:
            if r.mix is None and r.path:
                r.mix = mx.load_cached(r.path, md)
        self.title.setText(title)
        self.model.set_rows(rows)
        done = sum(1 for r in rows if r.mix is not None)
        self.count.setText(f"{len(rows)} piese · {done} analizate")

    # ===== filtre =====
    def _set_filter(self, key, on):
        setattr(self.proxy, key, on)
        self.proxy.invalidateFilter()

    def _search(self, text):
        self.proxy.words = lib.normalize(text).split()
        self.proxy.invalidateFilter()

    def _header_menu(self, pos):
        m = QMenu(self)
        m.addAction("Coloane afișate:").setEnabled(False)
        for i, (title, key) in enumerate(COLS):
            if key == "check":
                continue
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: (self.table.setColumnHidden(i, not on), self._save_hidden()))
        m.exec(self.table.horizontalHeader().viewport().mapToGlobal(pos))

    def _save_hidden(self):
        settings().setValue("explore/hidden", json.dumps([k for i, k in enumerate(KEYS) if self.table.isColumnHidden(i)]))

    # ===== analiză =====
    def analyze_visible(self, rows=None):
        if isinstance(rows, bool):  # semnalul clicked(bool)
            rows = None
        rows = rows if rows is not None else [self._row_at(self.proxy.index(i, 0)) for i in range(self.proxy.rowCount())]
        todo = [r for r in rows if r and r.path and not r.missing and r.mix is None]
        if not todo:
            QMessageBox.information(self, "Analiză", "Piesele afișate sunt deja analizate.")
            return
        self.analyze_rows(todo)

    def analyze_rows(self, todo, force=False):
        from .jobs import Job
        paths = [r.path for r in todo]
        refs = {r.path: float(r.bpm or 0) for r in todo}      # BPM-ul din Bibliotecă (Traktor / Rekordbox / al tău)
        md, ad = mix_dir(), analysis_dir()

        def work(progress, cancelled):
            for i, p in enumerate(paths):
                if cancelled():
                    break
                if os.path.exists(p):
                    mx.analyze(p, md, ad, force=force, ref_bpm=refs.get(p, 0.0))
                progress(i + 1, len(paths), p)
            return len(paths)

        job = Job(f"Analiză mixabilitate ({len(paths)})", work, writes=False)
        job.progress.connect(self._analyzed_one)
        job.succeeded.connect(lambda n: self.status.setText(f"Analiza s-a terminat ({n} piese)."))
        job.start()

    def _analyzed_one(self, done, total, path):
        m = mx.load_cached(path, mix_dir())
        for r in self.model.rows:
            if r.path == path:
                r.mix = m
        self.model.refresh(path)
        self.status.setText(f"Analizez mixabilitatea… {done}/{total}")
        n = sum(1 for r in self.model.rows if r.mix is not None)
        self.count.setText(f"{len(self.model.rows)} piese · {n} analizate")
        if path == self.player.path and m:
            self.player.wave.set_segment(m.seg_start, m.seg_end)

    # ===== player =====
    def _row_at(self, proxy_index) -> Row | None:
        if not proxy_index.isValid():
            return None
        return self.model.rows[self.proxy.mapToSource(proxy_index).row()]

    def _current(self) -> Row | None:
        return self._row_at(self.table.currentIndex())

    def play(self, r: Row | None, from_segment=False):
        if r is None or r.missing or not r.path or not os.path.exists(r.path):
            return
        a = an.load_cached(r.path, analysis_dir())
        t = r.track
        m = r.mix if (r.mix and not r.mix.error) else None
        info = " · ".join(x for x in (r.family or t.genre, t.year, r.key, f"Mix {m.score}" if m else "") if x)
        self.player.load(r.path, f"{t.artist} - {t.display_title}", info, t.duration, a,
                         bpm=r.bpm or (m.bpm if m else 0))
        if m:
            self.player.wave.set_segment(m.seg_start, m.seg_end)
            if from_segment and m.seg_start > 0 and t.duration:
                self.player.seek_abs(m.seg_start / t.duration)
        self.model.playing = r.path
        self.table.viewport().update()

    def attach_player(self):
        """Playerul comun vine în pagina asta (sub tabel), cu tot ce cântă."""
        if self._lay.indexOf(self.player) < 0:
            self._lay.insertWidget(1, self.player)
        self.focus_playing()

    def selected_paths(self) -> list[str]:
        return [r.path for r in self._selected() if r.path and not r.missing]

    def focus_paths(self, paths):
        """Selectează piesele venite din altă pagină; dacă nu sunt în lista afișată, trec pe „Toată muzica”."""
        want = {_norm(p) for p in paths}

        def rows():
            return [i for i in range(self.proxy.rowCount())
                    if (lambda r: r is not None and r.path and _norm(r.path) in want)(self._row_at(self.proxy.index(i, 0)))]
        found = rows()
        if not found and self.source != ("all",):
            self.show_source(("all",))
            found = rows()
        if not found and (self.search.text() or any(b.isChecked() for b in self.filters.values())):
            self.search.clear()
            for b in self.filters.values():
                b.setChecked(False)
            found = rows()
        if not found:
            self.status.setText("Piesa selectată nu e în lista din Explore.")
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        selm = self.table.selectionModel()
        sel = QItemSelection()
        for i in found:
            sel.select(self.proxy.index(i, 0), self.proxy.index(i, len(COLS) - 1))
        selm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        ix = self.proxy.index(found[0], 1)
        selm.setCurrentIndex(ix, QItemSelectionModel.NoUpdate)
        self.table.scrollTo(ix, QAbstractItemView.PositionAtCenter)
        self.table.setFocus()

    def focus_playing(self):
        """Selectează piesa care cântă, dacă e în lista afișată."""
        p = self.player.path
        if not p:
            return
        self.model.playing = p
        k = _norm(p)
        for i in range(self.proxy.rowCount()):
            r = self._row_at(self.proxy.index(i, 0))
            if r and r.path and _norm(r.path) == k:
                ix = self.proxy.index(i, 1)
                self.table.setCurrentIndex(ix)
                self.table.selectRow(i)
                self.table.scrollTo(ix, QAbstractItemView.PositionAtCenter)
                break
        self.table.viewport().update()

    def toggle_play(self):
        r = self._current()
        if r and r.path == self.player.path:
            self.player.toggle()
        else:
            self.play(r)

    def _row_changed(self, cur, _prev):
        # click (stânga/dreapta) doar selectează; autoplay numai cu ↑/↓ cât cântă ceva
        if cur.isValid() and self.table.kbd_nav and self.player.is_playing():
            r = self._row_at(cur)
            if r and r.path != self.player.path:
                self.play(r, from_segment=True)

    def _play_next(self, step):
        if not self.isVisible():
            return
        rows = [self._row_at(self.proxy.index(i, 0)) for i in range(self.proxy.rowCount())]
        idx = next((i for i, r in enumerate(rows) if r and r.path == self.player.path), -1)
        nxt = idx + step
        if 0 <= nxt < len(rows):
            self.table.selectRow(nxt)
            self.play(rows[nxt])

    # ===== bife =====
    def _selected(self) -> list[Row]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedIndexes()})
        return [r for r in (self._row_at(self.proxy.index(i, 0)) for i in rows) if r]

    def toggle_checks(self):
        sel = self._selected()
        if not sel:
            return
        on = not all(r.checked for r in sel)
        for r in sel:
            r.checked = on
            self.model.refresh(r.path)
        self._update_checked()

    def checked_rows(self) -> list[Row]:
        rows = [r for r in self.model.rows if r.checked and r.path and not r.missing]
        return rows or [r for r in self._selected() if r.path and not r.missing]

    def _update_checked(self):
        n = sum(1 for r in self.model.rows if r.checked)
        self.n_checked.setText(f"{n} bifate" if n else "0 bifate (se folosește selecția)")

    # ===== meniul pieselor =====
    def _table_menu(self, pos):
        sel = self._selected()
        if not sel:
            return
        m = QMenu(self)
        m.addAction("▶ Redă", lambda: self.play(sel[0]))
        m.addAction("Bifează / debifează (X)", self.toggle_checks)
        m.addSeparator()
        m.addAction("Analizează mixabilitatea", lambda: self.analyze_rows([r for r in sel if r.path], force=True))
        rm = m.addMenu("Rating")
        for n in range(5, -1, -1):
            rm.addAction(("★" * n) if n else "Fără rating (0)", lambda n=n: self.set_rating(sel, n))
        if any(r.manual() for r in sel):
            m.addAction("Revino la analiză (Mix / Folosire)", lambda: self.reset_manual(sel))
        bm = m.addMenu("BPM")
        with_bpm = [r for r in sel if r.path and (r.bpm or (r.mix and r.mix.bpm))]
        bm.addAction("×2", lambda: self.scale_bpm(with_bpm, 2))
        bm.addAction("½", lambda: self.scale_bpm(with_bpm, 0.5))
        bm.setEnabled(bool(with_bpm))
        m.addSeparator()
        cm = m.addMenu("Copiază în")
        self._fill_dest_menu(cm, move=False)
        mm = m.addMenu("Mută în")
        self._fill_dest_menu(mm, move=True)
        m.addAction("Sortează în foldere…", self.sort_into_folders)
        m.addAction(reveal_label(), lambda: reveal_in_folder(sel[0].path))
        from .dialogs import browser_search_menu
        browser_search_menu(m, sel[0].track.artist, sel[0].track.display_title)
        m.addSeparator()
        m.addAction("🗑 Șterge din calculator…", self.delete_rows)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _cell_clicked(self, ix):
        """Click pe stelele din coloana Rating: atâtea stele câte până unde ai dat click."""
        if KEYS[ix.column()] != "rating":
            return
        r = self._row_at(ix)
        if r is None or not r.path or r.missing:
            return
        rect = self.table.visualRect(ix)
        pos = self.table.viewport().mapFromGlobal(QCursor.pos())
        star_w = max(8, self.table.fontMetrics().horizontalAdvance("★"))
        n = max(1, min(5, int((pos.x() - rect.left() - 2) // star_w) + 1))
        self.set_rating([r], 0 if n == r.rating else n)

    def set_rating(self, rows, n):
        """Ratingul se scrie în fișier (merge cu piesa la copiere / mutare); dacă e în Bibliotecă, și acolo."""
        from .dialogs import write_tags_in_background
        rows = [r for r in rows if r and r.path and not r.missing]
        if not rows:
            return
        lib_items, jobs = [], []
        for r in rows:
            r.rating = n
            it = self.library.by_path.get(_norm(r.path))
            if it is not None:
                lib_items.append(it)
            else:
                r.track.rating = n
                jobs.append((r.path, {"rating": str(n)}, None))
            self.model.refresh(r.path)
        if lib_items:
            self.library.set_rating(lib_items, n)
        if jobs:
            write_tags_in_background(self, jobs, None, f"Scriu ratingul ({n}★)")
        self.status.setText(f"Rating {('★' * n) or '0'} la {len(rows)} piese (scris în fișier).")

    def reset_manual(self, rows):
        for r in rows:
            manual().pop(_norm(r.path), None)
            self.model.refresh(r.path)
        save_manual()

    def scale_bpm(self, rows, factor):
        """×2 / ½: se scrie în fișier; dacă piesa e în Bibliotecă, și acolo."""
        from .dialogs import write_tags_in_background
        lib_items, jobs = [], []
        for r in rows:
            b = r.bpm or (r.mix.bpm if r.mix else 0)
            if not b:
                continue
            nb = round(b * factor, 2)
            r.bpm = nb
            it = self.library.by_path.get(_norm(r.path))
            if it is not None:
                lib_items.append(it)
            else:
                jobs.append((r.path, {"bpm": str(int(round(nb)))}, None))
            self.model.refresh(r.path)
        if lib_items:
            self.library.scale_bpm(lib_items, factor)
        if jobs:
            write_tags_in_background(self, jobs, None, "Scriu BPM-ul")

    # ===== copiază / mută =====
    def _fill_dest_menu(self, menu, move: bool):
        menu.clear()
        verb = "Mută" if move else "Copiază"
        menu.addAction("BIBLIOTECA MANAGER").setEnabled(False)
        for d in self.library.library_folders():
            menu.addAction(f"Folderul bibliotecii: {os.path.basename(d) or d}",
                           lambda d=d: self.transfer(d, move, to_library=True))
        if not move:
            for p in self.library.db.playlists:
                if p.kind == "manual":
                    menu.addAction(f"Playlist: {p.name}", lambda pid=p.id: self.add_to_playlist(pid))
            menu.addAction("+ Playlist nou…", self.add_to_new_playlist)
        menu.addSeparator()
        menu.addAction("PE HARD").setEnabled(False)
        last = settings().value("explore/last_dest", "") or ""
        if last:
            menu.addAction(f"{last} (ultimul)", lambda: self.transfer(last, move))
        menu.addAction("Alege folder…", lambda: self._pick_and_transfer(move))
        menu.setTitle(verb)

    def _pick_and_transfer(self, move):
        d = QFileDialog.getExistingDirectory(self, "Unde le pun?")
        if d:
            settings().setValue("explore/last_dest", os.path.normpath(d))
            self.transfer(os.path.normpath(d), move)

    def copy_to_last(self):
        last = settings().value("explore/last_dest", "") or ""
        self.transfer(last, False) if last else self._pick_and_transfer(False)

    def move_to_last(self):
        last = settings().value("explore/last_dest", "") or ""
        self.transfer(last, True) if last else self._pick_and_transfer(True)

    def _local_then(self, rows, then):
        """Piesele din afara bibliotecii (stick, alt disc): întâi le copiez / mut pe hard, apoi then(căi)."""
        outside = [r for r in rows if _norm(r.path) not in self.library.by_path]
        if not outside:
            then([r.path for r in rows])
            return
        box = QMessageBox(self)
        box.setWindowTitle("Playlist")
        box.setText(f"{len(outside)} din {len(rows)} piese nu sunt pe hard, în biblioteca ta (ex. sunt pe stick).\n\n"
                    "Playlistul ține doar trimiterea la fișier: dacă scoți stick-ul, piesa apare ca lipsă.")
        b_copy = box.addButton("Copiază-le pe hard", QMessageBox.AcceptRole)
        b_move = box.addButton("Mută-le pe hard", QMessageBox.AcceptRole)
        b_keep = box.addButton("Doar adaugă-le așa", QMessageBox.AcceptRole)
        box.addButton("Renunță", QMessageBox.RejectRole)
        last = settings().value("explore/usb_choice", "copy") or "copy"
        box.setDefaultButton({"copy": b_copy, "move": b_move, "keep": b_keep}.get(last, b_copy))
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_copy, b_move, b_keep):
            return
        choice = "copy" if clicked is b_copy else "move" if clicked is b_move else "keep"
        settings().setValue("explore/usb_choice", choice)
        if choice == "keep":
            then([r.path for r in rows])
            return
        folders = self.library.library_folders()
        if not folders:
            QMessageBox.information(self, "Playlist", "N-ai niciun folder de bibliotecă (Setări → Foldere).")
            return
        dest = folders[0]
        if len(folders) > 1:
            from PySide6.QtWidgets import QInputDialog
            prev = settings().value("explore/usb_dest", "") or ""
            idx = folders.index(prev) if prev in folders else 0
            dest, ok = QInputDialog.getItem(self, "Unde le pun?", "Folderul bibliotecii:", folders, idx, False)
            if not ok:
                return
        settings().setValue("explore/usb_dest", dest)
        out_keys = {_norm(r.path) for r in outside}

        def after(moves):
            new = {_norm(a): b for a, b in moves}
            then([new.get(_norm(r.path), r.path) if _norm(r.path) in out_keys else r.path for r in rows])
        self.transfer(dest, choice == "move", to_library=True, rows=outside, then=after, confirm=False)

    def add_to_playlist(self, pid):
        rows = self.checked_rows()
        if not rows:
            return

        def add(paths):
            self.library.add_to_playlist(pid, paths)
            self.status.setText(f"Am adăugat {len(paths)} piese în playlist.")
        self._local_then(rows, add)

    def add_to_new_playlist(self):
        rows = self.checked_rows()
        if rows:
            self._local_then(rows, lambda paths: self.library.new_playlist(paths))

    def transfer(self, dest: str, move: bool, to_library=False, rows=None, plan=None, then=None, confirm=True):
        """Copiază / mută fișierele (în fundal, ca activitate). plan = [(sursă, folder destinație)]."""
        rows = rows if rows is not None else self.checked_rows()
        if plan is None:
            if not rows or not dest:
                return
            plan = [(r.path, dest) for r in rows]
        if not plan:
            return
        verb = "Mut" if move else "Copiez"
        n_lib = sum(1 for s, _d in plan if _norm(s) in self.library.by_path)
        q = f"{verb} {len(plan)} fișiere în {dest or 'folderele alese'}?"
        if move and n_lib:
            q += (f"\n\n{n_lib} sunt în Bibliotecă (poate și în Traktor). Playlisturile din Manager le urmează; "
                  "în Traktor va trebui să le regăsești cu Relocate.")
        if confirm and QMessageBox.question(self, verb, q) != QMessageBox.Yes:
            return
        from .jobs import Job

        def work(progress, cancelled):
            moves, errors = [], []
            for i, (src, d) in enumerate(plan):
                if cancelled():
                    break
                try:
                    os.makedirs(d, exist_ok=True)
                    target = lib.unique_path(os.path.join(d, os.path.basename(src)))
                    if lib.same_file(os.path.dirname(src), d):
                        continue
                    if move:
                        shutil.move(src, target)
                    else:
                        shutil.copy2(src, target)
                    moves.append((src, target))
                except OSError as e:
                    errors.append(f"{os.path.basename(src)}: {e.strerror or e}")
                progress(i + 1, len(plan), os.path.basename(src))
            return moves, errors

        job = Job(f"Explore: {verb.lower()} {len(plan)} fișiere", work)

        def done(res):
            moves, errors = res
            if move:
                after_moves(moves)
                for r in self.model.rows:
                    for a, b in moves:
                        if r.path == a:
                            r.track.path = b
            if to_library or any(lib.same_file(os.path.dirname(b), f) or
                                 _norm(b).startswith(_norm(f) + os.sep) for _a, b in moves[:1]
                                 for f in self.library.library_folders()):
                self.library.reload(quiet=True)
            for d in {d for _s, d in plan}:
                for k in list(self._scan_cache):
                    if _norm(d).startswith(_norm(k)) or _norm(k).startswith(_norm(d)):
                        self._scan_cache.pop(k, None)
            self.status.setText(f"{'Mutate' if move else 'Copiate'}: {len(moves)} fișiere."
                                + (f" Erori: {len(errors)}" if errors else ""))
            if errors:
                QMessageBox.warning(self, "Erori", "\n".join(errors[:15]))
            self.model.layoutChanged.emit()
            if then:
                then(moves)
        job.succeeded.connect(done)
        job.start()

    # ===== sortează în foldere =====
    def sort_into_folders(self):
        rows = [r for r in self.checked_rows()] or \
               [self._row_at(self.proxy.index(i, 0)) for i in range(self.proxy.rowCount())]
        rows = [r for r in rows if r and r.path and not r.missing]
        if not rows:
            return
        # aceeași fereastră ca în Curățenie și Bibliotecă
        from .sort_tool import items_from_explore, open_sort
        dlg = open_sort(items_from_explore(rows), self.library, self, self.title.text())
        if dlg is not None:
            dlg.on_applied = lambda: (self._scan_cache.clear(), self.show_source(self.source))

    # ===== ștergere =====
    def delete_rows(self):
        rows = self.checked_rows()
        if not rows:
            return
        in_lib = sum(1 for r in rows if _norm(r.path) in self.library.by_path)
        q = f"Trimit {len(rows)} fișiere în Coșul de gunoi?"
        if in_lib:
            q += f"\n\n{in_lib} sunt în Bibliotecă / playlisturi și vor apărea acolo ca lipsă."
        q += "\n\nPe discurile fără Coș (stick, card, rețea) nu le șterg: le mut în „_De șters” lângă ele."
        if QMessageBox.question(self, "Șterge", q) != QMessageBox.Yes:
            return
        from ..core.trash import split_by_bin, to_trash
        from .jobs import Job
        paths = [r.path for r in rows]

        def work(progress, cancelled):
            ok, no = split_by_bin(paths)
            done, errors = to_trash(ok)
            moved = []
            for p in no:
                try:
                    d = os.path.join(os.path.dirname(p), "_De șters")
                    os.makedirs(d, exist_ok=True)
                    t = lib.unique_path(os.path.join(d, os.path.basename(p)))
                    shutil.move(p, t)
                    moved.append((p, t))
                except OSError as e:
                    errors.append(f"{os.path.basename(p)}: {e.strerror or e}")
            return done, moved, errors

        job = Job(f"Explore: șterg {len(paths)} fișiere", work)

        def finished(res):
            done, moved, errors = res
            gone = set(done) | {a for a, _b in moved}
            self.model.set_rows([r for r in self.model.rows if r.path not in gone])
            for k, v in list(self._scan_cache.items()):
                self._scan_cache[k] = [r for r in v if r.path not in gone]
            text = f"Am trimis {len(done)} fișiere în Coș."
            if moved:
                text += f" {len(moved)} (de pe discuri fără Coș) sunt în „_De șters”."
            self.status.setText(text)
            if errors:
                QMessageBox.warning(self, "Erori", "\n".join(errors[:15]))
            if any(_norm(p) in self.library.by_path for p in gone):
                self.library.reload(quiet=True)
        job.succeeded.connect(finished)
        job.start()


def _norm(p: str) -> str:
    from ..core.djcollections import norm_path
    return norm_path(p)


def _safe_track(path: str):
    try:
        return lib.track_from_file(path)
    except OSError:
        return lib.Track(path=path)
