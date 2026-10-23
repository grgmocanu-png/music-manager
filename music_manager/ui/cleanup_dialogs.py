"""Ferestrele din Curățenie: dubluri, redenumire, curățarea numelor.

Toate au: bife pe fiecare rând, „Bifează tot” / „Debifează tot”, filtre pe stare,
motivul fiecărei probleme și istoricul (ce s-a făcut deja nu se mai bifează implicit).
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import library as lib
from ..core.namecleanup import clean_tag_fields, plan_name_cleanup
from .common import add_reveal_menu, add_space_toggle, after_moves, fmt_duration, history, settings, undo_dir

STATUS = {
    "ok": ("Se redenumește", "#27ae60"),
    "same": ("Deja corect", "gray"),
    "missing": ("Fără taguri", "#e67e22"),
    "conflict": ("Conflict", "#c0392b"),
}


class PlanTable(QWidget):
    """Tabelul comun pentru redenumire și curățarea numelor."""

    def __init__(self, step: str, parent=None):
        super().__init__(parent)
        self.step = step
        self.plan: list[lib.RenameItem] = []
        self.counts = QLabel()
        self.counts.setTextFormat(Qt.RichText)
        self.counts.linkActivated.connect(self._count_clicked)
        self.show_f = QComboBox()
        self.show_f.addItem("Arată: tot ce trebuie făcut", "todo")
        self.show_f.addItem("Arată: se redenumesc", "ok")
        self.show_f.addItem("Arată: fără taguri", "missing")
        self.show_f.addItem("Arată: conflicte", "conflict")
        self.show_f.addItem("Arată: deja corecte", "same")
        self.show_f.addItem("Arată: tot", "all")
        self.show_f.currentIndexChanged.connect(self.apply_filter)
        self.include_done = QCheckBox("Include și piesele la care am făcut deja pasul ăsta")
        self.include_done.toggled.connect(self._reset_checks)
        b_all, b_none = QPushButton("Bifează tot"), QPushButton("Debifează tot")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        self.b_dup = QPushButton("Bifează conflictele → _Dubluri")
        self.b_dup.setToolTip("Când numele nou există deja: piesa pleacă într-un folder „_Dubluri” lângă ea, "
                              "cu numele nou. Cea existentă rămâne pe loc. Ca la Dubluri: folderul „_Dubluri” nu intră în scanări, "
                              "îl verifici și îl golești când vrei (se poate anula).")
        self.b_dup.clicked.connect(self._check_conflicts)
        self.table = QTableWidget(0, 5)
        add_reveal_menu(self.table, lambda idx: self.plan[idx.row()].src)
        add_space_toggle(self.table, 0)
        self.table.setHorizontalHeaderLabels(["", "Acum", "Devine", "Stare", "Motiv / detalii"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        for i, w in enumerate((28, 360, 360, 120)):
            self.table.setColumnWidth(i, w)
        self.table.itemChanged.connect(lambda *_: self._update_counts())
        top = QHBoxLayout()
        top.addWidget(self.show_f)
        top.addWidget(b_all)
        top.addWidget(b_none)
        top.addWidget(self.b_dup)
        top.addWidget(self.include_done)
        top.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.counts)

    def set_plan(self, plan: list[lib.RenameItem]):
        self.plan = plan
        self._filling = True
        self.table.setRowCount(len(plan))
        common = ""
        try:
            common = os.path.commonpath([p.src for p in plan] + [p.dest for p in plan]) if plan else ""
        except ValueError:
            common = ""

        def rel(x):
            try:
                return os.path.relpath(x, common) if common else x
            except ValueError:
                return x

        h = history()
        for i, p in enumerate(plan):
            label, color = STATUS[p.status]
            done = h.done(p.src, self.step)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                         if p.status in ("ok", "conflict") else Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if p.status in ("ok", "conflict"):
                chk.setCheckState(Qt.Unchecked)
            if p.status == "conflict":
                chk.setToolTip("Bifează ca s-o muți în „_Dubluri” (lângă ea), cu numele nou.")
            self.table.setItem(i, 0, chk)
            a = QTableWidgetItem(rel(p.src))
            a.setToolTip(p.src)
            b = QTableWidgetItem(rel(p.dest) if p.status != "missing" else "")
            b.setToolTip(p.dest)
            c = QTableWidgetItem(label + (f"  (făcut {done[:10]})" if done else ""))
            c.setForeground(QBrush(QColor(color)))
            d = QTableWidgetItem(p.note)
            d.setToolTip(p.note)
            for col, w in enumerate((a, b, c, d), 1):
                if done:
                    w.setForeground(QBrush(QColor("#999999"))) if col != 3 else None
                self.table.setItem(i, col, w)
        self._filling = False
        self._reset_checks()

    def _reset_checks(self):
        h = history()
        inc = self.include_done.isChecked()
        self.table.blockSignals(True)
        for i, p in enumerate(self.plan):
            if p.status == "ok":
                on = inc or not h.done(p.src, self.step)
                self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self.apply_filter()

    def _check_conflicts(self):
        self.table.blockSignals(True)
        for i, p in enumerate(self.plan):
            if p.status == "conflict":
                self.table.item(i, 0).setCheckState(Qt.Checked)
        self.table.blockSignals(False)
        i = self.show_f.findData("conflict")
        if i >= 0 and self.show_f.currentData() not in ("conflict", "all", "todo"):
            self.show_f.setCurrentIndex(i)
        self._update_counts()

    def _set_all(self, on):
        self.table.blockSignals(True)
        for i, p in enumerate(self.plan):
            if p.status == "ok" and not self.table.isRowHidden(i):
                self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_counts()

    def apply_filter(self):
        f = self.show_f.currentData()
        h = history()
        inc = self.include_done.isChecked()
        for i, p in enumerate(self.plan):
            if f == "all":
                hide = False
            elif f == "todo":
                hide = p.status == "same" or (not inc and bool(h.done(p.src, self.step)) and p.status == "ok")
            else:
                hide = p.status != f
            self.table.setRowHidden(i, hide)
        self._update_counts()

    def _count_clicked(self, link):
        i = self.show_f.findData(link)
        if i >= 0:
            self.show_f.setCurrentIndex(i)

    def _update_counts(self):
        c = {k: sum(1 for p in self.plan if p.status == k) for k in STATUS}
        checked = len(self.checked())
        dup = len(self.checked_conflicts())
        self.counts.setText(
            f"<b>{checked}</b> bifate de redenumit · "
            + (f"<b>{dup}</b> conflicte bifate → _Dubluri · " if dup else "") +
            f"<a href='ok'>{c['ok']} se pot redenumi</a> · <a href='same'>{c['same']} deja corecte</a> · "
            f"<a href='missing'>{c['missing']} fără taguri</a> · <a href='conflict'>{c['conflict']} conflicte</a>"
            " — dă click pe o cifră ca să vezi piesele")

    def checked(self) -> list[lib.RenameItem]:
        return [p for i, p in enumerate(self.plan)
                if p.status == "ok" and self.table.item(i, 0) is not None
                and self.table.item(i, 0).checkState() == Qt.Checked]

    def checked_conflicts(self) -> list[lib.RenameItem]:
        return [p for i, p in enumerate(self.plan)
                if p.status == "conflict" and self.table.item(i, 0) is not None
                and self.table.item(i, 0).checkState() == Qt.Checked]

    def final_plan(self) -> list[lib.RenameItem]:
        chosen = {id(p) for p in self.checked()}
        dups = {id(p) for p in self.checked_conflicts()}
        taken: set[str] = set()
        out = []
        for p in self.plan:
            if id(p) in chosen:
                out.append(p)
            elif id(p) in dups:     # numele nou e ocupat: o mutăm în „_Dubluri”, lângă ea
                out.append(lib.RenameItem(p.src, lib.duplicates_dest(p.src, p.dest, taken), "ok",
                                          "mutată în _Dubluri (numele nou exista deja)"))
            else:
                out.append(lib.RenameItem(p.src, p.dest, "skip" if p.status == "ok" else p.status, p.note))
        return out


def _run_plan(parent, plan, step, title, on_done):
    """Aplică planul ca activitate; marchează pasul în istoric pentru ce s-a făcut și ce era deja corect."""
    from .dialogs import rename_in_background
    same = [p.src for p in plan if p.status == "same"]

    def done_cb():
        if on_done:
            on_done()

    job = rename_in_background(parent, plan, done_cb, title)

    def mark(res):
        moves, _errors = res
        after_moves(moves)
        history().mark([new for _, new in moves] + same, step)
    job.succeeded.connect(mark)
    return job


class RenameDialog(QDialog):
    def __init__(self, tracks: list[lib.Track], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Redenumește / organizează — {len(tracks)} piese")
        self.resize(1250, 700)
        self.tracks = tracks
        self.applied = False
        self.preset = QComboBox()
        self.preset.addItems(list(lib.RENAME_PRESETS.keys()) + ["Personalizat"])
        self.template = QLineEdit(lib.RENAME_PRESETS["Artist - Titlu"])
        self.template.setToolTip("Câmpuri: {artist} {title} {album} {albumartist} {genre} {year} {track:02} {bpm} {key}\n"
                                 "Un „/” înseamnă folder nou.")
        self.base = QLineEdit()
        self.base.setPlaceholderText("Folderul în care se creează structura (obligatoriu când șablonul are „/”)")
        b_base = QPushButton("Alege…")
        b_base.clicked.connect(self._choose_base)
        self.preset.currentTextChanged.connect(lambda n: self.template.setText(lib.RENAME_PRESETS[n])
                                               if n in lib.RENAME_PRESETS else None)
        self.template.textEdited.connect(lambda _: self.preset.setCurrentText("Personalizat"))
        self.template.textChanged.connect(self.refresh)
        self.base.textChanged.connect(self.refresh)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Model:"))
        r1.addWidget(self.preset)
        r1.addWidget(QLabel("Șablon:"))
        r1.addWidget(self.template, 1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Organizează în:"))
        r2.addWidget(self.base, 1)
        r2.addWidget(b_base)
        self.plan_table = PlanTable("redenumire")
        self.table = self.plan_table.table
        bb = QDialogButtonBox()
        self.btn_apply = bb.addButton("Aplică bifatele", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(r1)
        lay.addLayout(r2)
        lay.addWidget(self.plan_table, 1)
        lay.addWidget(bb)
        self.refresh()

    def _choose_base(self):
        d = QFileDialog.getExistingDirectory(self, "Unde creez structura de foldere?")
        if d:
            self.base.setText(os.path.normpath(d))

    def refresh(self):
        tpl = self.template.text().strip()
        uses_folders = "/" in tpl
        self.base.setEnabled(uses_folders)
        base = self.base.text().strip() or None
        if not tpl or (uses_folders and not base):
            self.plan_table.set_plan([])
            return
        self.plan_table.set_plan(lib.plan_renames(self.tracks, tpl, base))

    def apply(self):
        plan = self.plan_table.final_plan()
        n = sum(1 for p in plan if p.status == "ok")
        if not n:
            QMessageBox.information(self, "Redenumire", "N-ai bifat nimic de redenumit.")
            return
        if QMessageBox.question(self, "Confirmare", f"Redenumesc {n} fișiere? (Se poate anula din Curățenie.)") \
                != QMessageBox.Yes:
            return
        _run_plan(self.parent(), plan, "redenumire", "Redenumesc fișierele", getattr(self, "on_applied", None))
        self.applied = True
        self.accept()


class NameCleanupDialog(QDialog):
    def __init__(self, tracks: list[lib.Track], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Curăță numele — {len(tracks)} piese")
        self.resize(1250, 680)
        self.tracks = tracks
        self.applied = False
        info = QLabel("Scot din numele fișierelor site-urile, „[Free DL]”, „320kbps”, „(Official Video)”, „_ytmp3”, "
                      "numerele de piesă etc. Se poate anula.")
        info.setWordWrap(True)
        self.c_num = QCheckBox("Scoate numerele de piesă de la început (01, A1…)")
        self.c_num.setChecked(True)
        self.c_tags = QCheckBox("Curăță și tagurile Artist / Titlu / Album la piesele bifate")
        self.c_tags.setChecked(True)
        self.c_num.toggled.connect(self.refresh)
        self.plan_table = PlanTable("nume")
        self.table = self.plan_table.table
        bb = QDialogButtonBox()
        bb.addButton("Aplică bifatele", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        o = QHBoxLayout()
        o.addWidget(self.c_num)
        o.addWidget(self.c_tags)
        o.addStretch()
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(o)
        lay.addWidget(self.plan_table, 1)
        lay.addWidget(bb)
        self.refresh()

    def refresh(self):
        self.plan_table.set_plan(plan_name_cleanup(self.tracks, self.c_num.isChecked()))

    def apply(self):
        plan = self.plan_table.final_plan()
        chosen = {p.src for p in plan if p.status == "ok"}
        same = {p.src for p in plan if p.status == "same"}
        by_path = {t.path: t for t in self.tracks}
        tag_jobs = []
        if self.c_tags.isChecked():
            for src in chosen | same:
                t = by_path.get(src)
                f = clean_tag_fields(t) if t else {}
                if f:
                    tag_jobs.append((src, f, None))
        if not chosen and not tag_jobs:
            QMessageBox.information(self, "Nume", "N-ai bifat nimic de curățat.")
            return
        from .dialogs import write_tags_in_background
        if tag_jobs:            # întâi tagurile (pe căile vechi), apoi redenumirea — rulează pe rând
            write_tags_in_background(self.parent(), tag_jobs, None, "Curăț tagurile")
        _run_plan(self.parent(), plan, "nume", "Curăț numele fișierelor", getattr(self, "on_applied", None))
        self.applied = True
        self.accept()


class DuplicatesDialog(QDialog):
    def __init__(self, groups: list[list[lib.Track]], parent=None, targets: list[lib.Track] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Dubluri")
        self.resize(1100, 640)
        self.all_groups = groups
        self.targets = targets or [t for g in groups for t in g]
        self.moved = False
        info = QLabel("Piese care par la fel (același artist și titlu, durată apropiată). Alege ce păstrezi: "
                      "„Păstrez doar MP3” lasă în fiecare grup cel mai bun MP3 și bifează restul. Bifatele se mută în "
                      "„_Dubluri” (se poate anula) sau în Coșul de gunoi (le recuperezi de acolo).")
        info.setWordWrap(True)
        self.include_done = QCheckBox("Include și grupurile verificate deja")
        self.include_done.toggled.connect(self.fill)
        b_flac = QPushButton("Păstrez doar MP3")
        b_flac.setToolTip("În fiecare grup rămâne nebifat cel mai bun MP3; tot restul (FLAC, WAV, AIFF, MP3-uri mai "
                          "slabe) se bifează. Grupurile fără niciun MP3 nu sunt atinse.")
        b_mp3 = QPushButton("Bifează toate MP3-urile (păstrez FLAC)")
        b_all = QPushButton("Bifează tot în afară de cea mai bună")
        b_none = QPushButton("Debifează tot")
        b_flac.clicked.connect(self.keep_only_mp3)
        b_mp3.clicked.connect(lambda: self.check_format("mp3"))
        b_all.clicked.connect(self.check_all_but_best)
        b_none.clicked.connect(lambda: self._set_all(False))
        tools = QHBoxLayout()
        for b in (b_flac, b_mp3, b_all, b_none):
            tools.addWidget(b)
        tools.addStretch()
        tools.addWidget(self.include_done)

        self.tree = QTreeWidget()
        add_space_toggle(self.tree)
        add_reveal_menu(self.tree, lambda idx: getattr(self.tree.itemFromIndex(idx).data(0, Qt.UserRole), "path", ""))
        self.tree.setHeaderLabels(["Piesă", "Calitate", "Durată", "Cale"])
        for i, w in enumerate((340, 160, 60)):
            self.tree.setColumnWidth(i, w)
        self.summary = QLabel()
        self.tree.itemChanged.connect(lambda *_: self._update_summary())

        b_move = QPushButton("Mută bifatele în _Dubluri")
        b_trash = QPushButton("Trimite bifatele în Coș")
        b_move.clicked.connect(lambda: self.act("move"))
        b_trash.clicked.connect(lambda: self.act("trash"))
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.summary, 1)
        row.addWidget(close)
        row.addWidget(b_trash)
        row.addWidget(b_move)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(tools)
        lay.addWidget(self.tree, 1)
        lay.addLayout(row)
        self.fill()

    def _groups(self):
        if self.include_done.isChecked():
            return self.all_groups
        h = history()
        return [g for g in self.all_groups if not all(h.done(t.path, "dubluri") for t in g)]

    def fill(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        for g in self._groups():
            first = g[0]
            parent = QTreeWidgetItem([f"{first.artist or '?'} – {first.display_title}  ({len(g)})"])
            f = parent.font(0)
            f.setBold(True)
            parent.setFont(0, f)
            self.tree.addTopLevelItem(parent)
            for t in g:
                it = QTreeWidgetItem([t.filename, t.quality_label, fmt_duration(t.duration), os.path.dirname(t.path)])
                it.setData(0, Qt.UserRole, t)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked)
                it.setToolTip(3, t.path)
                parent.addChild(it)
            parent.setExpanded(True)
        self.tree.blockSignals(False)
        keep = settings().value("dup/keep", "mp3") or "mp3"
        if keep == "mp3":
            self.keep_only_mp3(save=False)
        elif keep == "flac":
            self.check_format("mp3", save=False)
        else:
            self.check_all_but_best()

    def _children(self):
        for i in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(i)
            yield p, [p.child(j) for j in range(p.childCount())]

    def check_format(self, fmt: str, save: bool = True):
        """Bifează toate fișierele de formatul dat, dar niciodată toate piesele dintr-un grup."""
        self.tree.blockSignals(True)
        for _p, kids in self._children():
            marks = [k.data(0, Qt.UserRole).ext == fmt for k in kids]
            if all(marks):          # grup doar cu acel format: păstrăm cel mai bun
                marks[0] = False
            for k, m in zip(kids, marks):
                k.setCheckState(0, Qt.Checked if m else Qt.Unchecked)
        self.tree.blockSignals(False)
        if save:
            settings().setValue("dup/keep", "mp3" if fmt == "flac" else "flac")
        self._update_summary()

    def keep_only_mp3(self, save: bool = True):
        """În fiecare grup: păstrez cel mai bun MP3, bifez tot restul. Fără MP3 în grup: nu ating nimic."""
        self.tree.blockSignals(True)
        for _p, kids in self._children():
            mp3 = [k for k in kids if k.data(0, Qt.UserRole).ext == "mp3"]
            keep = max(mp3, key=lambda k: k.data(0, Qt.UserRole).quality_score()) if mp3 else None
            for k in kids:
                on = keep is not None and k is not keep
                k.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        if save:
            settings().setValue("dup/keep", "mp3")
        self._update_summary()

    def check_all_but_best(self):
        self.tree.blockSignals(True)
        for _p, kids in self._children():
            for i, k in enumerate(kids):
                k.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
        self.tree.blockSignals(False)
        settings().setValue("dup/keep", "best")
        self._update_summary()

    def _set_all(self, on):
        self.tree.blockSignals(True)
        for _p, kids in self._children():
            for k in kids:
                k.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_summary()

    def _checked(self) -> list[str]:
        return [k.data(0, Qt.UserRole).path for _p, kids in self._children() for k in kids
                if k.checkState(0) == Qt.Checked]

    def _update_summary(self):
        n = len(self._checked())
        groups = self.tree.topLevelItemCount()
        hidden = len(self.all_groups) - groups
        self.summary.setText(f"<b>{groups}</b> grupuri · <b>{n}</b> fișiere bifate"
                             + (f" · {hidden} grupuri verificate deja (ascunse)" if hidden else ""))

    def act(self, how: str):
        paths = self._checked()
        if not paths:
            QMessageBox.information(self, "Dubluri", "N-ai bifat nimic.")
            return
        for _p, kids in self._children():
            if all(k.checkState(0) == Qt.Checked for k in kids):
                if QMessageBox.question(self, "Atenție", "Într-un grup ai bifat toate piesele, deci n-ar mai "
                                        "rămâne niciuna. Continui oricum?") != QMessageBox.Yes:
                    return
                break
        what = "în „_Dubluri”" if how == "move" else "în Coșul de gunoi"
        to_move = []
        if how == "trash":
            from ..core.trash import split_by_bin
            paths, to_move = split_by_bin(paths)
        msg = f"Trimit {len(paths)} fișiere {what}?" if paths else ""
        if to_move:
            msg += ("\n\n" if msg else "") + (
                f"{len(to_move)} fișiere sunt pe un disc fără Coș de gunoi (stick, card sau rețea), unde "
                "Windows le-ar șterge definitiv. Pe acestea le mut în folderul „_Dubluri” de lângă ele.")
        if QMessageBox.question(self, "Confirmare", msg) != QMessageBox.Yes:
            return
        from .jobs import Job
        from .library_tab import report
        parent = self.parent()
        cb = getattr(self, "on_applied", None)
        checked_paths = [t.path for t in self.targets]

        def work(progress, cancelled):
            if how == "move":
                return lib.move_to_duplicates(paths, undo_dir()), ([], [])
            from ..core.trash import to_trash
            done, errors = to_trash(paths)
            moved = lib.move_to_duplicates(to_move, undo_dir()) if to_move else ([], [])
            return ([(p, "") for p in done], errors), moved

        def done(res):
            (moves, errors), (moved, errors2) = res
            history().mark(checked_paths, "dubluri")
            after_moves(moves if how == "move" else moved)
            text = f"Am trimis {len(moves)} fișiere {what}."
            if moved:
                text += f"\n{len(moved)} fișiere (de pe discuri fără Coș) le-am mutat în „_Dubluri”."
            report(parent, text, errors + errors2)
            if cb:
                cb()

        job = Job(f"Dubluri: {len(paths)} fișiere {what}", work)
        job.succeeded.connect(done)
        job.start()
        self.moved = True
        self.accept()
