"""Calculatorul rămâne treaz cât descarcă (fără sleep; ecranul se poate stinge) și, la cerere,
se oprește când termină (cu numărătoare de 60 s și „Anulează”)."""
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

_mac_proc = None


def keep_awake(on: bool) -> None:
    global _mac_proc
    try:
        if sys.platform == "win32":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS)
        elif sys.platform == "darwin":
            if on and _mac_proc is None:
                _mac_proc = subprocess.Popen(["caffeinate", "-i"])
            elif not on and _mac_proc is not None:
                _mac_proc.terminate()
                _mac_proc = None
    except Exception:
        pass


def shutdown_now() -> None:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=0x08000000)
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", 'tell app "System Events" to shut down'])


class ShutdownCountdown(QDialog):
    """„Opresc calculatorul în 60 s” cu Anulează. Întoarce Accepted dacă a ajuns la 0."""

    def __init__(self, summary: str, seconds: int = 60, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opresc calculatorul")
        self.left = seconds
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.summary = summary
        b_now = QPushButton("Oprește acum")
        b_cancel = QPushButton("Anulează")
        b_cancel.setObjectName("primary")
        b_now.clicked.connect(self.accept)
        b_cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(b_now)
        row.addWidget(b_cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._show()

    def _show(self):
        self.text.setText(f"{self.summary}\n\nAm terminat. Opresc calculatorul în <b>{self.left}</b> secunde."
                          .replace("\n", "<br>"))

    def _tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
        else:
            self._show()
