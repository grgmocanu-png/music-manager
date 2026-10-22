"""Tabul „Setări”: Spotify, Soulseek, folderul de download."""
from __future__ import annotations

import json
import os
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..core import firewall
from ..core import soulseek as slsk
from ..core import spotify as sp
from .common import Task, settings


def spotify_client() -> sp.SpotifyClient | None:
    cid = (settings().value("spotify/client_id", "") or "").strip()
    if not cid:
        return None
    try:
        tokens = json.loads(settings().value("spotify/tokens", "") or "{}")
    except ValueError:
        tokens = {}

    def save(tok):
        settings().setValue("spotify/tokens", json.dumps(tok))

    return sp.SpotifyClient(cid, tokens, on_tokens=save)


def download_dir() -> str:
    return settings().value("download/dir", "") or ""


def playlist_subfolders() -> bool:
    v = settings().value("download/subfolders", True)
    return v not in (False, "false", "0", 0)


def slsk_credentials() -> tuple[str, str]:
    return settings().value("slsk/user", "") or "", settings().value("slsk/pass", "") or ""


def slsk_quality() -> str:
    q = settings().value("slsk/quality", "mp3_320") or "mp3_320"
    return q if q in slsk.QUALITY else "mp3_320"


SPOTIFY_HELP = f"""
<b>Cum conectezi Spotify (o singură dată, ~5 minute):</b>
<ol>
<li>Intră pe <a href="https://developer.spotify.com/dashboard">developer.spotify.com/dashboard</a>
și loghează-te cu contul tău de Spotify (cel cu Premium).</li>
<li>Apasă <b>Create app</b>. La <i>App name</i> și <i>Description</i> scrie ce vrei (ex. „Music Manager”).</li>
<li>La <b>Redirect URIs</b> lipește exact: <code>{sp.REDIRECT_URI}</code> și apasă <b>Add</b>
(butonul „Copiază” de mai jos o pune în clipboard).</li>
<li>La <i>Which API/SDKs</i> bifează <b>Web API</b>, acceptă termenii și apasă <b>Save</b>.</li>
<li>Pe pagina aplicației apasă <b>Settings</b>, copiază <b>Client ID</b> și lipește-l mai jos.</li>
<li>Apasă <b>Conectează-te la Spotify</b> și acceptă în browser.</li>
</ol>
"""


