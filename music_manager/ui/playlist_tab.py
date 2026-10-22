"""Tabul „Playlisturi”: filtrare, construire, ordonare, salvare M3U, copiere pe stick."""
from __future__ import annotations

import os
import random
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..core import playlist as pl
from ..core.library import Track, unique_path
from .common import AppState, Task, TrackTable, fmt_duration, settings, open_file

FORMATS = {
    "Toate": "", "Doar fără pierderi (FLAC/WAV/AIFF)": "lossless",
    "Doar comprimate (MP3/AAC…)": "lossy", "MP3": "mp3", "FLAC": "flac",
    "WAV": "wav", "AIFF": "aiff", "M4A": "m4a",
}
TRACK_ROLE = Qt.UserRole + 10


def _key_order() -> list[str]:
    return [f"{n}{l}" for n in range(1, 13) for l in "AB"]


def harmonic_order(tracks: list[Track]) -> list[Track]:
    """Ordonează piesele ca să curgă: tonalități compatibile și BPM apropiat.

    Algoritm simplu (lacom): pornește de la cea mai lentă piesă și alege mereu
    următoarea cea mai potrivită.
    """
    if len(tracks) < 3:
        return list(tracks)

    def bpm(t):
        return int(t.bpm) if t.bpm.isdigit() else 0

    rest = sorted(tracks, key=lambda t: (bpm(t) or 999, t.display_title.lower()))
    out = [rest.pop(0)]
    while rest:
        cur = out[-1]
        ck = pl.to_camelot(cur.key)
        compat = pl.compatible_keys(ck)

        def cost(t):
            c = 0.0
            tk = pl.to_camelot(t.key)
            if ck and tk:
                c += 0 if tk == ck else (1 if tk in compat else 6)
            else:
                c += 3
            b1, b2 = bpm(cur), bpm(t)
            if b1 and b2:
                diff = abs(b2 - b1)
                c += diff / 2 + (2 if b2 < b1 else 0)   # preferăm să urcăm în tempo
            else:
                c += 3
            return c

        best = min(rest, key=cost)
        rest.remove(best)
        out.append(best)
    return out


class PlaylistTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None
        self.current_file: str | None = None
        state.tracks_changed.connect(self.on_library_changed)
        state.send_to_playlist.connect(self.receive)

        # --- filtre ---
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("artist, titlu, album…")
        self.f_text.setClearButtonEnabled(True)
        self.f_genre = QComboBox()
        self.f_genre.setEditable(True)
        self.f_genre.lineEdit().setPlaceholderText("orice gen")
        self.f_year_min, self.f_year_max = QSpinBox(), QSpinBox()
        for s in (self.f_year_min, self.f_year_max):
            s.setRange(0, 2100)
            s.setSpecialValueText("—")
        self.f_bpm_min, self.f_bpm_max = QSpinBox(), QSpinBox()
        for s in (self.f_bpm_min, self.f_bpm_max):
            s.setRange(0, 250)
            s.setSpecialValueText("—")
        self.f_key = QComboBox()
        self.f_key.addItem("Oricare", "")
        for k in _key_order():
            self.f_key.addItem(f"{k}  ({pl.CAMELOT[k]})", k)
        self.f_harmonic = QCheckBox("și tonalitățile compatibile (mixaj armonic)")
        self.f_format = QComboBox()
        self.f_format.addItems(FORMATS.keys())
        b_reset = QPushButton("Resetează filtrele")
        b_reset.clicked.connect(self.reset_filters)

        def pair(a, b):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(a)
            h.addWidget(QLabel("până la"))
            h.addWidget(b)
            return w

        fbox = QGroupBox("Filtre")
        form = QFormLayout(fbox)
        form.addRow("Caută:", self.f_text)
        form.addRow("Gen:", self.f_genre)
        form.addRow("An de la:", pair(self.f_year_min, self.f_year_max))
        form.addRow("BPM de la:", pair(self.f_bpm_min, self.f_bpm_max))
        form.addRow("Tonalitate:", self.f_key)
        form.addRow("", self.f_harmonic)
        form.addRow("Format:", self.f_format)
        form.addRow("", b_reset)

        for w in (self.f_text,):
            w.textChanged.connect(self.apply_filters)
        self.f_genre.currentTextChanged.connect(self.apply_filters)
        for s in (self.f_year_min, self.f_year_max, self.f_bpm_min, self.f_bpm_max):
            s.valueChanged.connect(self.apply_filters)
        self.f_key.currentIndexChanged.connect(self.apply_filters)
        self.f_harmonic.toggled.connect(self.apply_filters)
        self.f_format.currentIndexChanged.connect(self.apply_filters)

        self.results = TrackTable(self, hidden_columns=("album", "folder"))
        self.results.double_click_handler = lambda t: self.add_tracks([t])
        self.res_label = QLabel()
        b_add = QPushButton("Adaugă selecția →")
        b_add_all = QPushButton("Adaugă toate rezultatele →")
        b_add.clicked.connect(self.add_selected)
        b_add_all.clicked.connect(lambda: self.add_tracks(self.results.visible_tracks()))
        rb = QHBoxLayout()
        rb.addWidget(self.res_label)
        rb.addStretch()
        rb.addWidget(b_add_all)
        rb.addWidget(b_add)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(fbox)
        ll.addWidget(self.results, 1)
        ll.addLayout(rb)

        # --- playlist ---
        self.name = QLineEdit()
        self.name.setPlaceholderText("Numele playlistului")
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setAlternatingRowColors(True)
        self.list.model().rowsMoved.connect(lambda *a: self._update_info())
        self.list.itemDoubleClicked.connect(lambda it: open_file(it.data(TRACK_ROLE).path))
        self.info = QLabel()

        b_up, b_down = QPushButton("▲ Sus"), QPushButton("▼ Jos")
        b_del, b_clear = QPushButton("Scoate"), QPushButton("Golește")
        b_up.clicked.connect(lambda: self.move_selected(-1))
        b_down.clicked.connect(lambda: self.move_selected(1))
        b_del.clicked.connect(self.remove_selected)
        b_clear.clicked.connect(self.clear)
        b_bpm = QPushButton("Ordonează după BPM")
        b_harm = QPushButton("Ordonează pentru mix (BPM + tonalitate)")
        b_shuffle = QPushButton("Amestecă")
        b_bpm.clicked.connect(self.sort_bpm)
        b_harm.clicked.connect(self.sort_harmonic)
        b_shuffle.clicked.connect(self.shuffle)

        b_open = QPushButton("Deschide M3U…")
        b_save = QPushButton("Salvează M3U…")
        b_export = QPushButton("Copiază piesele într-un folder / pe stick…")
        b_open.clicked.connect(self.open_m3u)
        b_save.clicked.connect(self.save_m3u)
        b_export.clicked.connect(self.export_folder)
        b_save.setDefault(True)

        edit_row = QHBoxLayout()
        for b in (b_up, b_down, b_del, b_clear):
            edit_row.addWidget(b)
        sort_row = QHBoxLayout()
        for b in (b_bpm, b_harm, b_shuffle):
            sort_row.addWidget(b)
        file_row = QHBoxLayout()
        file_row.addWidget(b_open)
        file_row.addWidget(b_save)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Playlistul tău</b>  (trage piesele ca să le muți)"))
        rl.addWidget(self.name)
        rl.addWidget(self.list, 1)
        rl.addWidget(self.info)
        rl.addLayout(edit_row)
        rl.addLayout(sort_row)
        rl.addLayout(file_row)
        rl.addWidget(b_export)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self._update_info()
        self.on_library_changed()

    # --- bibliotecă / filtre ---
    def on_library_changed(self):
        cur = self.f_genre.currentText()
        self.f_genre.blockSignals(True)
        self.f_genre.clear()
        self.f_genre.addItem("")
        self.f_genre.addItems(pl.genres_of(self.state.tracks))
        self.f_genre.setCurrentText(cur)
        self.f_genre.blockSignals(False)
        self.apply_filters()

    def current_filter(self) -> pl.Filter:
        return pl.Filter(
            text=self.f_text.text(), genre=self.f_genre.currentText().strip(),
            year_min=self.f_year_min.value(), year_max=self.f_year_max.value(),
            bpm_min=self.f_bpm_min.value(), bpm_max=self.f_bpm_max.value(),
            key=self.f_key.currentData() or "", harmonic=self.f_harmonic.isChecked(),
            format=FORMATS[self.f_format.currentText()],
        )

    def apply_filters(self):
        res = pl.apply_filter(self.state.tracks, self.current_filter())
        self.results.set_tracks(res)
        self.res_label.setText(f"{len(res)} piese găsite din {len(self.state.tracks)}")

    def reset_filters(self):
        self.f_text.clear()
        self.f_genre.setCurrentText("")
        for s in (self.f_year_min, self.f_year_max, self.f_bpm_min, self.f_bpm_max):
            s.setValue(0)
        self.f_key.setCurrentIndex(0)
        self.f_harmonic.setChecked(False)
        self.f_format.setCurrentIndex(0)

    # --- playlist ---
    def receive(self, tracks: list[Track]):
        self.add_tracks(tracks)
        tabs = getattr(self.window(), "tabs", None)
        if tabs:
            tabs.setCurrentWidget(self)

    def playlist_tracks(self) -> list[Track]:
        return [self.list.item(i).data(TRACK_ROLE) for i in range(self.list.count())]

    def _make_item(self, t: Track) -> QListWidgetItem:
        bits = []
        if t.bpm:
            bits.append(f"{t.bpm} BPM")
        cam = pl.to_camelot(t.key)
        if cam or t.key:
            bits.append(cam or t.key)
        bits.append(fmt_duration(t.duration))
        name = f"{t.artist} – {t.display_title}" if t.artist else t.display_title
        missing = not os.path.exists(t.path)
        it = QListWidgetItem(("⚠ " if missing else "") + name + "    ·  " + "  ·  ".join(bits))
        it.setData(TRACK_ROLE, t)
        it.setToolTip(t.path + ("\n(fișierul nu mai există)" if missing else ""))
        return it

    def add_tracks(self, tracks: list[Track]):
        have = {t.path for t in self.playlist_tracks()}
        skipped = 0
        for t in tracks:
            if t.path in have:
                skipped += 1
                continue
            have.add(t.path)
            self.list.addItem(self._make_item(t))
        self._update_info()
        if skipped and len(tracks) == skipped:
            self.info.setText(self.info.text() + "  (piesele erau deja în playlist)")

    def add_selected(self):
        self.add_tracks(self.results.selected_tracks())

    def set_tracks(self, tracks: list[Track]):
        self.list.clear()
        for t in tracks:
            self.list.addItem(self._make_item(t))
        self._update_info()

    def remove_selected(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._update_info()

    def clear(self):
        if self.list.count() and QMessageBox.question(self, "Golește", "Golesc playlistul?") != QMessageBox.Yes:
            return
        self.list.clear()
        self.current_file = None
        self.name.clear()
        self._update_info()

    def move_selected(self, delta: int):
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        if not rows:
            return
        if delta < 0 and rows[0] == 0 or delta > 0 and rows[-1] == self.list.count() - 1:
            return
        order = rows if delta < 0 else list(reversed(rows))
        for r in order:
            it = self.list.takeItem(r)
            self.list.insertItem(r + delta, it)
        self.list.clearSelection()
        for r in rows:
            self.list.item(r + delta).setSelected(True)
        self._update_info()

    def sort_bpm(self):
        self.set_tracks(sorted(self.playlist_tracks(),
                               key=lambda t: int(t.bpm) if t.bpm.isdigit() else 999))

    def sort_harmonic(self):
        tracks = self.playlist_tracks()
        no_data = sum(1 for t in tracks if not (t.bpm and pl.to_camelot(t.key)))
        self.set_tracks(harmonic_order(tracks))
        if no_data:
            QMessageBox.information(
                self, "Ordonare pentru mix",
                f"{no_data} piese nu au BPM sau tonalitate în taguri, așa că au fost "
                "așezate aproximativ. Programe ca Rekordbox, Mixed In Key sau KeyFinder "
                "pot scrie aceste taguri.")

    def shuffle(self):
        t = self.playlist_tracks()
        random.shuffle(t)
        self.set_tracks(t)

    def _update_info(self):
        t = self.playlist_tracks()
        dur = sum(x.duration for x in t)
        missing = sum(1 for x in t if not os.path.exists(x.path))
        txt = f"{len(t)} piese · {fmt_duration(dur)}"
        if missing:
            txt += f" · <span style='color:#c0392b'>{missing} fișiere lipsă</span>"
        self.info.setText(txt)

    # --- fișiere ---
    def _find_track(self, path: str) -> Track:
        for t in self.state.tracks:
            if os.path.normcase(t.path) == os.path.normcase(path):
                return t
        if os.path.exists(path):
            from ..core.library import track_from_file
            return track_from_file(path)
        return Track(path=path, error="lipsă")

    def open_m3u(self):
        start = settings().value("playlist/dir", "") or ""
        path, _ = QFileDialog.getOpenFileName(self, "Deschide playlist", start, "Playlisturi (*.m3u *.m3u8)")
        if not path:
            return
        settings().setValue("playlist/dir", os.path.dirname(path))
        paths = pl.read_m3u(path)
        self.set_tracks([self._find_track(p) for p in paths])
        self.current_file = path
        self.name.setText(os.path.splitext(os.path.basename(path))[0])

    def save_m3u(self):
        tracks = self.playlist_tracks()
        if not tracks:
            QMessageBox.information(self, "Playlist", "Playlistul e gol.")
            return
        name = self.name.text().strip() or "Playlist"
        start_dir = os.path.dirname(self.current_file) if self.current_file else (settings().value("playlist/dir", "") or "")
        suggested = os.path.join(start_dir, name + ".m3u8")
        path, _ = QFileDialog.getSaveFileName(self, "Salvează playlist", suggested, "Playlist M3U8 (*.m3u8);;Playlist M3U (*.m3u)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".m3u8"
        settings().setValue("playlist/dir", os.path.dirname(path))
        pl.write_m3u(path, tracks, relative=True)
        self.current_file = path
        QMessageBox.information(self, "Salvat",
                                f"Playlist salvat:\n{path}\n\nÎl poți deschide în Rekordbox, Serato, "
                                "VLC, foobar2000, Music (Apple) etc.")

    def export_folder(self):
        tracks = [t for t in self.playlist_tracks() if os.path.exists(t.path)]
        if not tracks:
            QMessageBox.information(self, "Copiere", "Nu am ce copia.")
            return
        d = QFileDialog.getExistingDirectory(self, "Alege folderul sau stick-ul")
        if not d:
            return
        name = self.name.text().strip() or "Playlist"
        dest_dir = os.path.join(d, name)
        numbered = QMessageBox.question(
            self, "Numerotare", "Pun numere în fața fișierelor, ca ordinea să se păstreze "
            "și pe playere care sortează alfabetic (ex. „01 Artist - Titlu”)?") == QMessageBox.Yes

        def work(progress, cancelled):
            os.makedirs(dest_dir, exist_ok=True)
            copied, errors = [], []
            width = max(2, len(str(len(tracks))))
            for i, t in enumerate(tracks, 1):
                if cancelled():
                    break
                fname = os.path.basename(t.path)
                if numbered:
                    fname = f"{i:0{width}} {fname}"
                dest = unique_path(os.path.join(dest_dir, fname))
                try:
                    shutil.copy2(t.path, dest)
                    nt = Track(**{**t.__dict__, "path": dest})
                    copied.append(nt)
                except OSError as e:
                    errors.append(f"{fname}: {e.strerror or e}")
                progress(i, len(tracks), fname)
            pl.write_m3u(os.path.join(dest_dir, name + ".m3u8"), copied, relative=True)
            return copied, errors

        self.task = Task(work, self)
        self.task.succeeded.connect(lambda res: self._export_done(res, dest_dir))
        self.task.failed.connect(lambda m: QMessageBox.warning(self, "Eroare", m))
        self.info.setText("Copiez…")
        self.task.progress.connect(lambda d_, t_, _: self.info.setText(f"Copiez {d_}/{t_}…"))
        self.task.start()

    def _export_done(self, res, dest_dir):
        copied, errors = res
        self._update_info()
        msg = f"Am copiat {len(copied)} piese și playlistul în:\n{dest_dir}"
        if errors:
            box = QMessageBox(QMessageBox.Warning, "Copiere", msg + f"\n{len(errors)} erori (poate s-a umplut stick-ul?).", parent=self)
            box.setDetailedText("\n".join(errors))
            box.exec()
        else:
            QMessageBox.information(self, "Copiere", msg)
