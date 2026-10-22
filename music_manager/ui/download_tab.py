"""Tabul „Download”: playlisturi Spotify → ce lipsește → Soulseek, plus lista de negăsite."""
from __future__ import annotations

import csv
import json
import os
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core import library as lib
from ..core import soulseek as slsk
from ..core import sources as src
from ..core import spotify as sp
from ..core.matching import Matcher, search_query
from ..core.sources import PlaylistStore, SavedPlaylist, track_from_dict, track_to_dict
from .common import AppState, Task, data_dir, fmt_duration, open_file, reveal_in_folder, settings
from .settings_tab import download_dir, slsk_credentials, slsk_quality, spotify_client

# stări (primele vin din core.sources.classify)
HAVE, IGNORED, NOTFOUND, DELETED, OLD, MISSING = (src.HAVE, src.IGNORED, src.NOTFOUND, src.DELETED,
                                                   src.OLD, src.MISSING)
QUEUED, SEARCHING, DOWNLOADING, DONE, FAILED = "queued", "searching", "downloading", "done", "failed"
STATUS_LABEL = {
    HAVE: ("✓ Ai deja", "#27ae60"),
    MISSING: ("Nouă — lipsește", "#e67e22"),
    OLD: ("Lipsește (adăugată înainte de ultimul download)", "#b9770e"),
    DELETED: ("Ștearsă de tine", "gray"),
    IGNORED: ("Ignorată", "gray"),
    NOTFOUND: ("Negăsită data trecută", "#c0392b"),
    QUEUED: ("În așteptare…", "#2980b9"),
    SEARCHING: ("Caut pe Soulseek…", "#2980b9"),
    DOWNLOADING: ("Se descarcă…", "#2980b9"),
    DONE: ("⬇ Descărcată", "#27ae60"),
    FAILED: ("✗ Negăsită", "#c0392b"),
}
DOWNLOADABLE = (MISSING, OLD, DELETED, NOTFOUND, FAILED)
ACTIVE = (QUEUED, SEARCHING, DOWNLOADING)
FILTERS = {
    "Noi de descărcat": (MISSING,) + ACTIVE,
    "Toate cele care lipsesc": (MISSING, OLD, NOTFOUND, FAILED) + ACTIVE,
    "Toate piesele": None,
    "Ce am deja": (HAVE, DONE),
    "Descărcate acum": (DONE,),
    "Adăugate înainte de ultimul download": (OLD,),
    "Șterse de mine": (DELETED,),
    "Negăsite": (NOTFOUND, FAILED),
    "Ignorate": (IGNORED,),
}
SEARCH_SITES = {
    "SoundCloud": "https://soundcloud.com/search?q={}",
    "Bandcamp": "https://bandcamp.com/search?q={}",
    "Beatport": "https://www.beatport.com/search?q={}",
    "YouTube": "https://www.youtube.com/results?search_query={}",
    "Google": "https://www.google.com/search?q={}",
}
C_CHECK, C_ARTIST, C_TITLE, C_DUR, C_STATUS, C_QA, C_QT, C_FILE = range(8)


@dataclass
class Row:
    track: sp.SpotifyTrack
    status: str
    path: str = ""
    artist_q: str = ""
    title_q: str = ""
    detail: str = ""
    playlist_id: str = ""


def _cell(text, editable=False, color=None, tooltip=None):
    it = QTableWidgetItem(text)
    flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
    if editable:
        flags |= Qt.ItemIsEditable
    it.setFlags(flags)
    if color:
        it.setForeground(QBrush(QColor(color)))
    if tooltip:
        it.setToolTip(tooltip)
    return it


def open_search(site: str, track: sp.SpotifyTrack):
    q = urllib.parse.quote_plus(f"{track.main_artist} {slsk.pretty_title(track.title)}")
    webbrowser.open(SEARCH_SITES[site].format(q))


# --------------------------------------------------------------------------
class DownloadRunner(QObject):
    """O singură descărcare odată, folosită de ambele subtaburi."""
    event = Signal(object, int, int)     # DownloadEvent, terminate, total
    started = Signal(str)
    finished = Signal(object)            # DownloadSummary sau None
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.task: Task | None = None
        self.owner = None
        self._running = False

    def busy(self) -> bool:
        return self._running

    def _succeeded(self, summary):
        self._running = False
        self.finished.emit(summary)

    def _failed(self, msg):
        self._running = False
        self.error.emit(msg)

    def start(self, owner, items: list[slsk.DownloadItem], wide=False) -> bool:
        if self.busy():
            QMessageBox.information(None, "Ocupat", "Se descarcă deja ceva. Așteaptă sau oprește descărcarea.")
            return False
        out = download_dir()
        if not out:
            QMessageBox.information(None, "Download", "Alege mai întâi folderul de download în Setări.")
            return False
        user, pw = slsk_credentials()
        if not user or not pw:
            QMessageBox.information(None, "Soulseek", "Completează utilizatorul și parola de Soulseek în Setări.")
            return False
        exe = slsk.sockseek_path(settings().value("slsk/sockseek_path", "") or "")
        if not exe:
            QMessageBox.warning(None, "Soulseek", "Nu găsesc Sockseek (programul care descarcă). Verifică în Setări.")
            return False
        quality = slsk_quality()
        work = os.path.join(data_dir(), "soulseek")
        self.owner = owner

        def work_fn(progress, cancelled):
            done = [0]

            def on_event(ev):
                if ev.kind in ("done", "failed"):
                    done[0] += 1
                progress(done[0], len(items), json.dumps(asdict(ev)))
            return slsk.run_download(items, out, user, pw, quality, exe, work, on_event, cancelled, wide=wide)

        self.task = Task(work_fn, self)
        self.task.progress.connect(self._progress)
        self.task.succeeded.connect(self._succeeded)
        self.task.failed.connect(self._failed)
        self._running = True
        self.started.emit(f"Descarc {len(items)} piese de pe Soulseek" + (" (căutare largă)" if wide else "") +
                          f" în {out}")
        self.task.start()
        return True

    def _progress(self, done, total, payload):
        try:
            ev = slsk.DownloadEvent(**json.loads(payload))
        except (ValueError, TypeError):
            return
        self.event.emit(ev, done, total)

    def stop(self, mode):
        if self.busy():
            self.task.cancel(mode)