class SettingsTab(QWidget):
    spotify_changed = Signal()
    library_changed = Signal()

    def __init__(self, state=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None

        # --- Spotify ---
        help_lbl = QLabel(SPOTIFY_HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        b_copy = QPushButton("Copiază Redirect URI")
        b_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(sp.REDIRECT_URI))
        self.client_id = QLineEdit(settings().value("spotify/client_id", "") or "")
        self.client_id.setPlaceholderText("ex. 3f1b2c… (32 de caractere)")
        self.client_id.editingFinished.connect(self._save_client_id)
        self.sp_status = QLabel()
        self.b_login = QPushButton("Conectează-te la Spotify")
        self.b_login.clicked.connect(self.login)
        self.b_logout = QPushButton("Deconectează")
        self.b_logout.clicked.connect(self.logout)

        sp_box = QGroupBox("Spotify")
        sl = QVBoxLayout(sp_box)
        sl.addWidget(help_lbl)
        sl.addWidget(b_copy, 0, Qt.AlignLeft)
        f = QFormLayout()
        f.addRow("Client ID:", self.client_id)
        sl.addLayout(f)
        r = QHBoxLayout()
        r.addWidget(self.b_login)
        r.addWidget(self.b_logout)
        r.addWidget(self.sp_status, 1)
        sl.addLayout(r)

        # --- Soulseek ---
        user, pw = slsk_credentials()
        self.s_user = QLineEdit(user)
        self.s_pass = QLineEdit(pw)
        self.s_pass.setEchoMode(QLineEdit.Password)
        self.quality = QComboBox()
        for k, label in slsk.QUALITY.items():
            self.quality.addItem(label, k)
        self.quality.setCurrentIndex(list(slsk.QUALITY).index(slsk_quality()))
        self.s_user.editingFinished.connect(lambda: settings().setValue("slsk/user", self.s_user.text().strip()))
        self.s_pass.editingFinished.connect(lambda: settings().setValue("slsk/pass", self.s_pass.text()))
        self.quality.currentIndexChanged.connect(
            lambda: settings().setValue("slsk/quality", self.quality.currentData()))
        self.dl_dir = QLineEdit(download_dir())
        self.dl_dir.setReadOnly(True)
        self.dl_dir.setPlaceholderText("ex. D:\\Muzica\\Mix\\_unsorted")
        b_dl = QPushButton("Alege…")
        b_dl.clicked.connect(self.pick_download_dir)
        self.ss_path = QLineEdit(settings().value("slsk/sockseek_path", "") or "")
        self.ss_path.setPlaceholderText("automat (inclus în aplicație)")
        self.ss_path.editingFinished.connect(
            lambda: (settings().setValue("slsk/sockseek_path", self.ss_path.text().strip()), self._refresh()))
        self.ss_status = QLabel()

        sl_box = QGroupBox("Soulseek")
        fl = QFormLayout(sl_box)
        fl.addRow("Utilizator:", self.s_user)
        fl.addRow("Parolă:", self.s_pass)
        fl.addRow("Calitate:", self.quality)
        drow = QHBoxLayout()
        drow.addWidget(self.dl_dir, 1)
        drow.addWidget(b_dl)
        dw = QWidget()
        dw.setLayout(drow)
        drow.setContentsMargins(0, 0, 0, 0)
        fl.addRow("Folder de download:", dw)
        fl.addRow("", QLabel("Piesele noi ajung aici. Recomandat: un folder „_unsorted” în folderul tău de mix."))
        self.subfolders = QCheckBox("Fiecare playlist în subfolderul lui (ex. _unsorted\\Numele playlistului)")
        self.subfolders.setChecked(playlist_subfolders())
        self.subfolders.toggled.connect(lambda v: settings().setValue("download/subfolders", bool(v)))
        fl.addRow("", self.subfolders)
        fl.addRow("Sockseek (avansat):", self.ss_path)
        fl.addRow("", self.ss_status)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 20)
        self.concurrent.setValue(int(settings().value("slsk/concurrent", 5) or 5))
        self.concurrent.valueChanged.connect(lambda v: settings().setValue("slsk/concurrent", v))
        self.retries = QSpinBox()
        self.retries.setRange(1, 50)
        self.retries.setValue(int(settings().value("slsk/retries", 20) or 20))
        self.retries.valueChanged.connect(lambda v: settings().setValue("slsk/retries", v))
        fl.addRow("Descărcări simultane:", self.concurrent)
        fl.addRow("Încercări per piesă:", self.retries)
        self.fw_status = QLabel()
        self.b_fw = QPushButton("Permite în Windows Firewall")
        self.b_fw.setToolTip("Adaugă o regulă care lasă alți utilizatori Soulseek să se conecteze la tine "
                             "ca să-ți trimită fișiere. Windows îți cere confirmarea.")
        self.b_fw.clicked.connect(self.allow_firewall)
        fw = QHBoxLayout()
        fw.addWidget(self.b_fw)
        fw.addWidget(self.fw_status, 1)
        fww = QWidget()
        fww.setLayout(fw)
        fw.setContentsMargins(0, 0, 0, 0)
        fl.addRow("Firewall:", fww)
        fwhelp = QLabel("Dacă vezi des „toate încercările de descărcare au eșuat”, de obicei Windows blochează "
                        "conexiunile care vin spre tine. Butonul de mai sus le permite.")
        fwhelp.setWordWrap(True)
        fwhelp.setStyleSheet("color: gray;")
        fl.addRow("", fwhelp)
        note = QLabel("Parola se păstrează doar pe acest calculator, în setările aplicației.")
        note.setStyleSheet("color: gray;")
        fl.addRow("", note)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(self._library_box())
        lay.addWidget(sp_box)
        lay.addWidget(sl_box)
        lay.addWidget(self._tags_box())
        lay.addWidget(self._dj_box())
        lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._refresh()

    def _save_client_id(self):
        cid = self.client_id.text().strip()
        if cid != (settings().value("spotify/client_id", "") or ""):
            settings().setValue("spotify/client_id", cid)
            settings().setValue("spotify/tokens", "{}")
            self._refresh()
            self.spotify_changed.emit()

    def _refresh(self):
        client = spotify_client()
        if not client:
            self.sp_status.setText("Completează Client ID.")
            self.b_login.setEnabled(False)
            self.b_logout.setEnabled(False)
        elif client.logged_in:
            self.sp_status.setText("<span style='color:#27ae60'>✓ Conectat</span>")
            self.b_login.setText("Reconectează")
            self.b_login.setEnabled(True)
            self.b_logout.setEnabled(True)
        else:
            self.sp_status.setText("Neconectat.")
            self.b_login.setText("Conectează-te la Spotify")
            self.b_login.setEnabled(True)
            self.b_logout.setEnabled(False)
        st = firewall.rule_exists()
        if hasattr(self, "fw_status"):
            if st is None:
                self.fw_status.setText("(doar pe Windows)")
                self.b_fw.setEnabled(False)
            elif st:
                self.fw_status.setText("<span style='color:#27ae60'>✓ permis</span>")
            else:
                self.fw_status.setText("<span style='color:#e67e22'>nu e permis încă</span>")
        p = slsk.sockseek_path(self.ss_path.text().strip())
        self.ss_status.setText(f"<span style='color:#27ae60'>✓ găsit: {p}</span>" if p else
                               "<span style='color:#c0392b'>✗ nu găsesc Sockseek</span>")

    def login(self):
        self._save_client_id()
        client = spotify_client()
        if not client:
            return
        self.b_login.setEnabled(False)
        self.sp_status.setText("Aștept confirmarea în browser…")

        def work(progress, cancelled):
            client.login(webbrowser.open)
            return True

        self.task = Task(work, self)
        self.task.succeeded.connect(lambda _: (self._refresh(), self.spotify_changed.emit()))
        self.task.failed.connect(self._login_failed)
        self.task.start()

    def _login_failed(self, msg):
        self._refresh()
        QMessageBox.warning(self, "Spotify", f"Conectarea nu a reușit:\n{msg}\n\n"
                            "Verifică dacă Redirect URI din aplicația de dezvoltator e exact "
                            f"{sp.REDIRECT_URI}")

    def logout(self):
        settings().setValue("spotify/tokens", "{}")
        self._refresh()
        self.spotify_changed.emit()

    # ---------- Biblioteca ----------
    def _library_box(self):
        box = QGroupBox("Folderele cu muzica ta (Biblioteca)")
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(90)
        if self.state:
            self.folder_list.addItems(self.state.folders)
        b_add = QPushButton("Adaugă folder…")
        b_rm = QPushButton("Scoate folderul")
        b_add.clicked.connect(self._add_folder)
        b_rm.clicked.connect(self._remove_folder)
        btns = QVBoxLayout()
        btns.addWidget(b_add)
        btns.addWidget(b_rm)
        btns.addStretch()
        row = QHBoxLayout()
        row.addWidget(self.folder_list, 1)
        row.addLayout(btns)
        hint = QLabel("Se scanează cu tot cu subfolderele. Folderul de download e inclus automat. "
                      "Muzica de audiție o lași pe dinafară, nu adaugi folderul ei.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay = QVBoxLayout(box)
        lay.addLayout(row)
        lay.addWidget(hint)
        return box

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Alege un folder cu muzică")
        if not d or not self.state:
            return
        d = os.path.normpath(d)
        if d not in self.state.folders:
            self.state.set_folders(self.state.folders + [d])
            self.folder_list.addItem(d)
            self.library_changed.emit()

    def _remove_folder(self):
        it = self.folder_list.currentItem()
        if not it or not self.state:
            return
        self.state.set_folders([f for f in self.state.folders if f != it.text()])
        self.folder_list.takeItem(self.folder_list.row(it))
        self.library_changed.emit()

    # ---------- Taguri ----------
    def _tags_box(self):
        box = QGroupBox("Taguri online (Discogs) și genuri")
        self.discogs = QLineEdit(settings().value("discogs/token", "") or "")
        self.discogs.setEchoMode(QLineEdit.Password)
        self.discogs.setPlaceholderText("lipește aici tokenul personal Discogs")
        self.discogs.editingFinished.connect(lambda: settings().setValue("discogs/token", self.discogs.text().strip()))
        b_show = QPushButton("Arată")
        b_show.setCheckable(True)
        b_show.toggled.connect(lambda on: self.discogs.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        trow = QHBoxLayout()
        trow.addWidget(self.discogs, 1)
        trow.addWidget(b_show)
        tw = QWidget()
        tw.setLayout(trow)
        trow.setContentsMargins(0, 0, 0, 0)
        how = QLabel('Tokenul îl generezi pe <a href="https://www.discogs.com/settings/developers">'
                     "discogs.com → Settings → Developers</a> → <i>Generate new token</i>. E gratuit; "
                     "aplicația doar citește informații publice despre lansări.")
        how.setWordWrap(True)
        how.setOpenExternalLinks(True)
        self.mb = QCheckBox("Folosește și MusicBrainz (fără cont) când Discogs nu găsește")
        self.mb.setChecked(settings().value("tags/musicbrainz", True) not in (False, "false"))
        self.mb.toggled.connect(lambda v: settings().setValue("tags/musicbrainz", bool(v)))
        from .dialogs import families
        self.fam_label = QLabel(", ".join(families()))
        self.fam_label.setWordWrap(True)
        b_fam = QPushButton("Editează familiile și stilurile…")
        b_fam.clicked.connect(self._edit_genres)
        note = QLabel("Genul scris în fișier = familia (ex. House). Stilul exact (ex. Deep House) merge în Comentariu.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        f = QFormLayout(box)
        f.addRow("Token Discogs:", tw)
        f.addRow("", how)
        f.addRow("", self.mb)
        f.addRow("Familii de gen:", self.fam_label)
        f.addRow("", b_fam)
        f.addRow("", note)
        return box

    def _edit_genres(self):
        from .dialogs import StyleMapDialog, families
        StyleMapDialog(self).exec()
        self.fam_label.setText(", ".join(families()))

    # ---------- Programe DJ ----------
    def _dj_box(self):
        from .library_view import documents_dirs, source_priority
        from ..core import djcollections as djc
        box = QGroupBox("Programe de DJ: colecții, prioritate, export, analiză")
        f = QFormLayout(box)

        tk_auto = djc.find_traktor_collection(documents_dirs())
        self.tk_path = QLineEdit(settings().value("collections/traktor", "") or "")
        self.tk_path.setPlaceholderText(tk_auto or "nu am găsit colecția Traktor automat")
        self.tk_path.editingFinished.connect(lambda: settings().setValue("collections/traktor", self.tk_path.text().strip()))
        f.addRow("Colecția Traktor:", self._with_button(self.tk_path, "Alege…", lambda: self._pick_file(
            self.tk_path, "collections/traktor", "Colecția Traktor (collection.nml)", "NML (*.nml)")))
        self.rb_path = QLineEdit(settings().value("collections/rekordbox_xml", "") or "")
        self.rb_path.setPlaceholderText("exportul XML din Rekordbox (File → Export Collection in xml format)")
        self.rb_path.editingFinished.connect(lambda: settings().setValue("collections/rekordbox_xml", self.rb_path.text().strip()))
        f.addRow("Rekordbox (XML):", self._with_button(self.rb_path, "Alege…", lambda: self._pick_file(
            self.rb_path, "collections/rekordbox_xml", "Exportul XML din Rekordbox", "XML (*.xml)")))
        vdj = djc.find_virtualdj_db(documents_dirs())
        f.addRow("VirtualDJ:", QLabel(vdj or "nu e instalat / nu am găsit baza de date"))
        f.addRow("Serato, Mixed In Key:", QLabel("își scriu datele în fișiere — le vedem automat"))

        self.prio = QListWidget()
        self.prio.addItems(source_priority())
        self.prio.setMaximumHeight(110)
        b_up, b_down = QPushButton("▲"), QPushButton("▼")
        b_up.clicked.connect(lambda: self._move_prio(-1))
        b_down.clicked.connect(lambda: self._move_prio(1))
        pb = QVBoxLayout()
        pb.addWidget(b_up)
        pb.addWidget(b_down)
        pb.addStretch()
        pr = QHBoxLayout()
        pr.addWidget(self.prio, 1)
        pr.addLayout(pb)
        pw = QWidget()
        pw.setLayout(pr)
        pr.setContentsMargins(0, 0, 0, 0)
        f.addRow("Prioritate BPM / tonalitate:", pw)
        f.addRow("", self._gray("Primul din listă câștigă. „Fișier” = ce e scris în tag (Serato, Mixed In Key); "
                                "„Analiză” = analiza făcută de aplicație."))

        self.auto_an = QCheckBox("Analizează automat piesele noi (BPM, tonalitate, waveform)")
        self.auto_an.setChecked(settings().value("library/auto_analyze", True) not in (False, "false"))
        self.auto_an.toggled.connect(lambda v: settings().setValue("library/auto_analyze", bool(v)))
        f.addRow("Analiză:", self.auto_an)
        self.bpm_lo = QSpinBox()
        self.bpm_lo.setRange(60, 110)
        self.bpm_lo.setValue(int(settings().value("analysis/bpm_lo", 85) or 85))
        self.bpm_lo.valueChanged.connect(lambda v: settings().setValue("analysis/bpm_lo", v))
        self.bpm_lo.setSuffix(" BPM")
        f.addRow("BPM-ul minim așteptat:", self.bpm_lo)
        f.addRow("", self._gray("Analiza alege BPM-ul între acest minim și dublul lui (ex. 85–175). "
                                "Ajută la decizia „dublu sau jumătate”."))

        self.auto_rb = QCheckBox("Actualizează automat exportul pentru Rekordbox când schimb un playlist")
        self.auto_rb.setChecked(settings().value("export/auto_rekordbox", False) in (True, "true"))
        self.auto_rb.toggled.connect(lambda v: settings().setValue("export/auto_rekordbox", bool(v)))
        f.addRow("Export:", self.auto_rb)
        return box

    def _gray(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet("color: gray;")
        return l

    def _with_button(self, edit, label, fn):
        b = QPushButton(label)
        b.clicked.connect(fn)
        h = QHBoxLayout()
        h.addWidget(edit, 1)
        h.addWidget(b)
        w = QWidget()
        w.setLayout(h)
        h.setContentsMargins(0, 0, 0, 0)
        return w

    def _pick_file(self, edit, key, title, filt):
        p, _ = QFileDialog.getOpenFileName(self, title, os.path.dirname(edit.text() or edit.placeholderText()), filt)
        if p:
            edit.setText(os.path.normpath(p))
            settings().setValue(key, os.path.normpath(p))
            self.library_changed.emit()

    def _move_prio(self, step):
        r = self.prio.currentRow()
        if r < 0 or not (0 <= r + step < self.prio.count()):
            return
        it = self.prio.takeItem(r)
        self.prio.insertItem(r + step, it)
        self.prio.setCurrentRow(r + step)
        settings().setValue("library/priority", json.dumps([self.prio.item(i).text() for i in range(self.prio.count())]))
        self.library_changed.emit()

    def allow_firewall(self):
        exe = slsk.sockseek_path(settings().value("slsk/sockseek_path", "") or "")
        if not exe:
            QMessageBox.warning(self, "Firewall", "Nu găsesc programul de download.")
            return
        ok = firewall.add_rule(exe)
        self._refresh()
        QMessageBox.information(self, "Firewall", "Gata, regula a fost adăugată." if ok else
                                "Regula nu a fost adăugată (ai apăsat „Nu” la întrebarea Windows?).")

    def pick_download_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Unde descarc piesele noi?", download_dir())
        if d:
            d = os.path.normpath(d)
            settings().setValue("download/dir", d)
            self.dl_dir.setText(d)
