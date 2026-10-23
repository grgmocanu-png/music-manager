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
from .common import Task, add_reveal_menu, add_space_toggle, settings, undo_dir


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
    """Scrie tagurile în fundal, ca activitate (vezi bara de jos). Nu blochează fereastra.

    jobs: (cale, câmpuri, copertă) — copertă = None, (bytes, mime) sau URL de descărcat.
    """
    from ..core.tagsearch import TagFinder
    from .jobs import Job

    def work(progress, cancelled):
        tf = TagFinder()
        errors = []
        for i, (path, fields, cover) in enumerate(jobs):
            if cancelled():
                break
            try:
                if isinstance(cover, (str, list)):
                    want = bool(cover)
                    cover = tf.download_cover(cover)
                    if want and not cover:
                        errors.append(f"{os.path.basename(path)}: copertă găsită, dar n-am putut-o descărca")
                if fields or cover:
                    current = tagmod.read_tags(path).fields
                    clear = tuple(k for k, v in fields.items() if v == "")
                    tagmod.write_tags(path, {**current, **fields}, cover, clear)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
            progress(i + 1, len(jobs), os.path.basename(path))
        return errors

    job = Job(f"{title} ({len(jobs)})", work)

    def finished(errors):
        if errors and parent is not None:
            box = QMessageBox(QMessageBox.Warning, "Taguri", f"{len(errors)} fișiere nu au putut fi scrise "
                              "(poate sunt deschise în Traktor sau alt program).", parent=parent)
            box.setDetailedText("\n".join(errors))
            box.open()
        if on_done:
            on_done()

    job.succeeded.connect(finished)
    job.failed.connect(lambda m: QMessageBox.warning(parent, "Eroare", m) if parent is not None else None)
    job.start()
    return job


def rename_in_background(parent, plan, on_done, title="Redenumesc fișierele"):
    """Aplică un plan de redenumire ca activitate în fundal (cu anulare ulterioară)."""
    from .common import undo_dir
    from .jobs import Job
    n = sum(1 for p in plan if p.status == "ok")

    def work(progress, cancelled):
        progress(0, n, "")
        return lib.apply_renames(plan, undo_dir())

    job = Job(f"{title} ({n})", work)

    def finished(res):
        moves, errors = res
        if errors and parent is not None:
            box = QMessageBox(QMessageBox.Warning, "Redenumire", f"Am redenumit {len(moves)} fișiere; "
                              f"{len(errors)} nu au putut fi redenumite.", parent=parent)
            box.setDetailedText("\n".join(errors))
            box.open()
        if on_done:
            on_done()

    job.succeeded.connect(finished)
    job.start()
    return job


# --- taguri online -------------------------------------------------------------
def _results_path() -> str:
    from .common import data_dir
    return os.path.join(data_dir(), "taguri_gasite.json")