# --------------------------------------------------------------------------
class FirstCheckDialog(QDialog):
    """La prima verificare a unui playlist: ce descarc din ce lipsește?"""

    def __init__(self, name: str, missing: int, oldest: datetime | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prima verificare")
        lbl = QLabel(f"E prima verificare pentru <b>{name}</b>. Lipsesc <b>{missing}</b> piese.<br><br>"
                     "De acum încolo programul ține minte ce ai avut, ca să nu redescarce ce ștergi tu. "
                     "Ce vrei să bifez pentru descărcare acum?")
        lbl.setWordWrap(True)
        self.rb_all = QRadioButton("Tot ce lipsește")
        self.rb_since = QRadioButton("Doar piesele adăugate în playlist după:")
        self.rb_none = QRadioButton("Nimic acum — doar de acum încolo (piesele adăugate de azi înainte)")
        g = QButtonGroup(self)
        for rb in (self.rb_all, self.rb_since, self.rb_none):
            g.addButton(rb)
        self.rb_all.setChecked(True)
        self.date = QDateEdit(QDate.currentDate().addYears(-1))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("d MMM yyyy")
        if oldest:
            self.date.setMinimumDate(QDate(oldest.year, oldest.month, oldest.day))
        self.date.dateChanged.connect(lambda _: self.rb_since.setChecked(True))
        row = QHBoxLayout()
        row.addWidget(self.rb_since)
        row.addWidget(self.date)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(lbl)
        lay.addWidget(self.rb_all)
        lay.addLayout(row)
        lay.addWidget(self.rb_none)
        lay.addWidget(bb)

    def baseline(self) -> str:
        if self.rb_all.isChecked():
            return "1970-01-01T00:00"
        if self.rb_none.isChecked():
            return datetime.now().isoformat(timespec="minutes")
        d = self.date.date()
        return datetime(d.year(), d.month(), d.day()).isoformat(timespec="minutes")


# --------------------------------------------------------------------------
class PlaylistButton(QFrame):
    clicked = Signal(str)
    action = Signal(str, str)

    def __init__(self, p: SavedPlaylist, parent=None):
        super().__init__(parent)
        self.pid = p.id
        self.setFrameShape(QFrame.StyledPanel)
        self.btn = QPushButton(p.name)
        self.btn.setStyleSheet("text-align:left; padding:6px 10px; font-weight:bold;")
        self.btn.setCheckable(True)
        self.btn.clicked.connect(lambda: self.clicked.emit(self.pid))
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        for label, key in (("Deschide în Spotify", "open"), ("Mută mai sus", "up"),
                           ("Mută mai jos", "down"), ("Scoate din listă", "remove")):
            menu.addAction(label, lambda k=key: self.action.emit(k, self.pid))
        more.setMenu(menu)
        info = p.last_update_label
        if p.last_update:
            info += f"<br>{p.last_total} piese · {p.last_missing} lipsă"
            if p.last_downloaded:
                info += f" · {p.last_downloaded} descărcate"
            if p.not_found:
                info += f" · {len(p.not_found)} negăsite"
        self.info = QLabel(info)
        self.info.setStyleSheet("color: gray; padding-left: 10px;")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.btn, 1)
        top.addWidget(more)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        lay.addLayout(top)
        lay.addWidget(self.info)

    def set_selected(self, on: bool):
        self.btn.setChecked(on)


# --------------------------------------------------------------------------
class TrackTableBase(QWidget):
    """Tabel comun pentru piese cu căutare editabilă și bife."""

    def _make_table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(24)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        t.setAlternatingRowColors(True)
        return t


