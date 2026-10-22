"""Ferestrele de lucru: taguri online, nume curate, editare în masă, genuri, artiști, reguli."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import library as lib
from ..core import tags as tagmod
from ..core.genres import DEFAULT_FAMILIES, DEFAULT_STYLE_MAP, GenreMapper
from ..core.libdb import (RULE_FIELDS, RULE_OPS, artist_groups, compose_comment, describe_rule, genre_counts,
                          parse_comment)
from ..core.namecleanup import clean_tag_fields, plan_name_cleanup
from ..core.playlist import CAMELOT
from .common import Task, settings, undo_dir


# --- setări comune ------------------------------------------------------------
def genre_mapper() -> GenreMapper:
    return GenreMapper.from_json(settings().value("genres/families", "") or "",
                                 settings().value("genres/style_map", "") or "")


def families() -> list[str]:
    return genre_mapper().families


def _table(headers, widths=()):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    return t


def _item(text, color=None, tooltip=None, check=None):
    it = QTableWidgetItem(text)
    flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
    if check is not None:
        flags |= Qt.ItemIsUserCheckable
    it.setFlags(flags)
    if check is not None:
        it.setCheckState(Qt.Checked if check else Qt.Unchecked)
    if color:
        it.setForeground(QBrush(QColor(color)))
    if tooltip:
        it.setToolTip(tooltip)
    return it


def write_tags_in_background(parent, jobs: list[tuple[str, dict, object]], on_done, title="Scriu tagurile"):
    """jobs: (cale, câmpuri, copertă) — copertă = None, (bytes, mime) sau URL de descărcat."""
    from ..core.tagsearch import TagFinder
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    bar = QProgressBar()
    bar.setMaximum(len(jobs))
    lbl = QLabel("…")
    lay = QVBoxLayout(dlg)
    lay.addWidget(lbl)
    lay.addWidget(bar)
    dlg.resize(420, 90)

    def work(progress, cancelled):
        tf = TagFinder()
        errors = []
        for i, (path, fields, cover) in enumerate(jobs):
            if cancelled():
                break
            try:
                if isinstance(cover, str):
                    cover = tf.download_cover(cover)
                if fields or cover:
                    current = tagmod.read_tags(path).fields
                    tagmod.write_tags(path, {**current, **fields}, cover)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
            progress(i + 1, len(jobs), os.path.basename(path))
        return errors

    task = Task(work, dlg)
    task.progress.connect(lambda d, t, txt: (bar.setValue(d), lbl.setText(txt)))

    def finished(errors):
        dlg.accept()
        if errors:
            box = QMessageBox(QMessageBox.Warning, "Taguri", f"{len(errors)} fișiere nu au putut fi scrise "
                              "(poate sunt deschise în Traktor sau alt program).", parent=parent)
            box.setDetailedText("\n".join(errors))
            box.exec()
        on_done()

    task.succeeded.connect(finished)
    task.failed.connect(lambda m: (dlg.accept(), QMessageBox.warning(parent, "Eroare", m)))
    task.start()
    dlg.exec()


# --- taguri online -------------------------------------------------------------
class TagFinderDialog(QDialog):
    COLS = ["", "Piesă", "Gen", "Stil (→ Comentariu)", "An", "Label", "Copertă", "Găsit în"]

    def __init__(self, tracks: list[lib.Track], spotify=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Taguri online — {len(tracks)} piese")
        self.resize(1250, 700)
        self.tracks = tracks
        self.spotify = spotify
        self.results: dict[int, object] = {}
        self.task: Task | None = None
        self.applied = False

        token = settings().value("discogs/token", "") or ""
        src = "Discogs → MusicBrainz → Spotify" if token else "MusicBrainz → Spotify (fără token Discogs)"
        info = QLabel(f"Caut genul, stilul, anul, labelul și coperta. Surse: <b>{src}</b>. "
                      "Nimic nu se scrie până nu apeși „Aplică”. Durează ~1–2 secunde pe piesă "
                      "(sursele limitează numărul de cereri).")
        info.setWordWrap(True)
        self.c_genre = QCheckBox("Gen + stil")
        self.c_year = QCheckBox("An")
        self.c_label = QCheckBox("Label")
        self.c_cover = QCheckBox("Copertă")
        for c in (self.c_genre, self.c_year, self.c_label, self.c_cover):
            c.setChecked(True)
        self.c_over = QCheckBox("Suprascrie ce e deja completat")
        self.c_orig = QCheckBox("Anul primei lansări (nu al reeditărilor)")
        self.c_orig.setChecked(True)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Completez:"))
        for c in (self.c_genre, self.c_year, self.c_label, self.c_cover):
            opts.addWidget(c)
        opts.addSpacing(20)
        opts.addWidget(self.c_over)
        opts.addWidget(self.c_orig)
        opts.addStretch()

        self.table = _table(self.COLS, (28, 300, 150, 200, 60, 140, 70))
        self.progress = QProgressBar()
        self.status = QLabel()
        self.b_start = QPushButton("Caută")
        self.b_start.clicked.connect(self.start_or_stop)
        self.b_apply = QPushButton("Aplică bifatele")
        self.b_apply.setEnabled(False)
        self.b_apply.clicked.connect(self.apply)
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.b_start)
        row.addWidget(close)
        row.addWidget(self.b_apply)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(opts)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.progress)
        lay.addLayout(row)
        self._fill_initial()

    def _fill_initial(self):
        self.table.setRowCount(len(self.tracks))
        for i, t in enumerate(self.tracks):
            self.table.setItem(i, 0, _item("", check=False))
            self.table.setItem(i, 1, _item(f"{t.artist} - {t.display_title}", tooltip=t.path))
            self.table.setItem(i, 2, _item(t.genre or "—", "gray"))
            self.table.setItem(i, 3, _item(t.comment or "—", "gray"))
            self.table.setItem(i, 4, _item(t.year or "—", "gray"))
            self.table.setItem(i, 5, _item(t.label or "—", "gray"))
            self.table.setItem(i, 6, _item("✓" if t.has_cover else "—", "gray"))

    def _need(self, t) -> tuple:
        over = self.c_over.isChecked()
        need = []
        if self.c_genre.isChecked() and (over or not t.genre or not t.comment):
            need.append("genre")
        if self.c_year.isChecked() and (over or not t.year):
            need.append("year")
        if self.c_label.isChecked() and (over or not t.label):
            need.append("label")
        if self.c_cover.isChecked() and (over or not t.has_cover):
            need.append("cover")
        return tuple(need)

    def start_or_stop(self):
        if self.task and self.task.isRunning():
            self.task.cancel()
            self.b_start.setEnabled(False)
            return
        from ..core.tagsearch import TagFinder
        finder = TagFinder(settings().value("discogs/token", "") or "", self.spotify, genre_mapper(),
                           use_musicbrainz=settings().value("tags/musicbrainz", True) not in (False, "false"),
                           original_release=self.c_orig.isChecked())
        todo = [(i, t, self._need(t)) for i, t in enumerate(self.tracks)]
        todo = [x for x in todo if x[2] and x[1].artist and (x[1].title or x[1].filename)]
        if not todo:
            QMessageBox.information(self, "Taguri", "Toate piesele au deja ce ai bifat (sau n-au artist/titlu).")
            return

        def work(progress, cancelled):
            for n, (i, t, need) in enumerate(todo):
                if cancelled():
                    break
                try:
                    r = finder.find(t.artist, t.display_title, t.duration, need)
                except RuntimeError as e:
                    progress(n, len(todo), json.dumps({"error": str(e)}))
                    return False
                self.results[i] = r
                progress(n + 1, len(todo), json.dumps({"row": i}))
            return True

        self.task = Task(work, self)
        self.task.progress.connect(self._progress)
        self.task.succeeded.connect(self._done)
        self.task.failed.connect(lambda m: (self._done(False), QMessageBox.warning(self, "Eroare", m)))
        self.progress.setMaximum(len(todo))
        self.b_start.setText("Oprește")
        self.b_apply.setEnabled(False)
        self.task.start()

    def _progress(self, d, total, payload):
        self.progress.setValue(d)
        data = json.loads(payload)
        if "error" in data:
            QMessageBox.warning(self, "Taguri", data["error"])
            return
        i = data["row"]
        r, t = self.results[i], self.tracks[i]
        over = self.c_over.isChecked()

        def cell(col, cur, new, show=None):
            if new and (over or not cur) and new != cur:
                self.table.setItem(i, col, _item(f"{cur or '—'} → {show or new}", "#27ae60"))
                return True
            return False

        ch = False
        if self.c_genre.isChecked():
            ch |= cell(2, t.genre, r.genre)
            ch |= cell(3, t.comment, r.style_text)
        if self.c_year.isChecked():
            ch |= cell(4, t.year, r.year)
        if self.c_label.isChecked():
            ch |= cell(5, t.label, r.label)
        if self.c_cover.isChecked() and r.cover_url and (over or not t.has_cover):
            self.table.setItem(i, 6, _item("nouă", "#27ae60", tooltip=r.cover_url))
            ch = True
        self.table.setItem(i, 7, _item(r.source or "negăsită", None if r.source else "#c0392b", tooltip=r.match))
        self.table.item(i, 0).setCheckState(Qt.Checked if ch else Qt.Unchecked)
        self.status.setText(f"{d} / {total} verificate")

    def _done(self, _):
        self.b_start.setText("Caută din nou")
        self.b_start.setEnabled(True)
        found = sum(1 for i in range(self.table.rowCount()) if self.table.item(i, 0).checkState() == Qt.Checked)
        self.status.setText(f"Gata: {found} piese cu informații noi. Debifează ce nu vrei, apoi „Aplică”.")
        self.b_apply.setEnabled(found > 0)

    def apply(self):
        jobs = []
        over = self.c_over.isChecked()
        for i, t in enumerate(self.tracks):
            if self.table.item(i, 0).checkState() != Qt.Checked or i not in self.results:
                continue
            r = self.results[i]
            f = {}
            if self.c_genre.isChecked():
                if r.genre and (over or not t.genre):
                    f["genre"] = r.genre
                old_style, role, energy = parse_comment(t.comment)
                if r.style_text and (over or not old_style):
                    f["comment"] = compose_comment(r.style_text, role, energy)   # păstrăm rolul și energia
            if self.c_year.isChecked() and r.year and (over or not t.year):
                f["year"] = r.year
            if self.c_label.isChecked() and r.label and (over or not t.label):
                f["label"] = r.label
            cover = r.cover_url if (self.c_cover.isChecked() and r.cover_url and (over or not t.has_cover)) else None
            if f or cover:
                jobs.append((t.path, f, cover))
        if not jobs:
            return
        write_tags_in_background(self, jobs, self._applied, f"Scriu tagurile în {len(jobs)} fișiere")

    def _applied(self):
        self.applied = True
        self.accept()


# --- nume curate ---------------------------------------------------------------
class NameCleanupDialog(QDialog):
    def __init__(self, tracks: list[lib.Track], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Curăță numele — {len(tracks)} piese")
        self.resize(1100, 620)
        self.tracks = tracks
        self.applied = False
        info = QLabel("Scot din numele fișierelor site-urile, „[Free DL]”, „320kbps”, „(Official Video)”, "
                      "„_ytmp3”, numerele de piesă etc. Previzualizare mai jos; se poate anula.")
        info.setWordWrap(True)
        self.c_num = QCheckBox("Scoate numerele de piesă de la început (01, A1…)")
        self.c_num.setChecked(True)
        self.c_tags = QCheckBox("Curăță și tagurile Artist / Titlu / Album")
        self.c_tags.setChecked(True)
        self.c_num.toggled.connect(self.refresh)
        self.table = _table(["Acum", "Devine", "Stare"], (450, 450))
        self.summary = QLabel()
        bb = QDialogButtonBox()
        self.b_ok = bb.addButton("Aplică", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        o = QHBoxLayout()
        o.addWidget(self.c_num)
        o.addWidget(self.c_tags)
        o.addStretch()
        lay.addLayout(o)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.summary)
        lay.addWidget(bb)
        self.refresh()

    def refresh(self):
        self.plan = plan_name_cleanup(self.tracks, self.c_num.isChecked())
        rows = [p for p in self.plan if p.status != "same"]
        self.table.setRowCount(len(rows))
        colors = {"ok": "#27ae60", "conflict": "#c0392b"}
        labels = {"ok": "Se redenumește", "conflict": "Ar suprascrie alt fișier — sărit"}
        for i, p in enumerate(rows):
            self.table.setItem(i, 0, _item(os.path.basename(p.src), tooltip=p.src))
            self.table.setItem(i, 1, _item(os.path.basename(p.dest)))
            self.table.setItem(i, 2, _item(labels[p.status], colors[p.status]))
        n = sum(1 for p in self.plan if p.status == "ok")
        self.tag_jobs = [(t.path, clean_tag_fields(t), None) for t in self.tracks if clean_tag_fields(t)]
        self.summary.setText(f"<b>{n}</b> fișiere de redenumit · {len(self.plan) - len(rows)} deja curate · "
                             f"{len(self.tag_jobs)} cu taguri de curățat")
        self.b_ok.setEnabled(n > 0 or bool(self.tag_jobs))

    def apply(self):
        moved = {}
        if self.c_tags.isChecked() and self.tag_jobs:
            write_tags_in_background(self, self.tag_jobs, lambda: None, "Curăț tagurile")
        moves, errors = lib.apply_renames(self.plan, undo_dir())
        self.applied = True
        msg = f"Am redenumit {len(moves)} fișiere."
        if errors:
            msg += f"\n{len(errors)} nu au putut fi redenumite."
        QMessageBox.information(self, "Gata", msg)
        self.accept()


# --- editare în masă ------------------------------------------------------------
class BulkEditDialog(QDialog):
    """Schimbă aceleași câmpuri pentru mai multe piese deodată."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = items
        self.setWindowTitle(f"Editează {len(items)} piese")
        self.result_fields: dict = {}
        self.result_rating: int | None = None
        self.result_bpm: float | None = None
        self.result_key: str | None = None
        first = items[0].track if items else None

        def common(attr):
            vals = {getattr(i.track, attr) for i in items}
            return vals.pop() if len(vals) == 1 else ""

        self.rows = {}
        form = QFormLayout()

        def add(key, label, widget):
            chk = QCheckBox()
            box = QHBoxLayout()
            box.addWidget(chk)
            box.addWidget(widget, 1)
            w = QWidget()
            w.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            form.addRow(label, w)
            self.rows[key] = (chk, widget)
            if hasattr(widget, "textEdited"):
                widget.textEdited.connect(lambda _: chk.setChecked(True))
            elif hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(lambda _: chk.setChecked(True))
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(lambda _: chk.setChecked(True))

        g = QComboBox()
        g.setEditable(True)
        g.addItems([""] + families())
        g.setCurrentText(common("genre"))
        add("genre", "Gen:", g)
        add("comment", "Stil:", QLineEdit(""))
        add("artist", "Artist:", QLineEdit(common("artist")))
        add("album", "Album:", QLineEdit(common("album")))
        add("year", "An:", QLineEdit(common("year")))
        add("label", "Label:", QLineEdit(common("label")))
        r = QSpinBox()
        r.setRange(0, 5)
        r.setSuffix(" ★")
        add("rating", "Rating:", r)
        b = QDoubleSpinBox()
        b.setRange(0, 250)
        b.setDecimals(2)
        add("bpm", "BPM (manual):", b)
        k = QComboBox()
        k.addItem("")
        for code, note in CAMELOT.items():
            k.addItem(f"{note} ({code})", note)
        add("key", "Tonalitate (manual):", k)
        for chk, _ in self.rows.values():
            chk.setChecked(False)

        note = QLabel("Bifează doar câmpurile pe care vrei să le schimbi. Genul, stilul, artistul, albumul, "
                      "anul și labelul se scriu în fișiere; BPM-ul și tonalitatea manuale au prioritate "
                      "față de orice altă sursă și se scriu și în fișiere.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(bb)
        self.resize(520, 420)

    def _ok(self):
        for key, (chk, w) in self.rows.items():
            if not chk.isChecked():
                continue
            if key == "rating":
                self.result_rating = w.value()
            elif key == "bpm":
                self.result_bpm = w.value()
                if w.value():
                    self.result_fields["bpm"] = str(int(round(w.value())))
            elif key == "key":
                self.result_key = w.currentData() or ""
                if self.result_key:
                    self.result_fields["key"] = self.result_key
            elif isinstance(w, QComboBox):
                self.result_fields[key] = w.currentText().strip()
            else:
                self.result_fields[key] = w.text().strip()
        self.accept()


# --- curățare genuri --------------------------------------------------------------
class GenreCleanupDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Curăță genurile")
        self.resize(780, 600)
        self.items = items
        self.applied = False
        self.mapper = genre_mapper()
        info = QLabel("Toate genurile din bibliotecă. Selectează mai multe și unește-le într-unul singur, "
                      "sau apasă „Grupează automat”, care propune familia pentru fiecare (ex. Deep House → House). "
                      "Stilul vechi se păstrează în Comentariu, dacă acesta e gol.")
        info.setWordWrap(True)
        self.table = _table(["", "Gen acum", "Piese", "Devine"], (28, 260, 60))
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.addItems(self.mapper.families)
        b_merge = QPushButton("Setează selecția la →")
        b_merge.clicked.connect(self.merge_selected)
        b_auto = QPushButton("Grupează automat în familii")
        b_auto.clicked.connect(self.auto)
        row = QHBoxLayout()
        row.addWidget(b_auto)
        row.addStretch()
        row.addWidget(b_merge)
        row.addWidget(self.target)
        bb = QDialogButtonBox()
        self.b_ok = bb.addButton("Scrie în fișiere", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        lay.addWidget(bb)
        self.counts = genre_counts(items)
        self.table.setRowCount(len(self.counts))
        for i, (g, n) in enumerate(self.counts):
            self.table.setItem(i, 0, _item("", check=False))
            self.table.setItem(i, 1, _item(g or "(fără gen)", "gray" if not g else None))
            self.table.setItem(i, 2, _item(str(n)))
            self.table.setItem(i, 3, _item(""))

    def merge_selected(self):
        tgt = self.target.currentText().strip()
        for i in sorted({ix.row() for ix in self.table.selectedIndexes()}):
            if self.counts[i][0] != tgt:
                self.table.setItem(i, 3, _item(tgt, "#27ae60"))
                self.table.item(i, 0).setCheckState(Qt.Checked)

    def auto(self):
        n = 0
        for i, (g, _) in enumerate(self.counts):
            fam = self.mapper.family_of(g)
            if fam and fam != g:
                self.table.setItem(i, 3, _item(fam, "#27ae60"))
                self.table.item(i, 0).setCheckState(Qt.Checked)
                n += 1
        unknown = [g for g, _ in self.counts if g and not self.mapper.family_of(g)]
        msg = f"Am propus familia pentru {n} genuri."
        if unknown:
            msg += (f"\n{len(unknown)} nu le recunosc (ex. {', '.join(unknown[:5])}). Le poți seta de mână "
                    "sau le adaugi în tabelul de stiluri din Setări.")
        QMessageBox.information(self, "Grupare", msg)

    def apply(self):
        mapping = {}
        for i, (g, _) in enumerate(self.counts):
            new = self.table.item(i, 3).text()
            if self.table.item(i, 0).checkState() == Qt.Checked and new and new != g:
                mapping[g] = new
        if not mapping:
            self.reject()
            return
        jobs = []
        for it in self.items:
            g = it.track.genre.strip()
            if g in mapping:
                f = {"genre": mapping[g]}
                style, role, energy = parse_comment(it.track.comment)
                if g and not style and genre_mapper().family_of(g) != g:
                    f["comment"] = compose_comment(g, role, energy)      # stilul vechi nu se pierde
                jobs.append((it.path, f, None))
        if QMessageBox.question(self, "Confirmare", f"Scriu genul nou în {len(jobs)} fișiere?") != QMessageBox.Yes:
            return
        write_tags_in_background(self, jobs, self._done, "Scriu genurile")

    def _done(self):
        self.applied = True
        self.accept()


# --- curățare artiști -------------------------------------------------------------
class ArtistCleanupDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Curăță artiștii")
        self.resize(760, 560)
        self.items = items
        self.applied = False
        self.groups = artist_groups(items)
        info = QLabel(f"Am găsit {len(self.groups)} artiști scriși în mai multe feluri (majuscule, diacritice, "
                      "„The”, „&” / „and”). Alege forma corectă pentru fiecare; se scrie în toate fișierele lui.")
        info.setWordWrap(True)
        self.table = _table(["", "Variante (piese)", "Forma corectă"], (28, 380))
        self.table.setRowCount(len(self.groups))
        self.combos = []
        for i, g in enumerate(self.groups):
            self.table.setItem(i, 0, _item("", check=True))
            self.table.setItem(i, 1, _item("  ·  ".join(f"{a} ({n})" for a, n in g)))
            c = QComboBox()
            c.setEditable(True)
            c.addItems([a for a, _ in g])
            self.table.setCellWidget(i, 2, c)
            self.combos.append(c)
        bb = QDialogButtonBox()
        bb.addButton("Scrie în fișiere", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addWidget(bb)

    def apply(self):
        rename = {}
        for i, g in enumerate(self.groups):
            if self.table.item(i, 0).checkState() != Qt.Checked:
                continue
            canon = self.combos[i].currentText().strip()
            for a, _ in g:
                if a != canon:
                    rename[a] = canon
        jobs = [(it.path, {"artist": rename[it.track.artist.strip()]}, None)
                for it in self.items if it.track.artist.strip() in rename]
        if not jobs:
            self.reject()
            return
        write_tags_in_background(self, jobs, self._done, "Scriu artiștii")

    def _done(self):
        self.applied = True
        self.accept()


# --- playlist inteligent -----------------------------------------------------------
class RuleRow(QWidget):
    def __init__(self, rule: dict | None, on_remove, parent=None):
        super().__init__(parent)
        self.field = QComboBox()
        for k, v in RULE_FIELDS.items():
            self.field.addItem(v, k)
        self.op = QComboBox()
        self.v1 = QLineEdit()
        self.v2 = QLineEdit()
        self.v2.setMaximumWidth(80)
        self.dash = QLabel("–")
        rm = QPushButton("✕")
        rm.setMaximumWidth(30)
        rm.clicked.connect(lambda: on_remove(self))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.field, self.op, self.v1, self.dash, self.v2, rm):
            lay.addWidget(w)
        self.field.currentIndexChanged.connect(self._field_changed)
        rule = rule or {"field": "genre", "op": "este oricare din", "value": ""}
        self.field.setCurrentIndex(max(0, self.field.findData(rule.get("field"))))
        self._field_changed()
        self.op.setCurrentText(rule.get("op", ""))
        v = rule.get("value", "")
        if isinstance(v, list):
            self.v1.setText(str(v[0]) if v else "")
            self.v2.setText(str(v[1]) if len(v) > 1 else "")
        else:
            self.v1.setText(str(v))

    def _field_changed(self):
        f = self.field.currentData()
        self.op.clear()
        self.op.addItems(RULE_OPS.get(f, []))
        rng = f in ("bpm", "year", "energy")
        self.v2.setVisible(rng)
        self.dash.setVisible(rng)
        self.v1.setVisible(f not in ("in_traktor", "has_cover"))
        hints = {"genre": "ex. House, Disco", "bpm": "de la", "year": "de la", "key": "ex. 8A sau Am",
                 "rating": "ex. 3", "added": "zile, ex. 30", "format": "ex. mp3, flac sau lossless",
                 "role": "ex. Peak, Closer", "energy": "de la", "plays": "ex. 2"}
        self.v1.setPlaceholderText(hints.get(f, ""))
        self.v2.setPlaceholderText("până la")

    def rule(self) -> dict:
        f = self.field.currentData()
        v = [self.v1.text().strip(), self.v2.text().strip()] if f in ("bpm", "year", "energy") else self.v1.text().strip()
        return {"field": f, "op": self.op.currentText(), "value": v}


class SmartPlaylistDialog(QDialog):
    def __init__(self, name: str = "", rules: dict | None = None, preview=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Playlist inteligent")
        self.resize(720, 420)
        self.preview = preview
        rules = rules or {"match": "all", "rules": [{"field": "genre", "op": "este oricare din", "value": ""},
                                                    {"field": "bpm", "op": "între", "value": ["", ""]}]}
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("ex. House 120-124")
        self.match = QComboBox()
        self.match.addItem("toate regulile", "all")
        self.match.addItem("oricare regulă", "any")
        self.match.setCurrentIndex(0 if rules.get("match", "all") == "all" else 1)
        self.rows_box = QVBoxLayout()
        self.rows: list[RuleRow] = []
        for r in rules.get("rules") or []:
            self._add(r)
        b_add = QPushButton("+ Regulă")
        b_add.clicked.connect(lambda: self._add(None))
        self.count = QLabel()
        b_prev = QPushButton("Câte piese?")
        b_prev.clicked.connect(self._preview)
        note = QLabel("BPM-ul acceptă și dublu/jumătate (ex. 62 BPM intră la 120–128). "
                      "Playlistul se actualizează singur când adaugi muzică.")
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        f.addRow("Nume:", self.name)
        f.addRow("Piesele trebuie să respecte:", self.match)
        lay.addLayout(f)
        lay.addLayout(self.rows_box)
        r = QHBoxLayout()
        r.addWidget(b_add)
        r.addStretch()
        r.addWidget(self.count)
        r.addWidget(b_prev)
        lay.addLayout(r)
        lay.addWidget(note)
        lay.addStretch()
        lay.addWidget(bb)

    def _add(self, rule):
        row = RuleRow(rule, self._remove)
        self.rows.append(row)
        self.rows_box.addWidget(row)

    def _remove(self, row):
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def rules(self) -> dict:
        return {"match": self.match.currentData(), "rules": [r.rule() for r in self.rows]}

    def _preview(self):
        if self.preview:
            self.count.setText(f"{self.preview(self.rules())} piese")

    def _save(self):
        if not self.name.text().strip():
            self.name.setText(" · ".join(describe_rule(r) for r in self.rules()["rules"])[:60] or "Playlist")
        self.accept()


# --- tabel stiluri → familie ---------------------------------------------------------
class StyleMapDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Familii de gen și stiluri")
        self.resize(640, 640)
        m = genre_mapper()
        self.fam = QLineEdit(", ".join(m.families))
        info = QLabel("Familiile sunt genurile „grosiere” scrise în tag. Mai jos: ce stil intră în ce familie. "
                      "Poți adăuga rânduri sau schimba familia oricărui stil.")
        info.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Stil", "Familie"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        current = {k: v for k, v in m.style_map.items()}
        self.table.setRowCount(len(current))
        for i, (k, v) in enumerate(sorted(current.items())):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(v))
        b_add = QPushButton("+ Rând")
        b_add.clicked.connect(lambda: self.table.insertRow(0))
        b_reset = QPushButton("Valorile implicite")
        b_reset.clicked.connect(self._reset)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        f = QFormLayout()
        f.addRow("Familii:", self.fam)
        lay.addLayout(f)
        lay.addWidget(self.table, 1)
        r = QHBoxLayout()
        r.addWidget(b_add)
        r.addWidget(b_reset)
        r.addStretch()
        lay.addLayout(r)
        lay.addWidget(bb)

    def _reset(self):
        settings().remove("genres/families")
        settings().remove("genres/style_map")
        self.reject()
        QMessageBox.information(self.parent(), "Genuri", "Am revenit la valorile implicite.")

    def _save(self):
        fams = [f.strip() for f in self.fam.text().split(",") if f.strip()]
        mp = {}
        for i in range(self.table.rowCount()):
            a, b = self.table.item(i, 0), self.table.item(i, 1)
            if a and b and a.text().strip() and b.text().strip():
                mp[a.text().strip().lower()] = b.text().strip()
        settings().setValue("genres/families", json.dumps(fams or DEFAULT_FAMILIES))
        settings().setValue("genres/style_map", json.dumps(mp or DEFAULT_STYLE_MAP))
        self.accept()
