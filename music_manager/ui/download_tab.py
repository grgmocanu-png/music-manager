"""Tabul „Download”: playlisturi Spotify → ce lipsește → descărcare de pe Soulseek."""
from __future__ import annotations

import json
import os
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..core import library as lib
from ..core import soulseek as slsk
from ..core import spotify as sp
from ..core.matching import Matcher, search_query
from ..core.sources import PlaylistStore, SavedPlaylist
from .common import AppState, Task, data_dir, fmt_duration, open_file, reveal_in_folder, settings
from .settings_tab import download_dir, slsk_credentials, slsk_quality, spotify_client

# stările unei piese
HAVE, MISSING, IGNORED, NOTFOUND, QUEUED, SEARCHING, DOWNLOADING, DONE, FAILED = (
    "have", "missing", "ignored", "notfound", "queued", "searching", "downloading", "done", "failed")
STATUS_LABEL = {
    HAVE: ("✓ Ai deja", "#27ae60"),
    MISSING: ("Lipsește", "#e67e22"),
    IGNORED: ("Ignorată", "gray"),
    NOTFOUND: ("Lipsește (negăsită data trecută)", "#c0392b"),
    QUEUED: ("În așteptare…", "#2980b9"),
    SEARCHING: ("Caut pe Soulseek…", "#2980b9"),
    DOWNLOADING: ("Se descarcă…", "#2980b9"),
    DONE: ("⬇ Descărcată", "#27ae60"),
    FAILED: ("✗ Negăsită", "#c0392b"),
}
DOWNLOADABLE = (MISSING, NOTFOUND, FAILED)
FILTERS = {
    "Doar ce lipsește": (MISSING, NOTFOUND, FAILED, QUEUED, SEARCHING, DOWNLOADING),
    "Toate piesele": None,
    "Ce am deja": (HAVE, DONE),
    "Descărcate acum": (DONE,),
    "Negăsite": (NOTFOUND, FAILED),
    "Ignorate": (IGNORED,),
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


class PlaylistButton(QFrame):
    clicked = Signal(str)
    action = Signal(str, str)   # (acțiune, id)

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


class DownloadTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None
        self.store = PlaylistStore(os.path.join(data_dir(), "playlisturi.json"))
        self.current: SavedPlaylist | None = None
        self.rows: list[Row] = []
        self._filling = False

        # --- stânga: playlisturi salvate ---
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

        # --- dreapta: piesele playlistului ---
        self.title = QLabel("<i>Alege un playlist din stânga.</i>")
        self.title.setTextFormat(Qt.RichText)
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

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "Artist", "Titlu", "Durată", "Stare",
                                              "Caută artist", "Caută titlu", "Fișier / detalii"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in ((C_CHECK, 28), (C_ARTIST, 170), (C_TITLE, 230), (C_DUR, 55), (C_STATUS, 170),
                     (C_QA, 140), (C_QT, 180)):
            self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)

        self.b_download = QPushButton("Descarcă bifatele")
        self.b_download.setMinimumHeight(34)
        self.b_download.clicked.connect(self.download_or_stop)
        b_all = QPushButton("Bifează tot ce lipsește")
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

        hint = QLabel("Sfat: dublu-click pe „Caută artist / titlu” ca să schimbi textul căutat pe Soulseek "
                      "(util pentru piesele negăsite). Dublu-click pe o piesă pe care o ai o pornește.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(130)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(head)
        rl.addWidget(self.table, 1)
        rl.addWidget(hint)
        rl.addLayout(actions)
        rl.addWidget(self.progress)
        rl.addWidget(self.status)
        rl.addWidget(self.log)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([280, 1000])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

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
                         "(Share → Copy link to playlist).")
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
                    self.current = None
                    self.rows = []
                    self.fill_table()
                self.refresh_buttons()

    def _need_spotify(self) -> sp.SpotifyClient | None:
        client = spotify_client()
        if client and client.logged_in:
            return client
        QMessageBox.information(self, "Spotify", "Mai întâi conectează Spotify din tabul Setări.")
        return None

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
    def _busy(self) -> bool:
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "Ocupat", "Aștept să se termine operația în curs.")
            return True
        return False

    def check(self, pid: str):
        if self._busy():
            self.refresh_buttons()
            return
        client = self._need_spotify()
        if not client:
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
                os.path.normcase(os.path.abspath(dl)).startswith(os.path.normcase(os.path.abspath(f)) + os.sep)
                or os.path.normcase(dl) == os.path.normcase(f) for f in folders):
            folders.append(dl)

        def work(progress, cancelled):
            progress(0, 0, "Citesc playlistul din Spotify…")
            pl = client.get_playlist(pid, lambda d, t: progress(d, t, f"Citesc playlistul: {d}/{t}"))
            progress(0, 0, "Compar cu muzica ta…")
            cache = lib.LibraryCache(os.path.join(data_dir(), "biblioteca.json"))
            local = lib.scan(folders, cache, lambda d, t: progress(d, t, f"Verific fișierele: {d}/{t}"),
                             cancelled) if folders else []
            matcher = Matcher(local)
            found = {t.id: matcher.find(t) for t in pl.tracks}
            return pl, found

        self.task = Task(work, self)
        self.task.progress.connect(self._on_progress)
        self.task.succeeded.connect(self._on_checked)
        self.task.failed.connect(self._on_failed)
        self._start_busy(f"Verific „{saved.name}”…")
        self.task.start()

    def _on_checked(self, result):
        pl, found = result
        saved = self.store.get(pl.id) or self.current
        saved.name = pl.name
        rows = []
        seen = set()
        for t in pl.tracks:
            if t.id in seen:       # aceeași piesă de două ori în playlist
                continue
            seen.add(t.id)
            local = found.get(t.id)
            aq, tq = saved.custom_queries.get(t.id) or search_query(t)
            if local:
                r = Row(t, HAVE, local.path, aq, tq, local.path)
            elif t.id in saved.ignored:
                r = Row(t, IGNORED, "", aq, tq)
            elif t.id in saved.not_found:
                r = Row(t, NOTFOUND, "", aq, tq, saved.not_found[t.id])
            else:
                r = Row(t, MISSING, "", aq, tq)
            rows.append(r)
        self.rows = rows
        missing = sum(1 for r in rows if r.status in (MISSING, NOTFOUND))
        saved.last_update = datetime.now().isoformat(timespec="minutes")
        saved.last_total = len(rows)
        saved.last_missing = missing
        saved.last_downloaded = 0
        self.store.save()
        self.current = saved
        self._end_busy()
        self.refresh_buttons()
        extra = f" · {pl.skipped} sărite (fișiere locale/indisponibile)" if pl.skipped else ""
        self.status.setText(f"<b>{len(rows)}</b> piese în playlist · <b>{len(rows) - missing - sum(1 for r in rows if r.status == IGNORED)}</b> "
                            f"le ai deja · <b style='color:#e67e22'>{missing}</b> lipsesc{extra}")
        self.fill_table(check_missing=True)

    # --- tabel ---
    def _cell(self, text, editable=False, color=None, tooltip=None):
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

    def fill_table(self, check_missing=False):
        self._filling = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            chk = QTableWidgetItem()
            if r.status in DOWNLOADABLE:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if check_missing:
                    chk.setCheckState(Qt.Checked if r.status == MISSING else Qt.Unchecked)
                else:
                    prev = self.table.item(i, C_CHECK)
                    chk.setCheckState(prev.checkState() if prev and prev.flags() & Qt.ItemIsUserCheckable
                                      else Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(i, C_CHECK, chk)
            self._fill_row(i)
        self.table.setUpdatesEnabled(True)
        self._filling = False
        if self.current:
            self.title.setText(f"<b style='font-size:14px'>{self.current.name}</b>")
        self.apply_filter()
        self._update_actions()

    def _fill_row(self, i):
        r = self.rows[i]
        self._filling = True
        label, color = STATUS_LABEL[r.status]
        self.table.setItem(i, C_ARTIST, self._cell(r.track.artist))
        self.table.setItem(i, C_TITLE, self._cell(r.track.title, tooltip=r.track.album))
        self.table.setItem(i, C_DUR, self._cell(fmt_duration(r.track.duration)))
        self.table.setItem(i, C_STATUS, self._cell(label, color=color))
        editable = r.status in DOWNLOADABLE
        self.table.setItem(i, C_QA, self._cell(r.artist_q, editable, tooltip="Dublu-click pentru a modifica"))
        self.table.setItem(i, C_QT, self._cell(r.title_q, editable, tooltip="Dublu-click pentru a modifica"))
        detail = os.path.basename(r.path) if r.path else r.detail
        self.table.setItem(i, C_FILE, self._cell(detail, tooltip=r.path or r.detail))
        if r.status not in DOWNLOADABLE:
            chk = self.table.item(i, C_CHECK)
            if chk and chk.flags() & Qt.ItemIsUserCheckable:
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setData(Qt.CheckStateRole, None)
        self._filling = False

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
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for i in rows:
            r = self.rows[i]
            if ignore and r.status in DOWNLOADABLE:
                r.status = IGNORED
                if r.track.id not in self.current.ignored:
                    self.current.ignored.append(r.track.id)
            elif not ignore and r.status == IGNORED:
                r.status = MISSING
                if r.track.id in self.current.ignored:
                    self.current.ignored.remove(r.track.id)
        self.store.save()
        self.fill_table()

    def _update_actions(self, force=False):
        if not force and self.task and self.task.isRunning():
            return
        n = len(self._checked_rows())
        self.b_download.setText(f"Descarcă bifatele ({n})")
        self.b_download.setEnabled(n > 0)
        self.b_check.setEnabled(self.current is not None)

    # --- descărcare ---
    def download_or_stop(self):
        if self.task and self.task.isRunning():
            self.task.cancel()
            self.b_download.setEnabled(False)
            self.b_download.setText("Se oprește…")
            return
        self.download()

    def download(self):
        idx = self._checked_rows()
        if not idx or not self.current:
            return
        out = download_dir()
        if not out:
            QMessageBox.information(self, "Download", "Alege mai întâi folderul de download în Setări.")
            return
        user, pw = slsk_credentials()
        if not user or not pw:
            QMessageBox.information(self, "Soulseek", "Completează utilizatorul și parola de Soulseek în Setări.")
            return
        exe = slsk.sockseek_path(settings().value("slsk/sockseek_path", "") or "")
        if not exe:
            QMessageBox.warning(self, "Soulseek", "Nu găsesc Sockseek (programul care descarcă). "
                                "Verifică în Setări.")
            return
        items = [slsk.DownloadItem(self.rows[i].track, self.rows[i].artist_q, self.rows[i].title_q) for i in idx]
        for i in idx:
            self.rows[i].status = QUEUED
            self._fill_row(i)
        quality = slsk_quality()
        work_dir = os.path.join(data_dir(), "soulseek")

        def work(progress, cancelled):
            done = [0]

            def on_event(ev):
                if ev.kind in ("done", "failed"):
                    done[0] += 1
                progress(done[0], len(items), json.dumps(asdict(ev)))
            return slsk.run_download(items, out, user, pw, quality, exe, work_dir, on_event, cancelled)

        self.task = Task(work, self)
        self.task.progress.connect(lambda d, t, txt: self._on_download_event(d, t, txt, idx))
        self.task.succeeded.connect(lambda s: self._on_download_done(s, idx))
        self.task.failed.connect(lambda m: self._on_download_failed(m, idx))
        self._start_busy(f"Descarc {len(items)} piese de pe Soulseek…", stop_button=True)
        self.progress.setMaximum(len(items))
        self.log.appendPlainText(f"— Pornesc descărcarea a {len(items)} piese în {out} —")
        self.task.start()

    def _on_download_event(self, done, total, payload, idx):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        try:
            ev = slsk.DownloadEvent(**json.loads(payload))
        except (ValueError, TypeError):
            return
        if ev.kind == "log":
            if ev.text.strip():
                self.log.appendPlainText(ev.text.strip())
            return
        if not (0 <= ev.index < len(idx)):
            return
        row_i = idx[ev.index]
        r = self.rows[row_i]
        if ev.kind == "searching":
            r.status, r.detail = SEARCHING, ""
        elif ev.kind == "downloading":
            r.status, r.detail = DOWNLOADING, ev.text
        elif ev.kind == "progress":
            r.status = DOWNLOADING
            r.detail = f"{ev.percent:.0f}%"
        elif ev.kind == "done":
            r.status, r.path, r.detail = DONE, ev.path, ev.text
            self.log.appendPlainText(f"✓ {r.track.label} → {os.path.basename(ev.path)} ({ev.text})")
        elif ev.kind == "failed":
            r.status, r.detail = FAILED, ev.text
            self.log.appendPlainText(f"✗ {r.track.label}: {ev.text}")
        self._fill_row(row_i)
        self.status.setText(f"Descărcare: {done} / {total} terminate")

    def _finish_rows(self, idx):
        for i in idx:
            r = self.rows[i]
            if r.status in (QUEUED, SEARCHING, DOWNLOADING):
                r.status = MISSING
                r.detail = ""
                self._fill_row(i)

    def _on_download_done(self, summary, idx):
        self._finish_rows(idx)
        cur = self.current
        if cur:
            for i in idx:
                r = self.rows[i]
                if r.status == DONE:
                    cur.not_found.pop(r.track.id, None)
                elif r.status == FAILED:
                    cur.not_found[r.track.id] = r.detail
            cur.last_downloaded += len(summary.done)
            cur.last_missing = sum(1 for r in self.rows if r.status in (MISSING, NOTFOUND, FAILED))
            self.store.save()
        self._end_busy()
        self.refresh_buttons()
        msg = f"Gata: {len(summary.done)} descărcate, {len(summary.failed)} negăsite."
        if summary.cancelled:
            msg = "Oprit. " + msg
        self.status.setText(msg)
        self.log.appendPlainText("— " + msg + " —")
        self.fill_table()

    def _on_download_failed(self, msg, idx):
        self._finish_rows(idx)
        self._end_busy()
        self.log.appendPlainText("✗ " + msg)
        QMessageBox.warning(self, "Soulseek", msg)
        self.fill_table()

    # --- stare ocupat ---
    def _start_busy(self, text, stop_button=False):
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.status.setText(text)
        self.b_check.setEnabled(False)
        if stop_button:
            self.b_download.setText("Oprește")
            self.b_download.setEnabled(True)
        else:
            self.b_download.setEnabled(False)

    def _end_busy(self):
        self.progress.setVisible(False)
        self.b_download.setEnabled(True)
        self._update_actions(force=True)

    def _on_progress(self, done, total, text):
        self.progress.setMaximum(total if total else 0)
        self.progress.setValue(done)
        if text:
            self.status.setText(text)

    def _on_failed(self, msg):
        self._end_busy()
        self.refresh_buttons()
        self.status.setText("")
        QMessageBox.warning(self, "Eroare", msg)