class PlaylistsPanel(TrackTableBase):
    changed = Signal()      # s-a schimbat ceva în store (pentru lista de negăsite)

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.store: PlaylistStore = tab.store
        self.state: AppState = tab.state
        self.runner: DownloadRunner = tab.runner
        self.task: Task | None = None
        self.current: SavedPlaylist | None = None
        self.rows: list[Row] = []
        self._filling = False
        self._dl_idx: list[int] = []

        # --- stânga ---
        self.link = QLineEdit()
        self.link.setPlaceholderText("Lipește linkul unui playlist Spotify…")
        self.link.returnPressed.connect(self.add_playlist)
        b_add = QPushButton("Adaugă")
        b_add.clicked.connect(self.add_playlist)
        add_row = QHBoxLayout()
        add_row.addWidget(self.link, 1)
        add_row.addWidget(b_add)
        self.buttons_box = QVBoxLayout()
        self.buttons_box.setSpacing(6)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addLayout(self.buttons_box)
        hl.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        self.dest_label = QLabel()
        self.dest_label.setWordWrap(True)
        self.dest_label.setStyleSheet("color: gray;")
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Playlisturile tale</b>"))
        ll.addLayout(add_row)
        ll.addWidget(scroll, 1)
        ll.addWidget(self.dest_label)

        # --- dreapta ---
        self.title = QLabel("<i>Alege un playlist din stânga.</i>")
        self.b_check = QPushButton("Verifică din nou")
        self.b_check.clicked.connect(lambda: self.check(self.current.id) if self.current else None)
        self.filter = QComboBox()
        self.filter.addItems(FILTERS.keys())
        self.filter.currentIndexChanged.connect(self.apply_filter)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(QLabel("Arată:"))
        head.addWidget(self.filter)
        head.addWidget(self.b_check)

        self.table = self._make_table(["", "Artist", "Titlu", "Durată", "Stare",
                                       "Caută artist", "Caută titlu", "Fișier / detalii"])
        for c, w in ((C_CHECK, 28), (C_ARTIST, 170), (C_TITLE, 230), (C_DUR, 55), (C_STATUS, 190),
                     (C_QA, 140), (C_QT, 180)):
            self.table.setColumnWidth(c, w)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        self.b_download = QPushButton("Descarcă bifatele")
        self.b_download.setMinimumHeight(34)
        self.b_download.clicked.connect(self.download)
        b_all = QPushButton("Bifează ce e afișat")
        b_none = QPushButton("Debifează tot")
        b_ign = QPushButton("Ignoră selecția")
        b_unign = QPushButton("Nu mai ignora")
        b_all.clicked.connect(lambda: self._set_checks(True))
        b_none.clicked.connect(lambda: self._set_checks(False))
        b_ign.clicked.connect(lambda: self._ignore_selected(True))
        b_unign.clicked.connect(lambda: self._ignore_selected(False))
        actions = QHBoxLayout()
        for b in (b_all, b_none, b_ign, b_unign):
            actions.addWidget(b)
        actions.addStretch()
        actions.addWidget(self.b_download)
        self.summary = QLabel()
        hint = QLabel("Bifate automat sunt doar piesele <b>noi</b> (adăugate după ultimul download). "
                      "Cele șterse de tine nu se mai bifează. Dublu-click pe „Caută artist / titlu” ca să "
                      "schimbi textul căutat; click dreapta pentru căutare pe SoundCloud, Bandcamp etc.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(head)
        rl.addWidget(self.summary)
        rl.addWidget(self.table, 1)
        rl.addWidget(hint)
        rl.addLayout(actions)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([280, 1000])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        self.runner.event.connect(self._on_event)
        self.runner.finished.connect(self._on_download_done)
        self.runner.error.connect(self._on_download_error)
        self.refresh_buttons()
        self._update_actions()

    # --- playlisturi salvate ---
    def refresh_buttons(self):
        while self.buttons_box.count():
            w = self.buttons_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for p in self.store.items:
            b = PlaylistButton(p)
            b.clicked.connect(self.check)
            b.action.connect(self._playlist_action)
            b.set_selected(bool(self.current and self.current.id == p.id))
            self.buttons_box.addWidget(b)
        if not self.store.items:
            lbl = QLabel("Niciun playlist încă. Lipește mai sus linkul unui playlist Spotify "
                         "(⋯ → Share → Copy link to playlist).")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: gray;")
            self.buttons_box.addWidget(lbl)
        d = download_dir()
        self.dest_label.setText(f"Piesele noi se descarcă în:<br><b>{d}</b>" if d else
                                "<span style='color:#c0392b'>Alege folderul de download în Setări.</span>")

    def _playlist_action(self, key, pid):
        p = self.store.get(pid)
        if not p:
            return
        if key == "open":
            webbrowser.open(p.url or f"https://open.spotify.com/playlist/{pid}")
        elif key in ("up", "down"):
            self.store.move(pid, -1 if key == "up" else 1)
            self.refresh_buttons()
        elif key == "remove":
            if QMessageBox.question(self, "Scoate", f"Scot „{p.name}” din listă? "
                                    "(Nu se șterge nimic de pe Spotify sau de pe disc.)") == QMessageBox.Yes:
                self.store.remove(pid)
                if self.current and self.current.id == pid:
                    self.current, self.rows = None, []
                    self.fill_table()
                self.refresh_buttons()
                self.changed.emit()

    def add_playlist(self):
        pid = sp.parse_playlist_id(self.link.text())
        if not pid:
            QMessageBox.information(self, "Playlist", "Linkul nu pare a fi un playlist Spotify.\n"
                                    "În Spotify: ⋯ → Share → Copy link to playlist.")
            return
        url = self.link.text().strip()
        self.link.clear()
        if not self.store.get(pid):
            self.store.add(SavedPlaylist(id=pid, name="(se încarcă…)", url=url))
            self.refresh_buttons()
        self.check(pid)

    # --- verificare ---
    def check(self, pid: str):
        if (self.task and self.task.isRunning()) or self.runner.busy():
            QMessageBox.information(self, "Ocupat", "Aștept să se termine operația în curs.")
            self.refresh_buttons()
            return
        client = spotify_client()
        if not (client and client.logged_in):
            QMessageBox.information(self, "Spotify", "Mai întâi conectează Spotify din tabul Setări.")
            self.refresh_buttons()
            return
        saved = self.store.get(pid)
        if not saved:
            return
        self.current = saved
        self.refresh_buttons()
        folders = [f for f in self.state.folders if os.path.isdir(f)]
        dl = download_dir()
        if dl and os.path.isdir(dl) and not any(
                os.path.normcase(os.path.abspath(dl)) == os.path.normcase(os.path.abspath(f)) or
                os.path.normcase(os.path.abspath(dl)).startswith(os.path.normcase(os.path.abspath(f)) + os.sep)
                for f in folders):
            folders.append(dl)
        if not folders:
            QMessageBox.information(self, "Foldere", "Adaugă folderele cu muzica ta în Bibliotecă → Piese "
                                    "(sau alege folderul de download în Setări), ca să știu ce ai deja.")
            return

        def work(progress, cancelled):
            progress(0, 0, "Citesc playlistul din Spotify…")
            pl = client.get_playlist(pid, lambda d, t: progress(d, t, f"Citesc playlistul: {d}/{t}"))
            progress(0, 0, "Compar cu muzica ta…")
            cache = lib.LibraryCache(os.path.join(data_dir(), "biblioteca.json"))
            local = lib.scan(folders, cache, lambda d, t: progress(d, t, f"Verific fișierele: {d}/{t}"),
                             cancelled)
            matcher = Matcher(local)
            return pl, {t.id: matcher.find(t) for t in pl.tracks}

        self.task = Task(work, self)
        self.task.progress.connect(self.tab.show_progress)
        self.task.succeeded.connect(self._on_checked)
        self.task.failed.connect(self._on_check_failed)
        self.tab.set_busy(f"Verific „{saved.name}”…", stoppable=False)
        self.task.start()

    def _on_check_failed(self, msg):
        self.tab.set_idle()
        self.refresh_buttons()
        QMessageBox.warning(self, "Eroare", msg)

    def _on_checked(self, result):
        pl, found = result
        saved = self.store.get(pl.id) or self.current
        saved.name = pl.name
        self.tab.set_idle()

        # unice + completăm detaliile negăsitelor din versiunea veche
        tracks, ids = [], set()
        for t in pl.tracks:
            if t.id not in ids:
                ids.add(t.id)
                tracks.append(t)
        for t in tracks:
            nf = saved.not_found.get(t.id)
            if nf is not None and not nf.get("track"):
                nf["track"] = track_to_dict(t)
            if nf is not None and found.get(t.id):      # între timp ai găsit-o
                saved.not_found.pop(t.id, None)

        # prima verificare: întrebăm ce să bifăm
        if not saved.baseline:
            missing = [t for t in tracks if not found.get(t.id) and t.id not in saved.ignored]
            dates = [src._parse_dt(t.added_at) for t in tracks if t.added_at]
            dates = [d for d in dates if d]
            if missing:
                dlg = FirstCheckDialog(saved.name, len(missing), min(dates) if dates else None, self)
                dlg.exec()
                saved.baseline = dlg.baseline()
            else:
                saved.baseline = datetime.now().isoformat(timespec="minutes")

        rows = []
        for t in tracks:
            local = found.get(t.id)
            aq, tq = saved.custom_queries.get(t.id) or search_query(t)
            status = src.classify(saved, t, bool(local))
            detail = ""
            if status == NOTFOUND:
                detail = saved.not_found[t.id].get("reason", "")
            rows.append(Row(t, status, local.path if local else "", aq, tq, detail, saved.id))
        saved.mark_seen(r.track.id for r in rows if r.status == HAVE)

        self.rows = rows
        counts = {k: sum(1 for r in rows if r.status == k) for k in (HAVE, MISSING, OLD, DELETED, NOTFOUND, IGNORED)}
        saved.last_update = datetime.now().isoformat(timespec="minutes")
        saved.last_total = len(rows)
        saved.last_missing = counts[MISSING]
        saved.last_downloaded = 0
        self.store.save()
        self.current = saved
        self.refresh_buttons()
        parts = [f"<b>{len(rows)}</b> piese", f"<b>{counts[HAVE]}</b> le ai",
                 f"<b style='color:#e67e22'>{counts[MISSING]}</b> noi de descărcat"]
        if counts[OLD]:
            parts.append(f"{counts[OLD]} mai vechi care lipsesc")
        if counts[DELETED]:
            parts.append(f"{counts[DELETED]} șterse de tine")
        if counts[NOTFOUND]:
            parts.append(f"{counts[NOTFOUND]} negăsite data trecută")
        if pl.skipped:
            parts.append(f"{pl.skipped} sărite (fișiere locale/indisponibile)")
        self.summary.setText(" · ".join(parts))
        self.filter.setCurrentIndex(0)
        self.fill_table(auto_check=True)
        self.changed.emit()

    # --- tabel ---
    def fill_table(self, auto_check=False):
        prev = {}
        if not auto_check:
            for i in range(min(self.table.rowCount(), len(self.rows))):
                it = self.table.item(i, C_CHECK)
                prev[i] = bool(it and it.checkState() == Qt.Checked)
        self._filling = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            chk = QTableWidgetItem()
            if r.status in DOWNLOADABLE:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                on = (r.status == MISSING) if auto_check else prev.get(i, False)
                chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(i, C_CHECK, chk)
            self._fill_row(i)
        self.table.setUpdatesEnabled(True)
        self._filling = False
        if self.current:
            self.title.setText(f"<b style='font-size:14px'>{self.current.name}</b>")
        else:
            self.title.setText("<i>Alege un playlist din stânga.</i>")
            self.summary.setText("")
        self.apply_filter()
        self._update_actions()

    def _fill_row(self, i):
        r = self.rows[i]
        was = self._filling
        self._filling = True
        label, color = STATUS_LABEL[r.status]
        added = src._parse_dt(r.track.added_at)
        self.table.setItem(i, C_ARTIST, _cell(r.track.artist))
        self.table.setItem(i, C_TITLE, _cell(r.track.title, tooltip=f"{r.track.album}\nAdăugată: "
                                             f"{added:%d.%m.%Y}" if added else r.track.album))
        self.table.setItem(i, C_DUR, _cell(fmt_duration(r.track.duration)))
        self.table.setItem(i, C_STATUS, _cell(label, color=color))
        editable = r.status in DOWNLOADABLE
        self.table.setItem(i, C_QA, _cell(r.artist_q, editable, tooltip="Dublu-click pentru a modifica"))
        self.table.setItem(i, C_QT, _cell(r.title_q, editable, tooltip="Dublu-click pentru a modifica"))
        detail = os.path.basename(r.path) if r.path else r.detail
        self.table.setItem(i, C_FILE, _cell(detail, tooltip=r.path or r.detail))
        if r.status not in DOWNLOADABLE:
            chk = self.table.item(i, C_CHECK)
            if chk and chk.flags() & Qt.ItemIsUserCheckable:
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setData(Qt.CheckStateRole, None)
        elif r.status in DOWNLOADABLE:
            chk = self.table.item(i, C_CHECK)
            if chk and not (chk.flags() & Qt.ItemIsUserCheckable):
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Unchecked)
        self._filling = was

    def apply_filter(self):
        allowed = FILTERS[self.filter.currentText()]
        for i, r in enumerate(self.rows):
            self.table.setRowHidden(i, allowed is not None and r.status not in allowed)

    def _item_changed(self, item):
        if self._filling or not self.current:
            return
        i, c = item.row(), item.column()
        if i >= len(self.rows):
            return
        r = self.rows[i]
        if c in (C_QA, C_QT):
            if c == C_QA:
                r.artist_q = item.text().strip()
            else:
                r.title_q = item.text().strip()
            if (r.artist_q, r.title_q) != search_query(r.track):
                self.current.custom_queries[r.track.id] = [r.artist_q, r.title_q]
            else:
                self.current.custom_queries.pop(r.track.id, None)
            self.store.save()
            chk = self.table.item(i, C_CHECK)
            if chk and chk.flags() & Qt.ItemIsUserCheckable:
                chk.setCheckState(Qt.Checked)
        self._update_actions()

    def _double_clicked(self, i, c):
        if i < len(self.rows) and c not in (C_QA, C_QT) and self.rows[i].path:
            if c == C_FILE:
                reveal_in_folder(self.rows[i].path)
            else:
                open_file(self.rows[i].path)

    def _context_menu(self, pos):
        i = self.table.rowAt(pos.y())
        if i < 0 or i >= len(self.rows):
            return
        r = self.rows[i]
        m = QMenu(self)
        for site in SEARCH_SITES:
            m.addAction(f"Caută pe {site}", lambda s=site: open_search(s, r.track))
        m.addSeparator()
        m.addAction("Copiază „Artist - Titlu”",
                    lambda: QGuiApplication.clipboard().setText(f"{r.track.artist} - {r.track.title}"))
        if r.path:
            m.addAction("Arată în folder", lambda: reveal_in_folder(r.path))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _checked_rows(self) -> list[int]:
        out = []
        for i, r in enumerate(self.rows):
            it = self.table.item(i, C_CHECK)
            if r.status in DOWNLOADABLE and it and it.checkState() == Qt.Checked:
                out.append(i)
        return out

    def _set_checks(self, on: bool):
        self._filling = True
        for i, r in enumerate(self.rows):
            it = self.table.item(i, C_CHECK)
            if r.status in DOWNLOADABLE and it and not self.table.isRowHidden(i):
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._filling = False
        self._update_actions()

    def _ignore_selected(self, ignore: bool):
        if not self.current:
            return
        for i in sorted({ix.row() for ix in self.table.selectedIndexes()}):
            r = self.rows[i]
            if ignore and r.status in DOWNLOADABLE:
                r.status = IGNORED
                if r.track.id not in self.current.ignored:
                    self.current.ignored.append(r.track.id)
                self.current.not_found.pop(r.track.id, None)
            elif not ignore and r.status == IGNORED:
                if r.track.id in self.current.ignored:
                    self.current.ignored.remove(r.track.id)
                r.status = src.classify(self.current, r.track, False)
        self.store.save()
        self.fill_table()
        self.changed.emit()

    def _update_actions(self):
        if self.runner.busy() and self.runner.owner is self:
            return
        n = len(self._checked_rows())
        self.b_download.setText(f"Descarcă bifatele ({n})")
        self.b_download.setEnabled(n > 0 and not self.runner.busy())
        self.b_check.setEnabled(self.current is not None)

    # --- descărcare ---
    def download(self):
        idx = self._checked_rows()
        if not idx or not self.current:
            return
        items = [slsk.DownloadItem(self.rows[i].track, self.rows[i].artist_q, self.rows[i].title_q) for i in idx]
        if not self.runner.start(self, items):
            return
        self._dl_idx = idx
        self._dl_playlist = self.current
        self.current.baseline = datetime.now().isoformat(timespec="minutes")
        self.store.save()
        for i in idx:
            self.rows[i].status = QUEUED
            self._fill_row(i)
        self.b_download.setEnabled(False)

    def _on_event(self, ev, done, total):
        if self.runner.owner is not self or ev.kind == "log" or not (0 <= ev.index < len(self._dl_idx)):
            return
        i = self._dl_idx[ev.index]
        if i >= len(self.rows):
            return
        r = self.rows[i]
        if ev.kind == "searching":
            r.status, r.detail = SEARCHING, ""
        elif ev.kind == "downloading":
            r.status, r.detail = DOWNLOADING, ev.text
        elif ev.kind == "progress":
            r.status, r.detail = DOWNLOADING, f"{ev.percent:.0f}%"
        elif ev.kind == "done":
            r.status, r.path, r.detail = DONE, ev.path, ev.text
        elif ev.kind == "failed":
            r.status, r.detail = FAILED, ev.text
        self._fill_row(i)

    def _on_download_done(self, summary):
        if self.runner.owner is not self:
            return
        cur = getattr(self, "_dl_playlist", None)
        now = datetime.now().isoformat(timespec="minutes")
        for i in self._dl_idx:
            if i >= len(self.rows):
                continue
            r = self.rows[i]
            if r.status in ACTIVE:
                r.status = src.classify(cur, r.track, False) if cur else MISSING
                r.detail = "oprită" if summary and summary.cancelled else ""
            if cur:
                if r.status == DONE:
                    cur.not_found.pop(r.track.id, None)
                    cur.mark_seen([r.track.id])
                elif r.status == FAILED:
                    code = next((f[2] for f in (summary.failed if summary else [])
                                 if 0 <= f[0] < len(self._dl_idx) and self._dl_idx[f[0]] == i), "")
                    cur.not_found[r.track.id] = {"reason": r.detail, "code": code, "date": now,
                                                 "track": track_to_dict(r.track),
                                                 "query": [r.artist_q, r.title_q]}
        if cur:
            cur.last_downloaded += len(summary.done) if summary else 0
            cur.last_missing = sum(1 for r in self.rows if r.status == MISSING)
            self.store.save()
        self.refresh_buttons()
        self.fill_table()
        self.changed.emit()

    def _on_download_error(self, msg):
        if self.runner.owner is not self:
            return
        for i in self._dl_idx:
            if i < len(self.rows) and self.rows[i].status in ACTIVE:
                self.rows[i].status = src.classify(self.current, self.rows[i].track, False) if self.current else MISSING
        self.fill_table()


