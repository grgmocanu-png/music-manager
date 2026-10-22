"""Tabul „Setări”: Spotify, Soulseek, folderul de download."""
from __future__ import annotations

import json
import os
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

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

    def __init__(self, parent=None):
        super().__init__(parent)
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
        fl.addRow("Sockseek (avansat):", self.ss_path)
        fl.addRow("", self.ss_status)
        note = QLabel("Parola se păstrează doar pe acest calculator, în setările aplicației.")
        note.setStyleSheet("color: gray;")
        fl.addRow("", note)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(sp_box)
        lay.addWidget(sl_box)
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

    def pick_download_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Unde descarc piesele noi?", download_dir())
        if d:
            d = os.path.normpath(d)
            settings().setValue("download/dir", d)
            self.dl_dir.setText(d)
