"""Sortează în foldere (Curățenie; scurtături din Bibliotecă și Explore).

Pune fișierele în foldere după Familie → Gen (implicit), doar Familie, Familie → Stil sau alte niveluri,
într-un folder ales (orice disc). Copiază sau Mută, cu bife pe fiecare piesă și previzualizarea arborelui.
Dacă în destinație există deja un fișier cu același nume: merge în „_Dubluri” acolo.
Se poate anula din Curățenie („Anulează ultima operație”); Biblioteca și playlisturile urmează fișierele.
"""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import library as lib
from ..core.djcollections import norm_path
from .common import add_space_toggle, after_moves, settings, undo_dir
from .dialogs import _item, _table

LEVELS = [("Familie", "family"), ("Gen", "genre"), ("Stil (din Comentariu)", "style"), ("Artist", "artist"),
          ("An", "year"), ("Label", "label"), ("BPM (din 10 în 10)", "bpm10"), ("Key", "key"),
          ("Folosire (Explore)", "use"), ("Mix (≥7 / restul)", "mixband")]
PRESETS = [("Familie → Gen", ("family", "genre")), ("Doar Familie", ("family", "")),
           ("Familie → Stil", ("family", "style")), ("Personalizat", None)]
EMPTY = {"family": "_Nerecunoscute", "genre": "_Fără gen", "style": "_Fără stil", "artist": "_Fără artist",
         "year": "_Fără an", "label": "_Fără label", "bpm10": "_Fără BPM", "key": "_Fără Key",
         "use": "_Neanalizate", "mixband": "_Neanalizate"}


class SortItem:
    """O piesă de sortat: calea și valorile pe care le poți folosi ca niveluri."""
    __slots__ = ("path", "title", "values", "checked")

    def __init__(self, path, title, values):
        self.path, self.title, self.values = path, title, values
        self.checked = True

    def value(self, key) -> str:
        return (self.values.get(key) or "").strip() or EMPTY.get(key, "_Necunoscut")


def _bpm10(b) -> str:
    try:
        b = float(b or 0)
    except ValueError:
        b = 0
    return f"{int(b // 10 * 10)}-{int(b // 10 * 10) + 10}" if b else ""


def items_from_library(lib_items) -> list[SortItem]:
    from ..core.libdb import parse_comment
    out = []
    for it in lib_items:
        if it.missing or it.offline:
            continue
        t = it.track
        style = (parse_comment(t.comment)[0] or "").split(",")[0]
        out.append(SortItem(it.path, f"{t.artist} - {t.display_title}", {
            "family": it.family, "genre": t.genre, "style": style, "artist": t.artist.split(",")[0],
            "year": t.year, "label": t.label, "bpm10": _bpm10(it.bpm), "key": it.camelot or it.key}))
    return out


def items_from_tracks(tracks, library=None) -> list[SortItem]:
    """Piese din Curățenie / foldere oarecare; familia vine din Bibliotecă dacă piesa e acolo, altfel din gen."""
    from ..core.libdb import parse_comment
    from .dialogs import genre_mapper
    mapper = genre_mapper()
    by = getattr(library, "by_path", {}) or {}
    out = []
    for t in tracks:
        it = by.get(norm_path(t.path))
        if it is not None:
            out.extend(items_from_library([it]))
            continue
        style = (parse_comment(t.comment)[0] or "").split(",")[0]
        fam = mapper.family_of(t.genre) if t.genre else (mapper.family_of(style) if style else "")
        out.append(SortItem(t.path, f"{t.artist} - {t.display_title}", {
            "family": fam, "genre": t.genre, "style": style, "artist": t.artist.split(",")[0], "year": t.year,
            "label": t.label, "bpm10": _bpm10(t.bpm), "key": t.key}))
    return out


def items_from_explore(rows) -> list[SortItem]:
    from ..core.libdb import parse_comment
    out = []
    for r in rows:
        if not r or not r.path or r.missing:
            continue
        t = r.track
        style = (parse_comment(t.comment)[0] or "").split(",")[0]
        out.append(SortItem(r.path, f"{t.artist} - {t.display_title}", {
            "family": r.family, "genre": t.genre, "style": style, "artist": t.artist.split(",")[0],
            "year": t.year, "label": t.label, "bpm10": _bpm10(r.bpm or (r.mix.bpm if r.mix else 0)),
            "key": r.key, "use": r.use, "mixband": ("" if r.score < 0 else ("Mix 7+" if r.score >= 7 else "Mix sub 7"))}))
    return out