# --------------------------------------------------------------------------
N_CHECK, N_PL, N_ARTIST, N_TITLE, N_REASON, N_DATE, N_QA, N_QT, N_RESULT = range(9)


class NotFoundPanel(TrackTableBase):
    """Toate piesele negăsite, din toate playlisturile, într-un singur loc."""
    changed = Signal()

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.store: PlaylistStore = tab.store
        self.runner: DownloadRunner = tab.runner
        self.rows: list[Row] = []
        self._filling = False
        self._dl_idx: list[int] = []

        info = QLabel("Piesele pe care Soulseek nu le-a avut. Poți încerca din nou (eventual cu alt text de căutare "
                      "sau cu „căutare mai largă”), le poți căuta pe alte site-uri, sau le scoți din listă când "
                      "le-ai găsit altundeva. <i>„Există, dar nu în calitatea cerută”</i> înseamnă că piesa e pe "
                      "Soulseek, dar doar sub 256 kbps sau cu altă durată.")
        info.setWordWrap(True)
        self.filter_pl = QComboBox()
        self.filter_pl.currentIndexChanged.connect(self.apply_filter)
        top = QHBoxLayout()
        top.addWidget(QLabel("Playlist:"))
        top.addWidget(self.filter_pl)
        top.addStretch()
        self.count = QLabel()
        top.addWidget(self.count)

        self.table = self._make_table(["", "Playlist", "Artist", "Titlu", "Motiv", "Data",
                                       "Caută artist", "Caută titlu", "Rezultat"])
        for c, w in ((N_CHECK, 28), (N_PL, 120), (N_ARTIST, 160), (N_TITLE, 220), (N_REASON, 220),
                     (N_DATE, 80), (N_QA, 130), (N_QT, 170)):
            self.table.setColumnWidth(c, w)
        self.table.itemChanged.connect(self._item_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellDoubleClicked.connect(self._double_clicked)

        b_retry = QPushButton("Încearcă din nou bifatele")
        b_wide = QPushButton("Încearcă cu căutare mai largă")
        b_wide.setToolTip("Mai lent, acceptă durate mai diferite și caută și fără paranteze / feat.")
        b_retry.clicked.connect(lambda: self.retry(False))
        b_wide.clicked.connect(lambda: self.retry(True))
        self.b_retry, self.b_wide = b_retry, b_wide
        b_all = QPushButton("Bifează tot")
        b_none = QPushButton("Debifează")
        b_all.clicked.connect(lambda: self._set_checks(True))
        b_none.clicked.connect(lambda: self._set_checks(False))

        sites = QHBoxLayout()
        sites.addWidget(QLabel("Caută selecția pe:"))
        for site in SEARCH_SITES:
            b = QPushButton(site)
            b.clicked.connect(lambda _=False, s=site: self._search_selected(s))
            sites.addWidget(b)
        sites.addStretch()

        b_found = QPushButton("Am găsit-o altundeva (scoate)")
        b_ignore = QPushButton("Ignoră definitiv")
        b_copy = QPushButton("Copiază lista")
        b_csv = QPushButton("Exportă CSV…")
        b_found.clicked.connect(lambda: self._remove_selected(ignore=False))
        b_ignore.clicked.connect(lambda: self._remove_selected(ignore=True))
        b_copy.clicked.connect(self._copy)
        b_csv.clicked.connect(self._export)

        row1 = QHBoxLayout()
        for b in (b_all, b_none):
            row1.addWidget(b)
        row1.addStretch()
        row1.addWidget(b_wide)
        row1.addWidget(b_retry)
        row2 = QHBoxLayout()
        for b in (b_found, b_ignore):
            row2.addWidget(b)
        row2.addStretch()
        row2.addWidget(b_copy)
        row2.addWidget(b_csv)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(info)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(sites)
        lay.addLayout(row1)
        lay.addLayout(row2)

        self.runner.event.connect(self._on_event)
        self.runner.finished.connect(self._on_done)
        self.runner.error.connect(lambda m: self._on_done(None) if self.runner.owner is self else None)
        self.reload()

    def reload(self):
        if self.runner.busy() and self.runner.owner is self:
            return
        rows = []
        for p in self.store.items:
            for tid, nf in list(p.not_found.items()):
                if not isinstance(nf, dict):
                    nf = p.not_found[tid] = {"reason": str(nf), "code": "", "date": "", "track": None}
                tdict = nf.get("track")
                if tdict:
                    t = track_from_dict(tdict)
                else:
                    t = sp.SpotifyTrack(id=tid, title="(verifică playlistul ca să apară detaliile)", artists=[])
                q = p.custom_queries.get(tid) or nf.get("query") or list(search_query(t))
                r = Row(t, FAILED, "", q[0], q[1], nf.get("reason", ""), p.id)
                r.date = nf.get("date", "")
                rows.append(r)
        self.rows = rows
        cur = self.filter_pl.currentData()
        self.filter_pl.blockSignals(True)
        self.filter_pl.clear()
        self.filter_pl.addItem("Toate", "")
        for p in self.store.items:
            if p.not_found:
                self.filter_pl.addItem(f"{p.name} ({len(p.not_found)})", p.id)
        i = self.filter_pl.findData(cur)
        self.filter_pl.setCurrentIndex(max(i, 0))
        self.filter_pl.blockSignals(False)
        self.fill()

    def _playlist_name(self, pid):
        p = self.store.get(pid)
        return p.name if p else ""

    def fill(self):
        self._filling = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(i, N_CHECK, chk)
            self._fill_row(i)
        self.table.setUpdatesEnabled(True)
        self._filling = False
        self.apply_filter()

    def _fill_row(self, i):
        r = self.rows[i]
        was = self._filling
        self._filling = True
        has_track = bool(r.track.artists)
        self.table.setItem(i, N_PL, _cell(self._playlist_name(r.playlist_id)))
        self.table.setItem(i, N_ARTIST, _cell(r.track.artist))
        self.table.setItem(i, N_TITLE, _cell(r.track.title))
        reason_color = "#c0392b" if r.status == FAILED else STATUS_LABEL.get(r.status, ("", None))[1]
        self.table.setItem(i, N_REASON, _cell(r.detail, color=reason_color, tooltip=r.detail))
        d = src._parse_dt(getattr(r, "date", ""))
        self.table.setItem(i, N_DATE, _cell(f"{d:%d.%m.%Y}" if d else ""))
        self.table.setItem(i, N_QA, _cell(r.artist_q, has_track))
        self.table.setItem(i, N_QT, _cell(r.title_q, has_track))
        res = STATUS_LABEL[r.status][0] if r.status in ACTIVE + (DONE,) else ""
        if r.status == DONE:
            res += " → " + os.path.basename(r.path)
        self.table.setItem(i, N_RESULT, _cell(res, color=STATUS_LABEL.get(r.status, ("", None))[1],
                                              tooltip=r.path))
        self._filling = was

    def apply_filter(self):
        pid = self.filter_pl.currentData()
        shown = 0
        for i, r in enumerate(self.rows):
            hide = bool(pid) and r.playlist_id != pid
            self.table.setRowHidden(i, hide)
            shown += 0 if hide else 1
        self.count.setText(f"{shown} negăsite")
        self.tab.update_notfound_title(len(self.rows))

    def _item_changed(self, item):
        if self._filling:
            return
        i, c = item.row(), item.column()
        if c not in (N_QA, N_QT) or i >= len(self.rows):
            return
        r = self.rows[i]
        if c == N_QA:
            r.artist_q = item.text().strip()
        else:
            r.title_q = item.text().strip()
        p = self.store.get(r.playlist_id)
        if p:
            p.custom_queries[r.track.id] = [r.artist_q, r.title_q]
            if r.track.id in p.not_found:
                p.not_found[r.track.id]["query"] = [r.artist_q, r.title_q]
            self.store.save()
        chk = self.table.item(i, N_CHECK)
        if chk:
            chk.setCheckState(Qt.Checked)

    def _selected(self) -> list[int]:
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()})
        return [i for i in rows if not self.table.isRowHidden(i)]

    def _checked(self) -> list[int]:
        return [i for i in range(len(self.rows))
                if not self.table.isRowHidden(i) and self.table.item(i, N_CHECK).checkState() == Qt.Checked
                and self.rows[i].track.artists]

    def _set_checks(self, on):
        for i in range(len(self.rows)):
            if not self.table.isRowHidden(i):
                self.table.item(i, N_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _search_selected(self, site):
        sel = self._selected() or self._checked()
        if not sel:
            QMessageBox.information(self, "Caută", "Selectează una sau mai multe piese din tabel.")
            return
        if len(sel) > 8 and QMessageBox.question(self, "Caută", f"Deschid {len(sel)} pagini în browser?") != QMessageBox.Yes:
            return
        for i in sel:
            if self.rows[i].track.artists:
                open_search(site, self.rows[i].track)

    def _context_menu(self, pos):
        i = self.table.rowAt(pos.y())
        if i < 0 or i >= len(self.rows):
            return
        r = self.rows[i]
        m = QMenu(self)
        for site in SEARCH_SITES:
            m.addAction(f"Caută pe {site}", lambda s=site: open_search(s, r.track))
        m.addSeparator()
        m.addAction("Copiază „Artist - Titlu”",
                    lambda: QGuiApplication.clipboard().setText(f"{r.track.artist} - {r.track.title}"))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _double_clicked(self, i, c):
        if i < len(self.rows) and self.rows[i].path and c not in (N_QA, N_QT):
            reveal_in_folder(self.rows[i].path)

    def _remove_selected(self, ignore: bool):
        sel = self._selected()
        if not sel:
            QMessageBox.information(self, "Negăsite", "Selectează piesele din tabel.")
            return
        for i in sel:
            r = self.rows[i]
            p = self.store.get(r.playlist_id)
            if not p:
                continue
            p.not_found.pop(r.track.id, None)
            if ignore:
                if r.track.id not in p.ignored:
                    p.ignored.append(r.track.id)
            else:
                p.mark_seen([r.track.id])     # o ai acum (din altă sursă)
        self.store.save()
        self.reload()
        self.changed.emit()

    def _lines(self):
        return [r for i, r in enumerate(self.rows) if not self.table.isRowHidden(i) and r.track.artists]

    def _copy(self):
        txt = "\n".join(f"{r.track.artist} - {r.track.title}" for r in self._lines())
        QGuiApplication.clipboard().setText(txt)
        QMessageBox.information(self, "Copiat", "Lista e în clipboard (Ctrl+V oriunde).")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportă negăsitele", "negasite.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Playlist", "Artist", "Titlu", "Album", "Durata", "Motiv", "Spotify"])
            for r in self._lines():
                w.writerow([self._playlist_name(r.playlist_id), r.track.artist, r.track.title, r.track.album,
                            fmt_duration(r.track.duration), r.detail, f"https://open.spotify.com/track/{r.track.id}"])

    # --- reîncercare ---
    def retry(self, wide: bool):
        idx = self._checked()
        if not idx:
            QMessageBox.information(self, "Negăsite", "Bifează piesele pe care vrei să le încerci din nou.")
            return
        items = [slsk.DownloadItem(self.rows[i].track, self.rows[i].artist_q, self.rows[i].title_q) for i in idx]
        if not self.runner.start(self, items, wide=wide):
            return
        self._dl_idx = idx
        for i in idx:
            self.rows[i].status = QUEUED
            self._fill_row(i)

    def _on_event(self, ev, done, total):
        if self.runner.owner is not self or ev.kind == "log" or not (0 <= ev.index < len(self._dl_idx)):
            return
        i = self._dl_idx[ev.index]
        r = self.rows[i]
        if ev.kind == "searching":
            r.status = SEARCHING
        elif ev.kind in ("downloading", "progress"):
            r.status = DOWNLOADING
        elif ev.kind == "done":
            r.status, r.path = DONE, ev.path
        elif ev.kind == "failed":
            r.status, r.detail = FAILED, ev.text
        self._fill_row(i)

    def _on_done(self, summary):
        if self.runner.owner is not self:
            return
        now = datetime.now().isoformat(timespec="minutes")
        for i in self._dl_idx:
            r = self.rows[i]
            p = self.store.get(r.playlist_id)
            if r.status in ACTIVE:
                r.status = FAILED
            if not p:
                continue
            if r.status == DONE:
                p.not_found.pop(r.track.id, None)
                p.mark_seen([r.track.id])
            elif r.track.id in p.not_found:
                p.not_found[r.track.id].update({"reason": r.detail, "date": now,
                                                "query": [r.artist_q, r.title_q]})
        self.store.save()
        done = [self.rows[i] for i in self._dl_idx if self.rows[i].status == DONE]
        self._dl_idx = []
        self.changed.emit()
        if done:
            QMessageBox.information(self, "Negăsite", f"Am găsit și descărcat {len(done)} piese. "
                                    "Le scot din lista de negăsite.")
        self.reload()


# --------------------------------------------------------------------------
class DownloadTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.store = PlaylistStore(os.path.join(data_dir(), "playlisturi.json"))
        self.runner = DownloadRunner(self)

        self.inner = QTabWidget()
        self.playlists = PlaylistsPanel(self)
        self.notfound = NotFoundPanel(self)
        self.inner.addTab(self.playlists, "Playlisturi")
        self.inner.addTab(self.notfound, "Negăsite")
        self.playlists.changed.connect(self.notfound.reload)
        self.notfound.changed.connect(self.playlists.refresh_buttons)
        self.notfound.changed.connect(self._refresh_current_playlist_rows)

        # zona de jos, comună: progres + oprire + jurnal
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.b_stop_soft = QPushButton("Oprește după piesele în lucru")
        self.b_stop_hard = QPushButton("Oprește acum")
        self.b_stop_soft.setToolTip("Termină de descărcat fișierele începute, apoi se oprește.")
        self.b_stop_hard.setToolTip("Oprește imediat; fișierele începute și neterminate se șterg.")
        self.b_stop_soft.clicked.connect(lambda: self._stop(slsk.STOP_AFTER_CURRENT))
        self.b_stop_hard.clicked.connect(lambda: self._stop(slsk.STOP_NOW))
        for b in (self.b_stop_soft, self.b_stop_hard):
            b.setVisible(False)
        srow = QHBoxLayout()
        srow.addWidget(self.status, 1)
        srow.addWidget(self.b_stop_soft)
        srow.addWidget(self.b_stop_hard)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(120)

        lay = QVBoxLayout(self)
        lay.addWidget(self.inner, 1)
        lay.addWidget(self.progress)
        lay.addLayout(srow)
        lay.addWidget(self.log)

        self.runner.started.connect(self._dl_started)
        self.runner.event.connect(self._dl_event)
        self.runner.finished.connect(self._dl_finished)
        self.runner.error.connect(self._dl_error)
        self.update_notfound_title(len(self.notfound.rows))

    # folosite de MainWindow
    @property
    def task(self):
        return self.runner.task if self.runner.busy() else self.playlists.task

    def refresh_buttons(self):
        self.playlists.refresh_buttons()

    def update_notfound_title(self, n):
        if hasattr(self, "inner"):
            self.inner.setTabText(1, f"Negăsite ({n})" if n else "Negăsite")

    def _refresh_current_playlist_rows(self):
        p = self.playlists
        if not p.current or self.runner.busy():
            return
        for r in p.rows:
            if r.status in (NOTFOUND, FAILED) and r.track.id not in p.current.not_found:
                r.status = HAVE if r.track.id in p.current.seen else src.classify(p.current, r.track, False)
        p.fill_table()

    # --- stare comună ---
    def show_progress(self, done, total, text):
        self.progress.setVisible(True)
        self.progress.setMaximum(total if total else 0)
        self.progress.setValue(done)
        if text:
            self.status.setText(text)

    def set_busy(self, text, stoppable):
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.status.setText(text)
        for b in (self.b_stop_soft, self.b_stop_hard):
            b.setVisible(stoppable)
            b.setEnabled(True)
        self.playlists.b_check.setEnabled(False)

    def set_idle(self):
        self.progress.setVisible(False)
        for b in (self.b_stop_soft, self.b_stop_hard):
            b.setVisible(False)
        self.playlists.b_check.setEnabled(self.playlists.current is not None)

    def _stop(self, mode):
        self.runner.stop(mode)
        if mode == slsk.STOP_NOW:
            self.status.setText("Opresc acum și șterg fișierele începute…")
            self.b_stop_soft.setEnabled(False)
            self.b_stop_hard.setEnabled(False)
        else:
            self.status.setText("Termin piesele care se descarcă acum, apoi mă opresc…")
            self.b_stop_soft.setEnabled(False)

    def _dl_started(self, text):
        self.set_busy(text, stoppable=True)
        self.log.appendPlainText("— " + text + " —")

    def _dl_event(self, ev, done, total):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        if ev.kind == "log":
            if ev.text.strip():
                self.log.appendPlainText(ev.text.strip())
            return
        owner = self.runner.owner
        name = ""
        idx = getattr(owner, "_dl_idx", [])
        if 0 <= ev.index < len(idx) and idx[ev.index] < len(owner.rows):
            name = owner.rows[idx[ev.index]].track.label
        if ev.kind == "done":
            self.log.appendPlainText(f"✓ {name} → {os.path.basename(ev.path)} ({ev.text})")
        elif ev.kind == "failed":
            self.log.appendPlainText(f"✗ {name}: {ev.text}")
        if self.b_stop_soft.isEnabled() or self.b_stop_hard.isEnabled():
            self.status.setText(f"Descărcare: {done} / {total} terminate")

    def _dl_finished(self, summary):
        self.set_idle()
        msg = f"Gata: {len(summary.done)} descărcate, {len(summary.failed)} negăsite."
        if summary.cancelled:
            msg = "Oprit. " + msg
        if summary.failed:
            msg += " Negăsitele sunt în subtabul „Negăsite”."
        self.status.setText(msg)
        self.log.appendPlainText("— " + msg + " —")
        self.playlists._update_actions()

    def _dl_error(self, msg):
        self.set_idle()
        self.log.appendPlainText("✗ " + msg)
        QMessageBox.warning(self, "Soulseek", msg)
        self.playlists._update_actions()