def load_tag_results() -> dict:
    """Rezultatele căutărilor anterioare, pe piesă (ca „Aplică” să meargă și mai târziu)."""
    try:
        with open(_results_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_tag_results(data: dict) -> None:
    from ..core.safeio import write_json
    try:
        write_json(_results_path(), data, ensure_ascii=False)
    except OSError:
        pass


def _rkey(path: str) -> str:
    from ..core.djcollections import norm_path
    return norm_path(path)


def _hints(t) -> dict:
    return {"mood": getattr(t, "mood", ""), "grouping": getattr(t, "grouping", ""),
            "style": getattr(t, "tag_style", ""), "url": getattr(t, "url", "")}


class TagFinderDialog(QDialog):
    COLS = ["", "Piesă", "Gen", "Stil (→ Comentariu)", "An", "Label", "Copertă", "Găsit în", "Verificată"]

    def __init__(self, tracks: list[lib.Track], spotify=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Taguri online — {len(tracks)} piese")
        self.resize(1250, 700)
        self.tracks = tracks
        self.spotify = spotify
        self.results: dict[int, object] = {}
        self.task: Task | None = None
        self.applied = False
        self._store = load_tag_results()
        self._now: set[int] = set()          # rândurile din căutarea curentă
        self._written_rows: set[int] = set()

        token = settings().value("discogs/token", "") or ""
        lfm = settings().value("lastfm/key", "") or ""
        srcs = ["din fișier (Mood/Grouping)"] + (["Discogs"] if token else []) + ["Bandcamp (dacă are link)",
                                                                                  "MusicBrainz", "Spotify"]
        srcs += (["Last.fm"] if lfm else []) + (["Discogs după artist"] if token else [])
        info = QLabel(f"Caut genul, stilul, anul, labelul și coperta <b>doar pentru piesele bifate</b>. "
                      f"Surse, pe rând: <b>{' → '.join(srcs)}</b>. "
                      + ("" if token else "<b>Fără token Discogs</b> găsesc mult mai puțin (Setări → Conturi). ")
                      + "Ce găsesc rămâne ținut minte pe piesă: poți închide fereastra și aplica mai târziu, "
                      "fără să caut din nou. Durează ~1–3 secunde pe piesă.")
        info.setWordWrap(True)
        self.c_genre = QCheckBox("Gen + stil")
        self.c_year = QCheckBox("An")
        self.c_label = QCheckBox("Label")
        self.c_cover = QCheckBox("Copertă")
        for c in (self.c_genre, self.c_year, self.c_label, self.c_cover):
            c.setChecked(True)
            c.toggled.connect(lambda *_: self._render_all())
        self.c_over = QCheckBox("Suprascrie ce e deja completat")
        self.c_over.setToolTip("Și după căutare: bifează, apoi „Aplică bifatele” — nu mai caut din nou.")
        self.c_over.toggled.connect(lambda *_: self._render_all())
        self.c_orig = QCheckBox("Anul primei lansări (nu al reeditărilor)")
        self.c_orig.setChecked(True)
        self.c_auto = QCheckBox("Scrie automat în fișiere pe măsură ce găsesc")
        self.c_auto.setChecked(settings().value("tags/auto_write", True) not in (False, "false"))
        self.c_auto.toggled.connect(lambda v: settings().setValue("tags/auto_write", bool(v)))
        self._pending: list = []
        self._written = 0
        self._derived: dict = {}       # rând -> (artist, titlu) luate din numele fișierului
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Completez:"))
        for c in (self.c_genre, self.c_year, self.c_label, self.c_cover):
            opts.addWidget(c)
        opts.addSpacing(20)
        opts.addWidget(self.c_over)
        opts.addWidget(self.c_orig)
        opts.addWidget(self.c_auto)
        opts.addStretch()

        self.table = _table(self.COLS, (28, 300, 150, 200, 60, 140, 70, 220))
        add_reveal_menu(self.table, lambda idx: self.tracks[idx.row()].path)
        add_space_toggle(self.table, 0)
        b_all, b_none, b_new = QPushButton("Bifează tot"), QPushButton("Debifează tot"), \
            QPushButton("Bifează doar ce n-a fost verificat")
        b_all.clicked.connect(lambda: self._set_checks(lambda i: True))
        b_none.clicked.connect(lambda: self._set_checks(lambda i: False))
        b_new.clicked.connect(lambda: self._set_checks(lambda i: not self._searched(i)))
        self.c_again = QCheckBox("Caută din nou și piesele verificate deja")
        self.c_now = QCheckBox("Arată doar ce caut acum")
        self.c_now.toggled.connect(lambda *_: self._apply_filter())
        self.c_new_only = QCheckBox("Arată doar piesele cu informații noi")
        self.c_new_only.toggled.connect(lambda *_: self._apply_filter())
        sel = QHBoxLayout()
        for b in (b_all, b_none, b_new):
            sel.addWidget(b)
        sel.addWidget(self.c_again)
        sel.addSpacing(16)
        sel.addWidget(self.c_now)
        sel.addWidget(self.c_new_only)
        sel.addStretch()
        self.progress = QProgressBar()
        self.status = QLabel()
        self.b_start = QPushButton("Caută")
        self.b_start.clicked.connect(self.start_or_stop)
        self.b_apply = QPushButton("Aplică bifatele")
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
        lay.addLayout(sel)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.progress)
        lay.addLayout(row)
        self._fill_initial()

    # Activități: ce scrie lângă fereastră
    def progress_text(self) -> str:
        if self.task and self.task.isRunning():
            return f"caut {self.progress.value()} / {self.progress.maximum()}"
        return self.status.text()[:60]

    def _searched(self, i) -> str:
        from .common import history
        return history().done(self.tracks[i].path, "taguri")

    def _set_checks(self, fn):
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                self.table.item(i, 0).setCheckState(Qt.Checked if fn(i) else Qt.Unchecked)

    def _fill_initial(self):
        from ..core.tagsearch import TagResult
        self.table.setRowCount(len(self.tracks))
        restored = 0
        for i, t in enumerate(self.tracks):
            done = self._searched(i)
            self.table.setItem(i, 0, _item("", check=not done))
            self.table.setItem(i, 8, _item(done[:16].replace("T", " ") if done else "—", "gray"))
            self.table.setItem(i, 1, _item(f"{t.artist} - {t.display_title}", tooltip=t.path))
            saved = self._store.get(_rkey(t.path))
            if saved:
                try:
                    self.results[i] = TagResult(**{k: v for k, v in saved.items() if k != "when"})
                    restored += 1
                except TypeError:
                    pass
            self._render(i)
        n_new = sum(1 for i in self.results if self._job_for(i))
        if restored:
            self.status.setText(f"{restored} piese au rezultate de la căutările de dinainte; "
                                f"{n_new} au informații noi — bifate, gata de „Aplică”.")
            for i in self.results:
                self.table.item(i, 0).setCheckState(Qt.Checked if self._job_for(i) else Qt.Unchecked)

    # --- afișarea unui rând (din rezultatul ținut minte; se refac la schimbarea opțiunilor) ---
    def _render(self, i):
        t = self.tracks[i]
        r = self.results.get(i)
        over = self.c_over.isChecked()

        def cell(col, cur, new, on):
            if on and r is not None and new and (over or not cur) and new != cur:
                self.table.setItem(i, col, _item(f"{cur or '—'} → {new}", "#27ae60", tooltip=new))
                return True
            self.table.setItem(i, col, _item(cur or "—", "gray"))
            return False

        ch = False
        ch |= cell(2, t.genre, r.genre if r else "", self.c_genre.isChecked())
        ch |= cell(3, t.comment, r.style_text if r else "", self.c_genre.isChecked())
        ch |= cell(4, t.year, r.year if r else "", self.c_year.isChecked())
        ch |= cell(5, t.label, r.label if r else "", self.c_label.isChecked())
        if r is not None and self.c_cover.isChecked() and r.cover_url and (over or not t.has_cover):
            self.table.setItem(i, 6, _item("nouă", "#27ae60", tooltip=r.cover_url))
            ch = True
        else:
            self.table.setItem(i, 6, _item("✓" if t.has_cover else "—", "gray"))
        if r is not None:
            if r.source:
                self.table.setItem(i, 7, _item(r.source, tooltip=r.match + (
                    "\n\nNegăsită în: " + "; ".join(r.reasons) if r.reasons else "")))
            else:
                why = "negăsită — " + "; ".join(r.reasons) if r.reasons else "negăsită"
                self.table.setItem(i, 7, _item(why, "#c0392b", tooltip=why.replace("; ", "\n")))
        return ch

    def _render_all(self):
        for i in range(len(self.tracks)):
            ch = self._render(i)
            if i in self.results and i not in self._written_rows:
                self.table.item(i, 0).setCheckState(Qt.Checked if ch else Qt.Unchecked)
        self._apply_filter()

    def _apply_filter(self):
        now, new_only = self.c_now.isChecked(), self.c_new_only.isChecked()
        for i in range(self.table.rowCount()):
            hide = (now and i not in self._now) or (new_only and not self._job_for(i))
            self.table.setRowHidden(i, hide)

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
                           original_release=self.c_orig.isChecked(),
                           lastfm_key=settings().value("lastfm/key", "") or "",
                           use_bandcamp=settings().value("tags/bandcamp", True) not in (False, "false"),
                           by_artist=settings().value("tags/by_artist", True) not in (False, "false"))
        rows = [i for i in range(self.table.rowCount()) if self.table.item(i, 0).checkState() == Qt.Checked]
        if not rows:
            QMessageBox.information(self, "Taguri", "Bifează piesele pe care vrei să le caut.")
            return
        todo, skipped = [], {}
        for i in rows:
            t = self.tracks[i]
            done = self._searched(i)
            if done and not self.c_again.isChecked():
                if i in self.results:
                    continue            # avem deja rezultatul: se aplică direct, fără căutare
                skipped[i] = f"căutată deja pe {done[:10]} — bifează „Caută din nou”"
                continue
            need = self._need(t)
            if not need:
                skipped[i] = "are deja tot ce ai cerut — bifează „Suprascrie” ca s-o cauți din nou"
                continue
            artist, title = t.artist, t.title
            if not (artist and title):
                fa, ft = lib.artist_title_from_filename(t)
                artist, title = artist or fa, title or ft
                if fa or ft:
                    self._derived[i] = (artist, title)
            if not artist:
                skipped[i] = "n-are artist nici în taguri, nici în numele fișierului („Artist - Titlu”)"
                continue
            todo.append((i, t, need, artist, title or t.display_title))
        for i, why in skipped.items():
            self.table.setItem(i, 7, _item(why, "#c0392b", tooltip=why))
        if not todo:
            if any(i in self.results for i in rows):
                QMessageBox.information(self, "Taguri", "Piesele bifate au fost căutate deja — rezultatele sunt "
                                        "în tabel. Apasă „Aplică bifatele” (sau bifează „Caută din nou”).")
            else:
                QMessageBox.information(self, "Taguri", "Nu am ce căuta. Motivul e scris pe fiecare rând, "
                                        "în coloana „Găsit în”.")
            return
        self._now = {i for i, *_ in todo}
        for i in range(self.table.rowCount()):
            if i in self._now:
                self.table.item(i, 0).setCheckState(Qt.Unchecked)     # de acum bifele = ce aplic
        self._apply_filter()

        def work(progress, cancelled):
            from .common import history
            for n, (i, t, need, artist, title) in enumerate(todo):
                if cancelled():
                    break
                try:
                    r = finder.find(artist, title, t.duration, need, hints=_hints(t))
                except RuntimeError as e:
                    progress(n, len(todo), json.dumps({"error": str(e)}))
                    return False
                self.results[i] = r
                history().mark([t.path], "taguri", save=(n % 10 == 9))
                progress(n + 1, len(todo), json.dumps({"row": i}))
            history().save()
            return True

        from .jobs import Job
        self.task = Job(f"Caut taguri online ({len(todo)} piese)", work, writes=False)
        self.task.progress.connect(self._progress)
        self.task.succeeded.connect(self._done)
        self.task.failed.connect(lambda m: (self._done(False), QMessageBox.warning(self, "Eroare", m)))
        self.progress.setMaximum(len(todo))
        self.progress.setValue(0)
        self.b_start.setText("Oprește")
        self.task.start()

    def _remember(self, i):
        from dataclasses import asdict
        from datetime import datetime
        r = self.results.get(i)
        if r is None:
            return
        d = asdict(r)
        d["when"] = datetime.now().isoformat(timespec="seconds")
        self._store[_rkey(self.tracks[i].path)] = d

    def _progress(self, d, total, payload):
        self.progress.setValue(d)
        data = json.loads(payload)
        if "error" in data:
            QMessageBox.warning(self, "Taguri", data["error"])
            return
        i = data["row"]
        self._remember(i)
        if d % 10 == 0:
            save_tag_results(self._store)
        ch = self._render(i)
        self.table.setItem(i, 8, _item("acum", "#27ae60"))
        if ch and self.c_auto.isChecked():
            job = self._job_for(i)
            if job:
                self._pending.append(job)
                self._remember_written(i, job)
                self._written_rows.add(i)
                self._render(i)
                self.table.setItem(i, 8, _item("✓ scrise", "#27ae60"))
            ch = False                       # deja scrise: nu mai e nimic de aplicat
            if len(self._pending) >= 10:
                self._flush()
        self.table.item(i, 0).setCheckState(Qt.Checked if ch else Qt.Unchecked)
        if self.c_new_only.isChecked():
            self.table.setRowHidden(i, not self._job_for(i))
        self.status.setText(f"{d} / {total} verificate" +
                            (f" · tagurile scrise automat la {self._written + len(self._pending)} piese"
                             if self.c_auto.isChecked() else ""))

    def _done(self, _):
        self.b_start.setText("Caută din nou")
        self.b_start.setEnabled(True)
        save_tag_results(self._store)
        self._flush(final=True)
        found = sum(1 for i in self._now if self.results.get(i) is not None and self.results[i].source)
        empty = sum(1 for i in self._now if self.results.get(i) is not None and not self.results[i].source)
        todo = sum(1 for i in range(self.table.rowCount()) if self.table.item(i, 0).checkState() == Qt.Checked)
        msg = f"Gata: găsite {found}, negăsite {empty} (motivul e în „Găsit în”)."
        if self.c_auto.isChecked():
            msg += f" Am scris automat tagurile la {self._written} piese."
        if todo:
            msg += f" {todo} au informații noi neaplicate — „Aplică bifatele”."
        self.status.setText(msg)

    def _job_for(self, i):
        """(cale, câmpuri, copertă) de scris pentru rândul i, sau None."""
        if i not in self.results:
            return None
        t, r = self.tracks[i], self.results[i]
        over = self.c_over.isChecked()
        f = {}
        if self.c_genre.isChecked():
            if r.genre and (over or not t.genre) and r.genre != t.genre:
                f["genre"] = r.genre
            old_style, role, energy = parse_comment(t.comment)
            if r.style_text and (over or not old_style) and r.style_text != old_style:
                f["comment"] = compose_comment(r.style_text, role, energy)   # păstrăm rolul și energia
        if self.c_year.isChecked() and r.year and (over or not t.year) and r.year != t.year:
            f["year"] = r.year
        if self.c_label.isChecked() and r.label and (over or not t.label) and r.label != t.label:
            f["label"] = r.label
        cover = (r.cover_urls or [r.cover_url]) if (self.c_cover.isChecked() and r.cover_url and
                                                   (over or not t.has_cover)) else None
        if i in self._derived and (f or cover):
            a, ti = self._derived[i]
            if a and not t.artist:
                f["artist"] = a
            if ti and not t.title:
                f["title"] = ti
        return (t.path, f, cover) if (f or cover) else None

    def _remember_written(self, i, job):
        t = self.tracks[i]
        for k, v in job[1].items():
            setattr(t, k, v)
        if job[2]:
            t.has_cover = True

    def _flush(self, final=False):
        """Scrie în fișiere ce s-a găsit (în loturi, ca activitate în fundal)."""
        if self._pending:
            batch, self._pending = self._pending, []
            self._written += len(batch)
            write_tags_in_background(self.parent() or self, batch,
                                     getattr(self, "on_applied", None) if final else None,
                                     "Scriu tagurile online")
        elif final and self._written and getattr(self, "on_applied", None):
            self.on_applied()

    def done(self, r):
        # la închidere: oprim căutarea, ținem minte rezultatele și scriem ce s-a găsit deja
        if self.task and self.task.isRunning():
            for sig in (self.task.progress, self.task.succeeded, self.task.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self.task.cancel()
        for i in self.results:
            self._remember(i)
        save_tag_results(self._store)
        self._flush(final=True)
        super().done(r)

    def apply(self):
        jobs = []
        for i in range(len(self.tracks)):
            if self.table.item(i, 0).checkState() != Qt.Checked:
                continue
            job = self._job_for(i)
            if job:
                jobs.append(job)
                self._remember_written(i, job)
                self._written_rows.add(i)
        if not jobs:
            QMessageBox.information(self, "Taguri", "Nu e nimic nou de scris la piesele bifate. "
                                    "Bifează „Suprascrie ce e deja completat” dacă vrei să înlocuiești ce există.")
            return
        write_tags_in_background(self.parent(), jobs, getattr(self, "on_applied", None),
                                 f"Scriu tagurile online")
        self.applied = True
        self._render_all()
        self.status.setText(f"Scriu tagurile la {len(jobs)} piese (vezi Activități). Fereastra rămâne deschisă.")


# --- nume curate: mutat în cleanup_dialogs --------------------------------------
def __getattr__(name):
    if name == "NameCleanupDialog":
        from .cleanup_dialogs import NameCleanupDialog
        return NameCleanupDialog
    raise AttributeError(name)


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
        add_space_toggle(self.table, 0)
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
        write_tags_in_background(self.parent(), jobs, getattr(self, "on_applied", None), "Scriu genurile")
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
        add_space_toggle(self.table, 0)
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
        write_tags_in_background(self.parent(), jobs, getattr(self, "on_applied", None), "Scriu artiștii")
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
        note = QLabel("BPM-ul se compară exact (62 nu intră la 120–128). "
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