class SortFoldersDialog(QDialog):
    def __init__(self, items: list[SortItem], library=None, parent=None, title_hint=""):
        super().__init__(parent)
        self.items = items
        self.library = library
        self.on_applied = None
        self.setWindowTitle(f"Sortează în foldere — {len(items)} piese" + (f" ({title_hint})" if title_hint else ""))
        self.resize(1200, 720)
        self.dest_edit = QLineEdit(settings().value("sort/dest", "") or "")
        self.dest_edit.setPlaceholderText("Folderul în care se fac subfolderele (orice disc)")
        self.dest_edit.textChanged.connect(self._refresh)
        b_pick = QPushButton("Alege…")
        b_pick.clicked.connect(self._pick)
        drow = QHBoxLayout()
        drow.addWidget(self.dest_edit, 1)
        drow.addWidget(b_pick)
        self.preset = QComboBox()
        for label, lv in PRESETS:
            self.preset.addItem(label, lv)
        self.l1, self.l2 = QComboBox(), QComboBox()
        self.l2.addItem("— nimic —", "")
        for label, key in LEVELS:
            self.l1.addItem(label, key)
            self.l2.addItem(label, key)
        self.l2.setCurrentIndex(self.l2.findData("genre"))
        self.preset.currentIndexChanged.connect(self._preset)
        for w in (self.l1, self.l2):
            w.currentIndexChanged.connect(self._levels_changed)
        lv = QHBoxLayout()
        lv.addWidget(self.preset)
        lv.addSpacing(12)
        lv.addWidget(QLabel("Nivelul 1:"))
        lv.addWidget(self.l1)
        lv.addWidget(QLabel("Nivelul 2:"))
        lv.addWidget(self.l2)
        lv.addStretch()
        self.rb_copy = QRadioButton("Copiază (originalele rămân)")
        self.rb_move = QRadioButton("Mută (pleacă de unde erau)")
        (self.rb_move if settings().value("sort/move", False) in (True, "true") else self.rb_copy).setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_copy)
        g.addButton(self.rb_move)
        mv = QHBoxLayout()
        mv.addWidget(self.rb_copy)
        mv.addWidget(self.rb_move)
        mv.addStretch()
        form = QFormLayout()
        form.addRow("Destinație:", drow)
        form.addRow("Structura:", lv)
        form.addRow("", mv)

        self.table = _table(["", "Piesă", "Merge în"], (28, 420))
        add_space_toggle(self.table, 0)
        self.table.setRowCount(len(items))
        for i, it in enumerate(items):
            self.table.setItem(i, 0, _item("", check=True))
            self.table.setItem(i, 1, _item(it.title, tooltip=it.path))
        self.table.itemChanged.connect(lambda *_: self._refresh_tree())
        b_all, b_none = QPushButton("Bifează tot"), QPushButton("Debifează tot")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        sel = QHBoxLayout()
        sel.addWidget(b_all)
        sel.addWidget(b_none)
        sel.addStretch()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(sel)
        ll.addWidget(self.table, 1)
        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(["Folderele care se fac", "Piese"])
        self.preview.setColumnWidth(0, 300)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.preview)
        split.setSizes([760, 420])
        note = QLabel("Fișierele își păstrează numele. Dacă în destinație există deja unul cu același nume, merge în "
                      "„_Dubluri” acolo. Se poate anula din Curățenie → „Anulează ultima operație”. Biblioteca și "
                      "playlisturile Manager urmează fișierele mutate; în Traktor le regăsești cu Relocate.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        self.count = QLabel()
        b_go = QPushButton("Sortează bifatele")
        b_go.setObjectName("primary")
        b_go.clicked.connect(self.apply)
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.count, 1)
        row.addWidget(close)
        row.addWidget(b_go)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(split, 1)
        lay.addWidget(note)
        lay.addLayout(row)
        self._preset()

    # --- niveluri -----------------------------------------------------------
    def _preset(self, *_):
        lv = self.preset.currentData()
        if lv:
            for w, k in ((self.l1, lv[0]), (self.l2, lv[1])):
                w.blockSignals(True)
                w.setCurrentIndex(max(0, w.findData(k)))
                w.blockSignals(False)
        self._refresh()

    def _levels_changed(self, *_):
        cur = (self.l1.currentData(), self.l2.currentData())
        i = next((n for n, (_l, lv) in enumerate(PRESETS) if lv == cur), len(PRESETS) - 1)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(i)
        self.preset.blockSignals(False)
        self._refresh()

    def _parts(self, it: SortItem) -> list[str]:
        out = [lib.safe_name(it.value(self.l1.currentData())) or "_Necunoscut"]
        if self.l2.currentData():
            out.append(lib.safe_name(it.value(self.l2.currentData())) or "_Necunoscut")
        return out

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Unde fac folderele?", self.dest_edit.text())
        if d:
            self.dest_edit.setText(os.path.normpath(d))

    def _set_all(self, on):
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._refresh_tree()

    def checked(self) -> list[SortItem]:
        return [it for i, it in enumerate(self.items) if self.table.item(i, 0).checkState() == Qt.Checked]

    def _refresh(self, *_):
        dest = self.dest_edit.text().strip()
        self.table.blockSignals(True)
        for i, it in enumerate(self.items):
            rel = os.path.join(*self._parts(it))
            self.table.setItem(i, 2, _item(os.path.join(dest, rel) if dest else rel, "gray"))
        self.table.blockSignals(False)
        self._refresh_tree()

    def _refresh_tree(self):
        self.preview.clear()
        tree: dict = {}
        chosen = self.checked()
        for it in chosen:
            node = tree
            for part in self._parts(it):
                node = node.setdefault(part, {"__n": 0})
                node["__n"] += 1

        def add(parent, d):
            for k in sorted((x for x in d if x != "__n"), key=str.lower):
                w = QTreeWidgetItem([k, str(d[k]["__n"])])
                (parent.addChild(w) if parent else self.preview.addTopLevelItem(w))
                add(w, d[k])
        add(None, tree)
        self.preview.expandToDepth(0)
        self.count.setText(f"<b>{len(chosen)}</b> bifate din {len(self.items)} · {len(tree)} foldere de nivel 1")

    def plan(self, dest) -> list[tuple[str, str]]:
        return [(it.path, os.path.join(dest, *self._parts(it))) for it in self.checked()]

    # --- aplicare -------------------------------------------------------------
    def apply(self):
        dest = self.dest_edit.text().strip()
        if not dest:
            QMessageBox.information(self, "Sortează", "Alege folderul de destinație.")
            return
        plan = self.plan(dest)
        if not plan:
            QMessageBox.information(self, "Sortează", "N-ai bifat nicio piesă.")
            return
        move = self.rb_move.isChecked()
        settings().setValue("sort/dest", dest)
        settings().setValue("sort/move", move)
        verb = "Mut" if move else "Copiez"
        if QMessageBox.question(self, "Sortează", f"{verb} {len(plan)} fișiere în foldere sub\n{dest}?") \
                != QMessageBox.Yes:
            return
        if move:
            from .player import free_files
            free_files([s for s, _d in plan])
        from .jobs import Job

        def work(progress, cancelled):
            moves, errors, dups = [], [], 0
            taken: set[str] = set()
            for i, (src, d) in enumerate(plan):
                if cancelled():
                    break
                try:
                    os.makedirs(d, exist_ok=True)
                    target = os.path.join(d, os.path.basename(src))
                    if os.path.exists(target) or os.path.normcase(target) in taken:
                        if lib.same_file(src, target):
                            continue
                        target = lib.duplicates_dest(target, target, taken)   # „_Dubluri” în folderul ăla
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        dups += 1
                    taken.add(os.path.normcase(target))
                    if move:
                        shutil.move(src, target)
                    else:
                        shutil.copy2(src, target)
                    moves.append((src, target))
                except OSError as e:
                    errors.append(f"{os.path.basename(src)}: {e.strerror or e}")
                progress(i + 1, len(plan), os.path.basename(src))
            if move and moves:
                lib._write_undo_log(undo_dir(), "sortare", moves)
            return moves, errors, dups

        job = Job(f"Sortez în foldere ({len(plan)} fișiere)", work)
        parent = self.parent()
        lib_view = self.library
        on_applied = self.on_applied

        def done(res):
            moves, errors, dups = res
            if move:
                after_moves(moves)
            msg = f"{'Mutate' if move else 'Copiate'} {len(moves)} fișiere în foldere."
            if dups:
                msg += f" {dups} existau deja acolo → „_Dubluri”."
            if errors:
                msg += f"\n\n{len(errors)} erori:\n" + "\n".join(errors[:12])
            QMessageBox.information(parent, "Sortează în foldere", msg)
            if lib_view is not None:
                folders = lib_view.library_folders()
                inside = any(norm_path(dest).startswith(norm_path(f).rstrip("\\/") + os.sep) or
                             norm_path(dest) == norm_path(f) for f in folders)
                if not inside and QMessageBox.question(
                        parent, "Bibliotecă", f"„{dest}” nu e un folder al bibliotecii. Îl adaug, ca piesele "
                        "sortate să apară în Bibliotecă?") == QMessageBox.Yes:
                    lib_view.state.set_folders(list(lib_view.state.folders) + [dest])
                lib_view.reload(quiet=True)
            if on_applied:
                on_applied()
        job.succeeded.connect(done)
        job.start()
        self.accept()


def open_sort(items, library=None, parent=None, title_hint=""):
    if not items:
        QMessageBox.information(parent, "Sortează în foldere", "Nu am piese de sortat (sau lipsesc de pe disc).")
        return None
    dlg = SortFoldersDialog(items, library, parent, title_hint)
    from .windows import tool_window
    tool_window(dlg)
    return dlg
